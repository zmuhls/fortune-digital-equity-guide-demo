"""Privacy-bounded PostgreSQL persistence for guide conversations.

The public guide remains query-log free unless an operator explicitly selects
``metadata`` or ``transcript`` capture.  This module deliberately has no import-
time dependency on psycopg so the key-free local tests keep working without a
database installation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from prompt_policy import PROMPT_POLICY_VERSION


CAPTURE_MODES = {"none", "metadata", "transcript"}
HUMAN_REVIEW_SURFACES = frozenset({"replica", "wix"})
SCHEMA_VERSION = "012_shared_prompt_and_review_history"


class CaptureUnavailable(RuntimeError):
    """Raised when required capture cannot be completed safely."""


class IdempotencyConflict(RuntimeError):
    """Raised when one client event ID is reused for different input."""


class ConversationLimit(RuntimeError):
    """Raised when one conversation reaches its configured turn bound."""


def capture_mode(value: str | None) -> str:
    normalized = str(value or "none").strip().lower()
    if normalized not in CAPTURE_MODES:
        raise ValueError(
            "FORTUNE_CONVERSATION_CAPTURE must be none, metadata, or transcript"
        )
    return normalized


def canonical_uuid(value: Any = None) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return str(uuid.uuid4())


def valid_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def sanitized_surface(value: Any) -> str:
    surface = str(value or "unknown").strip().lower()
    allowed = {"replica", "wix", "api", "synthetic", "benchmark"}
    return surface if surface in allowed else "unknown"


def sanitized_automation_source(value: Any) -> str:
    """Retain only a short, non-identifying automation label."""

    source = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower())
    return source.strip("-.")[:80]


def sanitized_evaluator_slot(value: Any) -> str | None:
    slot = str(value or "").strip()
    return slot if slot in {"admin", "editor-1", "editor-2", "editor-3"} else None


def automation_provenance(client_surface: Any, automation_source: Any = None) -> tuple[bool, str]:
    """Mark known automation without inferring identity from transcript text."""

    surface = sanitized_surface(client_surface)
    source = sanitized_automation_source(automation_source)
    if surface in {"benchmark", "synthetic"}:
        return True, source or surface
    if source:
        return True, source
    return False, ""


def fingerprint_request(
    secret: str,
    *,
    question: str,
    page_context: dict | None,
    client_surface: Any,
    history_context: list[dict] | None,
    automation_source: Any = None,
) -> str:
    """Bind idempotency to every input that can affect the model response."""

    return hmac.new(
        secret.encode("utf-8"),
        json.dumps(
            {
                "message": str(question),
                "page_context": page_context or {},
                "client_surface": sanitized_surface(client_surface),
                "automation_source": sanitized_automation_source(automation_source),
                "history": history_context or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class TurnReservation:
    conversation_id: str
    turn_id: str
    client_event_id: str
    user_message_id: str
    assistant_message_id: str
    lease_id: str
    capture_mode: str
    client_surface: str = "unknown"
    is_automated: bool = False
    automation_source: str = ""
    persisted: bool = False
    duplicate_response: dict | None = None
    in_progress: bool = False


def new_reservation(
    conversation_id: Any = None,
    client_event_id: Any = None,
    mode: str = "none",
    client_surface: Any = None,
    automation_source: Any = None,
) -> TurnReservation:
    is_automated, source = automation_provenance(client_surface, automation_source)
    return TurnReservation(
        conversation_id=canonical_uuid(conversation_id),
        turn_id=canonical_uuid(),
        client_event_id=canonical_uuid(client_event_id),
        user_message_id=canonical_uuid(),
        assistant_message_id=canonical_uuid(),
        lease_id=canonical_uuid(),
        capture_mode=capture_mode(mode),
        client_surface=sanitized_surface(client_surface),
        is_automated=is_automated,
        automation_source=source,
    )


def response_with_ids(
    response: dict,
    reservation: TurnReservation,
    *,
    mode: str,
    stored: bool,
    conversation_token: str = "",
) -> dict:
    value = dict(response)
    value.update(
        {
            "conversation_id": reservation.conversation_id,
            "turn_id": reservation.turn_id,
            "client_event_id": reservation.client_event_id,
            "message_ids": {
                "user": reservation.user_message_id,
                "assistant": reservation.assistant_message_id,
            },
            "capture": {"mode": mode, "stored": bool(stored)},
        }
    )
    if conversation_token:
        value["conversation_token"] = conversation_token
    return value


def _load_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        from psycopg_pool import ConnectionPool
    except ImportError as error:
        raise CaptureUnavailable(
            "PostgreSQL support is unavailable; install requirements.txt"
        ) from error
    return psycopg, dict_row, Jsonb, ConnectionPool


def run_migrations(database_url: str, migrations_dir: pathlib.Path | None = None) -> list[str]:
    """Apply ordered SQL migrations and return the versions applied now."""

    if not str(database_url or "").strip():
        return []
    psycopg, _, _, _ = _load_psycopg()
    directory = migrations_dir or pathlib.Path(__file__).with_name("migrations")
    applied: list[str] = []
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("fortune-conversation-migrations",),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
                version = path.stem
                cursor.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    (version,),
                )
                if cursor.fetchone():
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied.append(version)
        connection.commit()
    return applied


class ConversationRecorder:
    """Capture completed turns without persisting network or device identifiers."""

    def __init__(
        self,
        database_url: str | None = None,
        mode: str | None = None,
        *,
        app_version: str | None = None,
        prompt_version: str | None = None,
        token_secret: str | None = None,
        retention_days: int | None = None,
        lease_seconds: int | None = None,
        max_turns: int | None = None,
    ):
        self.database_url = str(
            database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
        ).strip()
        self.mode = capture_mode(
            mode if mode is not None else os.environ.get("FORTUNE_CONVERSATION_CAPTURE")
        )
        self.app_version = str(
            app_version
            or os.environ.get("FORTUNE_APP_VERSION")
            or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
            or os.environ.get("RAILWAY_DEPLOYMENT_ID")
            or "local"
        )[:120]
        self.prompt_version = str(prompt_version or PROMPT_POLICY_VERSION)[:80]
        self.token_secret = str(
            token_secret
            if token_secret is not None
            else os.environ.get("FORTUNE_CONVERSATION_TOKEN_SECRET", "")
        ).strip()
        self.retention_days = self._bounded_int(
            retention_days,
            "FORTUNE_CONVERSATION_RETENTION_DAYS",
            default=90,
            minimum=1,
            maximum=365,
        )
        self.lease_seconds = self._bounded_int(
            lease_seconds,
            "FORTUNE_TURN_LEASE_SECONDS",
            default=180,
            minimum=30,
            maximum=900,
        )
        self.max_turns = self._bounded_int(
            max_turns,
            "FORTUNE_MAX_TURNS_PER_CONVERSATION",
            default=50,
            minimum=1,
            maximum=200,
        )
        self._pool = None
        self._dict_row = None
        self._jsonb = None
        self._purge_lock = threading.Lock()
        self._last_purge = 0.0

    @staticmethod
    def _bounded_int(value, env_name, *, default, minimum, maximum):
        candidate = value if value is not None else os.environ.get(env_name, default)
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    @property
    def configured(self) -> bool:
        return bool(self.database_url)

    @property
    def required(self) -> bool:
        return self.mode != "none"

    @property
    def ready(self) -> bool:
        return self._pool is not None

    @property
    def enabled(self) -> bool:
        return self.required and self.ready

    def open(self) -> None:
        if self.required and not self.configured:
            raise CaptureUnavailable(
                "Conversation capture was enabled without DATABASE_URL"
            )
        if self.required and len(self.token_secret) < 32:
            raise CaptureUnavailable(
                "Conversation capture requires FORTUNE_CONVERSATION_TOKEN_SECRET "
                "with at least 32 characters"
            )
        if not self.configured or self._pool is not None:
            return
        _, dict_row, Jsonb, ConnectionPool = _load_psycopg()
        try:
            self._pool = ConnectionPool(
                conninfo=self.database_url,
                min_size=1,
                max_size=5,
                timeout=10,
                open=True,
            )
            self._dict_row = dict_row
            self._jsonb = Jsonb
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.execute(
                        "SELECT 1 FROM schema_migrations "
                        "WHERE version = %s",
                        (SCHEMA_VERSION,),
                    )
                    if not cursor.fetchone():
                        raise CaptureUnavailable(
                            "Conversation schema is missing; run migrations first"
                        )
            self.purge_expired(force=True)
        except Exception as error:
            self.close()
            if isinstance(error, CaptureUnavailable):
                raise
            raise CaptureUnavailable("PostgreSQL conversation storage is unavailable") from error

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
        self._pool = None

    def check(self) -> bool:
        if not self.ready:
            return False
        try:
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() == (1,)
        except Exception:
            return False

    def begin_turn(
        self,
        *,
        question: str = "",
        conversation_id: Any = None,
        conversation_token: Any = None,
        client_event_id: Any = None,
        page_context: dict | None = None,
        client_surface: Any = None,
        automation_source: Any = None,
        evaluator_slot: Any = None,
        history_context: list[dict] | None = None,
        interaction_context: dict | None = None,
    ) -> TurnReservation:
        accepted_conversation_id = self.accepted_conversation_id(
            conversation_id,
            conversation_token,
        )
        reservation = new_reservation(
            accepted_conversation_id,
            client_event_id,
            self.mode,
            client_surface,
            automation_source,
        )
        if not self.required:
            return reservation
        if not self.ready:
            raise CaptureUnavailable("Required conversation capture is not ready")
        self.purge_expired()
        request_fingerprint = fingerprint_request(
            self.token_secret,
            question=question,
            page_context=page_context,
            client_surface=client_surface,
            history_context=history_context,
            automation_source=automation_source,
        )
        attributed_evaluator = sanitized_evaluator_slot(evaluator_slot)
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor(row_factory=self._dict_row) as cursor:
                        cursor.execute(
                            """
                            INSERT INTO conversations (
                                id, capture_mode, client_surface, page_context,
                                app_version, is_automated, automation_source,
                                evaluator_slot, evaluator_attribution_version,
                                evaluator_attribution_source, evaluator_attributed_by,
                                evaluator_attributed_at,
                                expires_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                CASE WHEN %s IS NULL THEN NULL ELSE NOW() END,
                                NOW() + (%s * INTERVAL '1 day')
                            )
                            ON CONFLICT (id) DO UPDATE SET
                                last_seen_at = NOW(),
                                is_automated = conversations.is_automated
                                    OR EXCLUDED.is_automated,
                                automation_source = CASE
                                    WHEN EXCLUDED.is_automated
                                      THEN COALESCE(
                                        NULLIF(EXCLUDED.automation_source, ''),
                                        conversations.automation_source
                                      )
                                    ELSE conversations.automation_source
                                END,
                                evaluator_slot = COALESCE(
                                    conversations.evaluator_slot,
                                    EXCLUDED.evaluator_slot
                                ),
                                evaluator_attribution_version = CASE
                                    WHEN conversations.evaluator_slot IS NULL
                                     AND EXCLUDED.evaluator_slot IS NOT NULL
                                      THEN conversations.evaluator_attribution_version + 1
                                    ELSE conversations.evaluator_attribution_version
                                END,
                                evaluator_attribution_source = CASE
                                    WHEN conversations.evaluator_slot IS NULL
                                     AND EXCLUDED.evaluator_slot IS NOT NULL
                                      THEN EXCLUDED.evaluator_attribution_source
                                    ELSE conversations.evaluator_attribution_source
                                END,
                                evaluator_attributed_by = CASE
                                    WHEN conversations.evaluator_slot IS NULL
                                     AND EXCLUDED.evaluator_slot IS NOT NULL
                                      THEN EXCLUDED.evaluator_attributed_by
                                    ELSE conversations.evaluator_attributed_by
                                END,
                                evaluator_attributed_at = CASE
                                    WHEN conversations.evaluator_slot IS NULL
                                     AND EXCLUDED.evaluator_slot IS NOT NULL
                                      THEN EXCLUDED.evaluator_attributed_at
                                    ELSE conversations.evaluator_attributed_at
                                END
                            """,
                            (
                                reservation.conversation_id,
                                self.mode,
                                sanitized_surface(client_surface),
                                self._jsonb(page_context or {}),
                                self.app_version,
                                reservation.is_automated,
                                reservation.automation_source or None,
                                attributed_evaluator,
                                1 if attributed_evaluator else 0,
                                "session" if attributed_evaluator else None,
                                attributed_evaluator,
                                attributed_evaluator,
                                self.retention_days,
                            ),
                        )
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            (reservation.conversation_id,),
                        )
                        cursor.execute(
                            """
                            INSERT INTO conversation_turns (
                                id, conversation_id, client_event_id,
                                user_message_id, assistant_message_id,
                                request_fingerprint, lease_id, capture_mode,
                                page_context, chat_stage, request_kind,
                                request_language, response_language,
                                prompt_policy_version,
                                status, privacy_state, review_state,
                                prompt_version, app_version
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                'pending', 'pending', 'pending', %s, %s
                            )
                            ON CONFLICT (client_event_id) DO NOTHING
                            RETURNING id
                            """,
                            (
                                reservation.turn_id,
                                reservation.conversation_id,
                                reservation.client_event_id,
                                reservation.user_message_id,
                                reservation.assistant_message_id,
                                request_fingerprint,
                                reservation.lease_id,
                                reservation.capture_mode,
                                self._jsonb(page_context or {}),
                                str((interaction_context or {}).get("chat_stage") or "unknown"),
                                str((interaction_context or {}).get("request_kind") or "unknown"),
                                str((interaction_context or {}).get("request_language") or "und"),
                                "und",
                                str((interaction_context or {}).get("prompt_policy_version") or "legacy")[:80],
                                self.prompt_version,
                                self.app_version,
                            ),
                        )
                        inserted = cursor.fetchone()
                        if inserted:
                            cursor.execute(
                                "SELECT COUNT(*) AS turn_count FROM conversation_turns "
                                "WHERE conversation_id = %s",
                                (reservation.conversation_id,),
                            )
                            count_row = cursor.fetchone()
                            if int(count_row["turn_count"]) > self.max_turns:
                                raise ConversationLimit(
                                    "The conversation reached its turn limit"
                                )
                            return TurnReservation(**{
                                **reservation.__dict__,
                                "persisted": True,
                            })
                        cursor.execute(
                            """
                            SELECT t.id, t.conversation_id, t.user_message_id,
                                   t.assistant_message_id, t.request_fingerprint,
                                   t.lease_id, t.capture_mode, t.status, t.response_json,
                                   c.client_surface, c.is_automated,
                                   c.automation_source,
                                   t.created_at < NOW() - (%s * INTERVAL '1 second')
                                       AS lease_expired
                            FROM conversation_turns t
                            JOIN conversations c ON c.id = t.conversation_id
                            WHERE t.client_event_id = %s
                            """,
                            (self.lease_seconds, reservation.client_event_id),
                        )
                        existing = cursor.fetchone()
                        if not existing:
                            raise CaptureUnavailable("Idempotent turn reservation was lost")
                        if not hmac.compare_digest(
                            str(existing["request_fingerprint"]),
                            request_fingerprint,
                        ):
                            raise IdempotencyConflict(
                                "The client event ID was already used for different input"
                            )
                        existing_conversation_id = str(existing["conversation_id"])
                        if existing_conversation_id != reservation.conversation_id:
                            cursor.execute(
                                "DELETE FROM conversations WHERE id = %s "
                                "AND NOT EXISTS ("
                                "SELECT 1 FROM conversation_turns WHERE conversation_id = %s"
                                ")",
                                (
                                    reservation.conversation_id,
                                    reservation.conversation_id,
                                ),
                            )
                        if existing["status"] == "pending" and existing["lease_expired"]:
                            cursor.execute(
                                """
                                UPDATE conversation_turns
                                SET lease_id = %s, created_at = NOW()
                                WHERE id = %s AND status = 'pending'
                                  AND lease_id = %s
                                RETURNING id
                                """,
                                (
                                    reservation.lease_id,
                                    existing["id"],
                                    existing["lease_id"],
                                ),
                            )
                            if cursor.fetchone():
                                return TurnReservation(
                                    conversation_id=existing_conversation_id,
                                    turn_id=str(existing["id"]),
                                    client_event_id=reservation.client_event_id,
                                    user_message_id=str(existing["user_message_id"]),
                                    assistant_message_id=str(existing["assistant_message_id"]),
                                    lease_id=reservation.lease_id,
                                    capture_mode=str(existing["capture_mode"]),
                                    client_surface=str(existing["client_surface"]),
                                    is_automated=bool(existing["is_automated"]),
                                    automation_source=str(existing["automation_source"] or ""),
                                    persisted=True,
                                )
                        if existing["status"] == "failed":
                            return TurnReservation(
                                conversation_id=existing_conversation_id,
                                turn_id=str(existing["id"]),
                                client_event_id=reservation.client_event_id,
                                user_message_id=str(existing["user_message_id"]),
                                assistant_message_id=str(existing["assistant_message_id"]),
                                lease_id=str(existing["lease_id"]),
                                capture_mode=str(existing["capture_mode"]),
                                client_surface=str(existing["client_surface"]),
                                is_automated=bool(existing["is_automated"]),
                                automation_source=str(existing["automation_source"] or ""),
                                persisted=True,
                                duplicate_response={
                                    "error": "This turn already failed. Send it again as a new turn."
                                },
                            )
                        return TurnReservation(
                            conversation_id=existing_conversation_id,
                            turn_id=str(existing["id"]),
                            client_event_id=reservation.client_event_id,
                            user_message_id=str(existing["user_message_id"]),
                            assistant_message_id=str(existing["assistant_message_id"]),
                            lease_id=str(existing["lease_id"]),
                            capture_mode=str(existing["capture_mode"]),
                            client_surface=str(existing["client_surface"]),
                            is_automated=bool(existing["is_automated"]),
                            automation_source=str(existing["automation_source"] or ""),
                            persisted=True,
                            duplicate_response=existing["response_json"],
                            in_progress=existing["status"] != "complete",
                        )
        except CaptureUnavailable:
            raise
        except IdempotencyConflict:
            raise
        except ConversationLimit:
            raise
        except Exception as error:
            raise CaptureUnavailable("The conversation turn could not be reserved") from error

    def conversation_token(self, conversation_id: str) -> str:
        if not self.token_secret:
            return ""
        return hmac.new(
            self.token_secret.encode("utf-8"),
            conversation_id.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def accepted_conversation_id(self, value: Any, token: Any) -> str | None:
        candidate = valid_uuid(value)
        if not candidate:
            return None
        if not self.required:
            return candidate
        expected = self.conversation_token(candidate)
        supplied = str(token or "")
        return candidate if hmac.compare_digest(expected, supplied) else None

    def purge_expired(self, *, force: bool = False) -> int:
        if not self.ready:
            return 0
        now = time.monotonic()
        if not force and now - self._last_purge < 3600:
            return 0
        if not self._purge_lock.acquire(blocking=False):
            return 0
        try:
            now = time.monotonic()
            if not force and now - self._last_purge < 3600:
                return 0
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "DELETE FROM conversations WHERE expires_at <= NOW()"
                        )
                        deleted = max(0, int(cursor.rowcount))
            self._last_purge = now
            return deleted
        except Exception as error:
            if self.required:
                raise CaptureUnavailable(
                    "Expired conversation data could not be purged"
                ) from error
            return 0
        finally:
            self._purge_lock.release()

    def complete_turn(
        self,
        reservation: TurnReservation,
        *,
        question: str,
        response: dict,
        privacy_state: str,
        latency_ms: int,
        error_code: str | None = None,
    ) -> bool:
        if not reservation.persisted:
            return False
        if not self.enabled:
            raise CaptureUnavailable("Required conversation capture is not ready")
        review_state = (
            "ready"
            if reservation.capture_mode == "transcript"
            and privacy_state == "clear"
            and reservation.client_surface in HUMAN_REVIEW_SURFACES
            else "pending" if privacy_state == "clear" else "excluded"
        )
        source_ids = [
            str(item.get("id"))
            for item in response.get("sources", [])
            if isinstance(item, dict) and item.get("id")
        ]
        store_text = reservation.capture_mode == "transcript" and privacy_state == "clear"
        stored_response = dict(response) if store_text else {
            key: response.get(key)
            for key in (
                "kind",
                "retrieval_scope",
                "model",
                "model_provider",
                "model_called",
                "conversation_id",
                "turn_id",
                "client_event_id",
                "message_ids",
                "capture",
                "chat_stage",
                "request_kind",
                "request_language",
                "response_language",
                "prompt_policy_version",
            )
            if key in response
        }
        stored_response.pop("conversation_token", None)
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        if store_text:
                            cursor.executemany(
                                """
                                INSERT INTO conversation_messages (
                                    id, conversation_id, turn_id, ordinal, role, content
                                ) VALUES (%s, %s, %s, %s, %s, %s)
                                ON CONFLICT (id) DO NOTHING
                                """,
                                [
                                    (
                                        reservation.user_message_id,
                                        reservation.conversation_id,
                                        reservation.turn_id,
                                        0,
                                        "user",
                                        str(question)[:2000],
                                    ),
                                    (
                                        reservation.assistant_message_id,
                                        reservation.conversation_id,
                                        reservation.turn_id,
                                        1,
                                        "assistant",
                                        str(response.get("message") or "")[:4000],
                                    ),
                                ],
                            )
                        cursor.execute(
                            """
                            UPDATE conversation_turns SET
                                status = 'complete',
                                privacy_state = %s,
                                review_state = %s,
                                response_kind = %s,
                                retrieval_scope = %s,
                                source_ids = %s,
                                model = %s,
                                model_called = %s,
                                latency_ms = %s,
                                error_code = %s,
                                chat_stage = %s,
                                request_kind = %s,
                                request_language = %s,
                                response_language = %s,
                                prompt_policy_version = %s,
                                response_json = %s,
                                completed_at = NOW()
                            WHERE id = %s AND status = 'pending' AND lease_id = %s
                            """,
                            (
                                privacy_state,
                                review_state,
                                response.get("kind"),
                                response.get("retrieval_scope"),
                                self._jsonb(source_ids),
                                response.get("model"),
                                bool(response.get("model_called")),
                                max(0, int(latency_ms)),
                                error_code,
                                response.get("chat_stage") or "unknown",
                                response.get("request_kind") or "unknown",
                                response.get("request_language") or "und",
                                response.get("response_language") or "und",
                                str(response.get("prompt_policy_version") or "legacy")[:80],
                                self._jsonb(stored_response),
                                reservation.turn_id,
                                reservation.lease_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise CaptureUnavailable("The conversation turn was not completed")
                        cursor.execute(
                            "UPDATE conversations SET last_seen_at = NOW(), last_turn_at = NOW() "
                            "WHERE id = %s",
                            (reservation.conversation_id,),
                        )
            return True
        except CaptureUnavailable:
            raise
        except Exception as error:
            raise CaptureUnavailable("The conversation turn could not be stored") from error

    def fail_turn(
        self,
        reservation: TurnReservation,
        *,
        question: str = "",
        latency_ms: int,
        error_code: str,
        model: str,
        model_called: bool,
        retrieval_scope: str,
        privacy_state: str = "clear",
        interaction_context: dict | None = None,
    ) -> bool:
        """Close a failed request and retain only a privacy-clear human question."""

        if not reservation.persisted:
            return False
        if not self.enabled:
            raise CaptureUnavailable("Required conversation capture is not ready")
        context = dict(interaction_context or {})
        store_question = (
            reservation.capture_mode == "transcript"
            and privacy_state == "clear"
            and reservation.client_surface in HUMAN_REVIEW_SURFACES
            and bool(str(question).strip())
        )
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        if store_question:
                            cursor.execute(
                                """
                                INSERT INTO conversation_messages (
                                    id, conversation_id, turn_id, ordinal, role, content
                                ) VALUES (%s, %s, %s, 0, 'user', %s)
                                ON CONFLICT (id) DO NOTHING
                                """,
                                (
                                    reservation.user_message_id,
                                    reservation.conversation_id,
                                    reservation.turn_id,
                                    str(question)[:2000],
                                ),
                            )
                        cursor.execute(
                            """
                            UPDATE conversation_turns SET
                                status = 'failed',
                                privacy_state = %s,
                                review_state = 'excluded',
                                response_kind = NULL,
                                retrieval_scope = %s,
                                source_ids = '[]'::jsonb,
                                model = %s,
                                model_called = %s,
                                latency_ms = %s,
                                error_code = %s,
                                chat_stage = %s,
                                request_kind = %s,
                                request_language = %s,
                                response_language = 'und',
                                prompt_policy_version = %s,
                                response_json = NULL,
                                completed_at = NOW()
                            WHERE id = %s AND status = 'pending' AND lease_id = %s
                            """,
                            (
                                (
                                    privacy_state
                                    if privacy_state in {"clear", "sensitive_handoff"}
                                    else "clear"
                                ),
                                retrieval_scope if retrieval_scope in {"page", "site", "staff"} else "staff",
                                str(model or "")[:120],
                                bool(model_called),
                                max(0, int(latency_ms)),
                                str(error_code or "model_error")[:80],
                                context.get("chat_stage") or "unknown",
                                context.get("request_kind") or "unknown",
                                context.get("request_language") or "und",
                                str(context.get("prompt_policy_version") or self.prompt_version)[:80],
                                reservation.turn_id,
                                reservation.lease_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise CaptureUnavailable("The failed conversation turn was not closed")
                        cursor.execute(
                            "UPDATE conversations SET last_seen_at = NOW(), last_turn_at = NOW() "
                            "WHERE id = %s",
                            (reservation.conversation_id,),
                        )
            return True
        except CaptureUnavailable:
            raise
        except Exception as error:
            raise CaptureUnavailable("The failed conversation turn could not be stored") from error
