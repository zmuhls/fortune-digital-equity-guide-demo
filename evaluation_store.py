"""Authenticated, shared evaluation data for privacy-clear human transcripts."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from prompt_policy import (
    CURRENT_TUNABLE_SELECTIONS,
    PROMPT_LAB_TUNABLE_MODULES,
    TEAM_TUNABLE_PROMPT_MODULES,
)


EVALUATION_SCHEMA_VERSION = "009_human_review_capture"
COOKIE_NAME = "__Host-fs_eval"
SLOT_KEYS = ("admin", "editor-1", "editor-2", "editor-3")
SHARED_BUCKET_OWNER = "admin"
COLOR_KEYS = {"blue", "sky", "eggplant", "coral"}
ANNOTATION_CATEGORIES = {"helpful", "unclear", "incorrect", "unsafe", "other"}
PROMPT_EDITABLE_KEYS = PROMPT_LAB_TUNABLE_MODULES
PROMPT_MODULE_LABELS = {
    "style": "Tone and concision",
    "clarification": "Clarification style",
    "page_awareness": "Page awareness and flow",
    "follow_up": "Follow-up advancement",
}
PROMPT_PROPOSAL_STATUSES = {"draft", "ready", "archived"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class EvaluationUnavailable(RuntimeError):
    """Raised when the evaluation system cannot meet its security contract."""


class AuthenticationFailed(RuntimeError):
    """Raised with one generic response for every failed login or invitation."""


class EvaluationForbidden(RuntimeError):
    """Raised when an authenticated account lacks permission."""


class EvaluationConflict(RuntimeError):
    """Raised when an optimistic version no longer matches stored state."""

    def __init__(self, message: str, current: dict | None = None):
        super().__init__(message)
        self.current = current or {}


class EvaluationValidation(RuntimeError):
    """Raised when bounded evaluator input is invalid."""


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise EvaluationValidation("Enter a valid email address.")
    return email


def _display_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if not 1 <= len(name) <= 80:
        raise EvaluationValidation("Enter a name between 1 and 80 characters.")
    return name


def _password(value: Any) -> str:
    password = str(value or "")
    if not 12 <= len(password) <= 128:
        raise EvaluationValidation("Use a password between 12 and 128 characters.")
    return password


def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise EvaluationValidation(f"{label} must be a UUID.") from error


def _reviewer_note(value: Any, *, maximum: int, label: str) -> str | None:
    note = str(value or "").strip()
    if len(note) > maximum:
        raise EvaluationValidation(f"{label} must be {maximum} characters or fewer.")
    return note or None


def _annotation_category(value: Any, *, allow_empty: bool = False) -> str | None:
    category = str(value or "").strip().lower()
    if allow_empty and not category:
        return None
    if category not in ANNOTATION_CATEGORIES:
        raise EvaluationValidation("Choose an available annotation type.")
    return category


def _proposal_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not 1 <= len(title) <= 80:
        raise EvaluationValidation("Proposal titles must be between 1 and 80 characters.")
    return title


def _prompt_module_values(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise EvaluationValidation("Prompt module suggestions must be an object.")
    unknown = sorted(set(value) - set(PROMPT_EDITABLE_KEYS))
    if unknown:
        raise EvaluationValidation("Only the four reviewable prompt modules can be proposed.")
    modules: dict[str, str] = {}
    for key in PROMPT_EDITABLE_KEYS:
        if key not in value:
            continue
        if not isinstance(value[key], str):
            raise EvaluationValidation("Prompt module suggestions must be text.")
        suggestion = " ".join(value[key].split())
        if not suggestion:
            continue
        if len(suggestion) > 500:
            raise EvaluationValidation("Prompt module suggestions must be 500 characters or fewer.")
        modules[key] = suggestion
    if not modules:
        raise EvaluationValidation("Propose a change to at least one reviewable module.")
    return modules


def _proposal_comment(value: Any) -> str:
    comment = " ".join(str(value or "").split())
    if not 1 <= len(comment) <= 1000:
        raise EvaluationValidation("Comments must be between 1 and 1000 characters.")
    return comment


def _json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _load_dependencies():
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import InvalidHashError, VerifyMismatchError
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        from psycopg_pool import ConnectionPool
    except ImportError as error:
        raise EvaluationUnavailable(
            "Evaluation dependencies are unavailable; install requirements.txt"
        ) from error
    return PasswordHasher, InvalidHashError, VerifyMismatchError, dict_row, Jsonb, ConnectionPool


class EvaluationStore:
    """PostgreSQL-backed identities, buckets, placements, and transcript reads."""

    def __init__(
        self,
        database_url: str | None = None,
        enabled: bool | None = None,
        auth_secret: str | None = None,
    ):
        self.database_url = str(
            database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
        ).strip()
        self.enabled = (
            bool(enabled)
            if enabled is not None
            else _enabled(os.environ.get("FORTUNE_EVALUATION_ENABLED"))
        )
        self.auth_secret = str(
            auth_secret
            if auth_secret is not None
            else os.environ.get("FORTUNE_EVALUATOR_AUTH_SECRET", "")
        ).strip()
        self.idle_seconds = _bounded_int(
            "FORTUNE_EVALUATOR_IDLE_SECONDS", 1800, 300, 7200
        )
        self.absolute_seconds = _bounded_int(
            "FORTUNE_EVALUATOR_ABSOLUTE_SECONDS", 28800, 1800, 86400
        )
        self.idle_seconds = min(self.idle_seconds, self.absolute_seconds)
        self.invite_seconds = _bounded_int(
            "FORTUNE_EVALUATOR_INVITE_SECONDS", 86400, 900, 604800
        )
        self.min_inactive_seconds = _bounded_int(
            "FORTUNE_EVALUATOR_MIN_INACTIVE_SECONDS", 60, 0, 3600
        )
        self._pool = None
        self._dict_row = None
        self._jsonb = None
        self._password_hasher = None
        self._invalid_hash_errors: tuple[type[Exception], ...] = ()
        self._dummy_hash = ""
        self._password_lock = threading.BoundedSemaphore(2)

    @property
    def ready(self) -> bool:
        return self._pool is not None

    def open(self) -> None:
        if not self.enabled or self._pool is not None:
            return
        if not self.database_url:
            raise EvaluationUnavailable("Evaluation access requires DATABASE_URL")
        if len(self.auth_secret) < 32:
            raise EvaluationUnavailable(
                "Evaluation access requires FORTUNE_EVALUATOR_AUTH_SECRET with at least 32 characters"
            )
        (
            PasswordHasher,
            InvalidHashError,
            VerifyMismatchError,
            dict_row,
            Jsonb,
            ConnectionPool,
        ) = _load_dependencies()
        self._dict_row = dict_row
        self._jsonb = Jsonb
        self._password_hasher = PasswordHasher(
            time_cost=2,
            memory_cost=19456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        self._invalid_hash_errors = (InvalidHashError, VerifyMismatchError)
        self._dummy_hash = self._password_hasher.hash(secrets.token_urlsafe(24))
        try:
            self._pool = ConnectionPool(
                conninfo=self.database_url,
                min_size=1,
                max_size=5,
                timeout=10,
                open=True,
            )
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = %s",
                        (EVALUATION_SCHEMA_VERSION,),
                    )
                    if not cursor.fetchone():
                        raise EvaluationUnavailable(
                            "Evaluation schema is missing; run migrations first"
                        )
                    cursor.execute(
                        "SELECT COUNT(*) FROM evaluator_accounts WHERE slot_key = ANY(%s)",
                        (list(SLOT_KEYS),),
                    )
                    if cursor.fetchone()[0] != 4:
                        raise EvaluationUnavailable(
                            "Evaluation schema does not contain exactly four account slots"
                        )
                    cursor.execute(
                        "DELETE FROM evaluator_sessions "
                        "WHERE absolute_expires_at <= NOW() OR revoked_at IS NOT NULL"
                    )
                connection.commit()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    def check(self) -> bool:
        if not self.enabled or self._pool is None:
            return False
        try:
            with self._pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone()[0] == 1
        except Exception:
            return False

    def public_status(self) -> dict:
        status = {
            "enabled": self.enabled,
            "ready": self.check(),
            "total_slots": 4,
            "claimed_slots": 0,
            "unassigned_slots": 4,
        }
        if not status["ready"]:
            return status
        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FILTER (WHERE claimed_at IS NOT NULL), "
                    "COUNT(*) FILTER (WHERE claimed_at IS NULL) "
                    "FROM evaluator_accounts"
                )
                claimed, unassigned = cursor.fetchone()
                status.update(
                    claimed_slots=int(claimed),
                    unassigned_slots=int(unassigned),
                )
        return status

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(
            self.auth_secret.encode(),
            f"{purpose}\0{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def csrf_token(self, session_token: str) -> str:
        return self._digest("csrf", session_token)

    def csrf_matches(self, session_token: str, supplied: str) -> bool:
        return hmac.compare_digest(
            self.csrf_token(session_token), str(supplied or "")
        )

    def _verify_password(self, password_hash: str, password: str) -> bool:
        with self._password_lock:
            try:
                return bool(self._password_hasher.verify(password_hash, password))
            except self._invalid_hash_errors:
                return False

    def _hash_password(self, password: str) -> str:
        with self._password_lock:
            return self._password_hasher.hash(password)

    @staticmethod
    def _account_payload(row: dict) -> dict:
        return {
            "slot_key": row["slot_key"],
            "role": row["role"],
            "display_name": row.get("display_name") or row["slot_key"].replace("-", " ").title(),
        }

    def _create_session(self, cursor, account: dict) -> dict:
        token = secrets.token_urlsafe(32)
        cursor.execute(
            """
            INSERT INTO evaluator_sessions (
                id, token_hash, account_slot, auth_version,
                idle_expires_at, absolute_expires_at
            ) VALUES (
                %s, %s, %s, %s,
                NOW() + (%s * INTERVAL '1 second'),
                NOW() + (%s * INTERVAL '1 second')
            )
            """,
            (
                str(uuid.uuid4()),
                self._digest("session", token),
                account["slot_key"],
                account["auth_version"],
                self.idle_seconds,
                self.absolute_seconds,
            ),
        )
        return {
            "session_token": token,
            "csrf_token": self.csrf_token(token),
            "account": self._account_payload(account),
        }

    def login(self, email_value: Any, password_value: Any) -> dict:
        if not self.ready:
            raise EvaluationUnavailable("Evaluation access is unavailable.")
        try:
            email = _normalize_email(email_value)
        except EvaluationValidation:
            email = "invalid@example.invalid"
        password = str(password_value or "")[:128]
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    "SELECT * FROM evaluator_accounts WHERE email_normalized = %s",
                    (email,),
                )
                account = cursor.fetchone()
                candidate_hash = (
                    account.get("password_hash") if account else None
                ) or self._dummy_hash
                valid = self._verify_password(candidate_hash, password)
                blocked = bool(
                    account
                    and (
                        account.get("claimed_at") is None
                        or account.get("disabled_at") is not None
                        or (
                            account.get("locked_until") is not None
                            and account["locked_until"] > datetime.now(timezone.utc)
                        )
                    )
                )
                if not account or not valid or blocked:
                    if account:
                        cursor.execute(
                            """
                            UPDATE evaluator_accounts
                            SET failed_login_count = failed_login_count + 1,
                                locked_until = CASE
                                    WHEN failed_login_count + 1 >= 5
                                    THEN NOW() + INTERVAL '15 minutes'
                                    ELSE locked_until
                                END,
                                updated_at = NOW()
                            WHERE slot_key = %s
                            """,
                            (account["slot_key"],),
                        )
                    connection.commit()
                    raise AuthenticationFailed("Email or password was not recognized.")
                cursor.execute(
                    """
                    UPDATE evaluator_accounts
                    SET failed_login_count = 0, locked_until = NULL, updated_at = NOW()
                    WHERE slot_key = %s
                    """,
                    (account["slot_key"],),
                )
                result = self._create_session(cursor, account)
            connection.commit()
        return result

    def authenticate(self, session_token: str) -> dict | None:
        if not self.ready or not session_token:
            return None
        token_hash = self._digest("session", session_token)
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT a.*, s.id AS session_id, s.absolute_expires_at
                    FROM evaluator_sessions s
                    JOIN evaluator_accounts a ON a.slot_key = s.account_slot
                    WHERE s.token_hash = %s
                      AND s.revoked_at IS NULL
                      AND s.idle_expires_at > NOW()
                      AND s.absolute_expires_at > NOW()
                      AND s.auth_version = a.auth_version
                      AND a.claimed_at IS NOT NULL
                      AND a.disabled_at IS NULL
                    """,
                    (token_hash,),
                )
                account = cursor.fetchone()
                if not account:
                    return None
                cursor.execute(
                    """
                    UPDATE evaluator_sessions
                    SET last_seen_at = NOW(),
                        idle_expires_at = LEAST(
                            absolute_expires_at,
                            NOW() + (%s * INTERVAL '1 second')
                        )
                    WHERE id = %s
                    """,
                    (self.idle_seconds, account["session_id"]),
                )
            connection.commit()
        return self._account_payload(account)

    def logout(self, session_token: str) -> None:
        if not self.ready or not session_token:
            return
        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE evaluator_sessions SET revoked_at = NOW() "
                    "WHERE token_hash = %s AND revoked_at IS NULL",
                    (self._digest("session", session_token),),
                )
            connection.commit()

    def issue_invitation(
        self,
        slot_key: str,
        *,
        email: str | None = None,
        actor_slot: str = "admin",
        operation_id: str | None = None,
    ) -> str:
        if slot_key not in SLOT_KEYS:
            raise EvaluationValidation("Unknown account slot.")
        normalized_email = _normalize_email(email) if email else None
        token = secrets.token_urlsafe(32)
        operation = _uuid(operation_id or uuid.uuid4(), "operation_id")
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    "SELECT * FROM evaluator_accounts WHERE slot_key = %s FOR UPDATE",
                    (slot_key,),
                )
                account = cursor.fetchone()
                if not account or account.get("claimed_at") is not None:
                    raise EvaluationConflict("That account slot is not available.")
                cursor.execute(
                    """
                    UPDATE evaluator_accounts
                    SET email_normalized = COALESCE(%s, email_normalized),
                        invite_token_hash = %s,
                        invite_expires_at = NOW() + (%s * INTERVAL '1 second'),
                        invited_at = NOW(), updated_at = NOW()
                    WHERE slot_key = %s
                    """,
                    (
                        normalized_email,
                        self._digest("invite", token),
                        self.invite_seconds,
                        slot_key,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events
                        (id, operation_id, actor_slot, action, metadata)
                    VALUES (%s, %s, %s, 'account.invite', %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        operation,
                        actor_slot,
                        self._jsonb({"slot_key": slot_key}),
                    ),
                )
            connection.commit()
        return token

    def claim_invitation(
        self,
        token_value: Any,
        email_value: Any,
        display_name_value: Any,
        password_value: Any,
    ) -> dict:
        token = str(token_value or "")
        if len(token) < 32:
            raise AuthenticationFailed("This invitation is invalid or expired.")
        email = _normalize_email(email_value)
        name = _display_name(display_name_value)
        password_hash = self._hash_password(_password(password_value))
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM evaluator_accounts
                    WHERE invite_token_hash = %s
                      AND invite_expires_at > NOW()
                      AND claimed_at IS NULL
                    FOR UPDATE
                    """,
                    (self._digest("invite", token),),
                )
                account = cursor.fetchone()
                if not account or (
                    account.get("email_normalized")
                    and account["email_normalized"] != email
                ):
                    raise AuthenticationFailed("This invitation is invalid or expired.")
                cursor.execute(
                    """
                    SELECT 1 FROM evaluator_accounts
                    WHERE email_normalized = %s AND slot_key <> %s
                    """,
                    (email, account["slot_key"]),
                )
                if cursor.fetchone():
                    raise AuthenticationFailed("This invitation is invalid or expired.")
                cursor.execute(
                    """
                    UPDATE evaluator_accounts
                    SET email_normalized = %s, display_name = %s,
                        password_hash = %s, claimed_at = NOW(),
                        invite_token_hash = NULL, invite_expires_at = NULL,
                        failed_login_count = 0, locked_until = NULL,
                        auth_version = auth_version + 1, updated_at = NOW()
                    WHERE slot_key = %s
                    RETURNING *
                    """,
                    (email, name, password_hash, account["slot_key"]),
                )
                claimed = cursor.fetchone()
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events
                        (id, operation_id, actor_slot, action, metadata)
                    VALUES (%s, %s, %s, 'account.claim', %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        str(uuid.uuid4()),
                        claimed["slot_key"],
                        self._jsonb({"slot_key": claimed["slot_key"]}),
                    ),
                )
                result = self._create_session(cursor, claimed)
            connection.commit()
        return result

    def reset_account_invitation(
        self,
        slot_key: str,
        *,
        email: str | None = None,
        actor_slot: str = "admin",
        operation_id: str | None = None,
    ) -> str:
        """Revoke a claimed account's login and return one replacement invite."""
        if slot_key not in SLOT_KEYS:
            raise EvaluationValidation("Unknown account slot.")
        normalized_email = _normalize_email(email) if email else None
        token = secrets.token_urlsafe(32)
        operation = _uuid(operation_id or uuid.uuid4(), "operation_id")
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    "SELECT * FROM evaluator_accounts WHERE slot_key = %s FOR UPDATE",
                    (slot_key,),
                )
                account = cursor.fetchone()
                if not account or account.get("claimed_at") is None:
                    raise EvaluationConflict("That account slot is not claimed.")
                cursor.execute(
                    """
                    UPDATE evaluator_sessions
                    SET revoked_at = NOW()
                    WHERE account_slot = %s AND revoked_at IS NULL
                    """,
                    (slot_key,),
                )
                revoked_sessions = cursor.rowcount
                cursor.execute(
                    """
                    UPDATE evaluator_accounts
                    SET email_normalized = %s,
                        display_name = NULL,
                        password_hash = NULL,
                        claimed_at = NULL,
                        invite_token_hash = %s,
                        invite_expires_at = NOW() + (%s * INTERVAL '1 second'),
                        invited_at = NOW(),
                        disabled_at = NULL,
                        auth_version = auth_version + 1,
                        failed_login_count = 0,
                        locked_until = NULL,
                        updated_at = NOW()
                    WHERE slot_key = %s
                    """,
                    (
                        normalized_email,
                        self._digest("invite", token),
                        self.invite_seconds,
                        slot_key,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events
                        (id, operation_id, actor_slot, action, metadata)
                    VALUES (%s, %s, %s, 'account.invite', %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        operation,
                        actor_slot,
                        self._jsonb({
                            "slot_key": slot_key,
                            "credential_reset": True,
                            "sessions_revoked": revoked_sessions,
                        }),
                    ),
                )
            connection.commit()
        return token

    def list_accounts(self) -> list[dict]:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT slot_key, role,
                           claimed_at IS NOT NULL AS claimed,
                           invite_token_hash IS NOT NULL
                             AND invite_expires_at > NOW() AS invitation_active,
                           disabled_at IS NOT NULL AS disabled
                    FROM evaluator_accounts
                    ORDER BY CASE slot_key
                        WHEN 'admin' THEN 0 WHEN 'editor-1' THEN 1
                        WHEN 'editor-2' THEN 2 ELSE 3 END
                    """
                )
                return [_json_value(dict(row)) for row in cursor.fetchall()]

    def _bucket_set_id(self, cursor, account_slot: str) -> str:
        """Return the one shared workspace while retaining the actor separately."""

        cursor.execute(
            "SELECT id FROM evaluation_bucket_sets "
            "WHERE account_slot = %s AND archived_at IS NULL",
            (SHARED_BUCKET_OWNER,),
        )
        row = cursor.fetchone()
        if not row:
            raise EvaluationUnavailable("The reviewer bucket set is unavailable.")
        return str(row.get("id") if isinstance(row, dict) else row[0])

    @staticmethod
    def _lock_review_record(
        cursor,
        bucket_set_id: str,
        conversation_id: str,
        message_id: str | None = None,
    ) -> None:
        """Serialize first writes where no evaluation row exists to lock yet."""

        scope = "annotation" if message_id else "evaluation"
        identity = ":".join(
            (scope, str(bucket_set_id), str(conversation_id), str(message_id or ""))
        )
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (identity,),
        )

    @staticmethod
    def _lock_prompt_proposal(cursor, proposal_id: str) -> None:
        """Serialize proposal creation and edits even before a row exists."""

        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"prompt-proposal:shared:{proposal_id}",),
        )

    def _prompt_workspace_scope(self, cursor) -> str:
        cursor.execute(
            """
            SELECT w.scope_key
            FROM prompt_review_workspaces w
            JOIN evaluation_bucket_sets s ON s.id = w.bucket_set_id
            WHERE w.scope_key = 'shared'
              AND s.account_slot = %s
              AND s.archived_at IS NULL
            """,
            (SHARED_BUCKET_OWNER,),
        )
        row = cursor.fetchone()
        if not row:
            raise EvaluationUnavailable("The shared prompt review workspace is unavailable.")
        return str(row.get("scope_key") if isinstance(row, dict) else row[0])

    @staticmethod
    def _prompt_module_catalog() -> list[dict]:
        catalog = []
        for key in PROMPT_EDITABLE_KEYS:
            variant = CURRENT_TUNABLE_SELECTIONS[key]
            catalog.append({
                "key": key,
                "label": PROMPT_MODULE_LABELS[key],
                "current_variant": variant,
                "current_value": TEAM_TUNABLE_PROMPT_MODULES[key][variant],
                "maximum_length": 500,
            })
        return catalog

    def _prompt_proposal_record(self, cursor, proposal_id: str, scope: str) -> dict:
        cursor.execute(
            """
            SELECT id, base_prompt_version, title, module_values, status, version,
                   created_by, updated_by, created_at, updated_at,
                   ready_at, archived_at
            FROM prompt_proposals
            WHERE id = %s AND scope_key = %s
            """,
            (proposal_id, scope),
        )
        row = cursor.fetchone()
        if not row:
            raise EvaluationForbidden("That prompt proposal is not available.")
        proposal = dict(row)
        cursor.execute(
            """
            SELECT proposal_version, base_prompt_version, title, module_values,
                   status, actor_slot, action, recorded_at
            FROM prompt_proposal_revisions
            WHERE proposal_id = %s
            ORDER BY proposal_version DESC
            """,
            (proposal_id,),
        )
        proposal["revisions"] = [dict(item) for item in cursor.fetchall()]
        cursor.execute(
            """
            SELECT id, body, actor_slot, proposal_version, created_at
            FROM prompt_proposal_comments
            WHERE proposal_id = %s
            ORDER BY created_at, id
            """,
            (proposal_id,),
        )
        proposal["comments"] = [dict(item) for item in cursor.fetchall()]
        return proposal

    @staticmethod
    def _prompt_operation(cursor, operation_id: str) -> dict | None:
        cursor.execute(
            """
            SELECT operation_id, proposal_id, action, proposal_version
            FROM prompt_proposal_events
            WHERE operation_id = %s
            """,
            (operation_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def _record_prompt_event(
        self,
        cursor,
        *,
        operation_id: str,
        proposal_id: str,
        actor_slot: str,
        action: str,
        proposal_version: int,
        metadata: dict | None = None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO prompt_proposal_events (
                id, operation_id, proposal_id, actor_slot, action,
                proposal_version, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()), operation_id, proposal_id, actor_slot, action,
                proposal_version, self._jsonb(metadata or {}),
            ),
        )

    def get_prompt_lab(
        self,
        account_slot: str,
        deployed_version: str,
        behavior_release: str,
    ) -> dict:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                scope = self._prompt_workspace_scope(cursor)
                cursor.execute(
                    """
                    SELECT id
                    FROM prompt_proposals
                    WHERE scope_key = %s
                    ORDER BY updated_at DESC, id
                    LIMIT 100
                    """,
                    (scope,),
                )
                proposal_ids = [str(row["id"]) for row in cursor.fetchall()]
                proposals = [
                    self._prompt_proposal_record(cursor, proposal_id, scope)
                    for proposal_id in proposal_ids
                ]
        return _json_value({
            "scope": scope,
            "shared": True,
            "deployed": {
                "version": str(deployed_version or "unknown")[:80],
                "behavior_release": str(behavior_release or "unknown")[:120],
                "editable": False,
            },
            "editable_modules": self._prompt_module_catalog(),
            "code_controlled": [
                "Grounding and no-guessing rules",
                "Approved source access",
                "Privacy and safety rules",
                "Response validation and release activation",
            ],
            "activation": "code_review_and_deploy_only",
            "can_mark_status": account_slot == SHARED_BUCKET_OWNER,
            "proposals": proposals,
        })

    def create_prompt_proposal(
        self,
        account_slot: str,
        title_value: Any,
        module_values_value: Any,
        base_prompt_version: str,
        proposal_value: Any,
        operation_value: Any,
    ) -> dict:
        title = _proposal_title(title_value)
        modules = _prompt_module_values(module_values_value)
        proposal_id = _uuid(proposal_value, "proposal_id")
        operation_id = _uuid(operation_value, "operation_id")
        base_version = str(base_prompt_version or "").strip()
        if not 1 <= len(base_version) <= 80:
            raise EvaluationValidation("The deployed prompt version is unavailable.")
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                scope = self._prompt_workspace_scope(cursor)
                self._lock_prompt_proposal(cursor, proposal_id)
                repeated = self._prompt_operation(cursor, operation_id)
                if repeated:
                    if (
                        str(repeated["proposal_id"]) == proposal_id
                        and repeated["action"] == "proposal.create"
                    ):
                        return _json_value(
                            self._prompt_proposal_record(cursor, proposal_id, scope)
                        )
                    raise EvaluationConflict("That operation ID was already used.")
                cursor.execute(
                    "SELECT 1 FROM prompt_proposals WHERE id = %s",
                    (proposal_id,),
                )
                if cursor.fetchone():
                    raise EvaluationConflict("That proposal already exists.")
                cursor.execute(
                    """
                    INSERT INTO prompt_proposals (
                        id, scope_key, base_prompt_version, title, module_values,
                        status, version, created_by, updated_by
                    ) VALUES (%s, %s, %s, %s, %s, 'draft', 1, %s, %s)
                    """,
                    (
                        proposal_id, scope, base_version, title,
                        self._jsonb(modules), account_slot, account_slot,
                    ),
                )
                self._record_prompt_event(
                    cursor,
                    operation_id=operation_id,
                    proposal_id=proposal_id,
                    actor_slot=account_slot,
                    action="proposal.create",
                    proposal_version=1,
                    metadata={"module_keys": list(modules)},
                )
                proposal = self._prompt_proposal_record(cursor, proposal_id, scope)
            connection.commit()
        return _json_value(proposal)

    def update_prompt_proposal(
        self,
        account_slot: str,
        proposal_value: Any,
        title_value: Any,
        module_values_value: Any,
        expected_version_value: Any,
        operation_value: Any,
    ) -> dict:
        proposal_id = _uuid(proposal_value, "proposal_id")
        title = _proposal_title(title_value)
        modules = _prompt_module_values(module_values_value)
        operation_id = _uuid(operation_value, "operation_id")
        try:
            expected_version = max(1, int(expected_version_value))
        except (TypeError, ValueError) as error:
            raise EvaluationValidation("Proposal versions must be integers.") from error
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                scope = self._prompt_workspace_scope(cursor)
                self._lock_prompt_proposal(cursor, proposal_id)
                repeated = self._prompt_operation(cursor, operation_id)
                if repeated:
                    if (
                        str(repeated["proposal_id"]) == proposal_id
                        and repeated["action"] == "proposal.update"
                    ):
                        return _json_value(
                            self._prompt_proposal_record(cursor, proposal_id, scope)
                        )
                    raise EvaluationConflict("That operation ID was already used.")
                cursor.execute(
                    """
                    SELECT status, version
                    FROM prompt_proposals
                    WHERE id = %s AND scope_key = %s
                    FOR UPDATE
                    """,
                    (proposal_id, scope),
                )
                current = cursor.fetchone()
                if not current:
                    raise EvaluationForbidden("That prompt proposal is not available.")
                if current["status"] != "draft":
                    raise EvaluationValidation("Only draft proposals can be edited.")
                if expected_version != int(current["version"]):
                    raise EvaluationConflict(
                        "The proposal changed; refresh before saving.",
                        _json_value(self._prompt_proposal_record(cursor, proposal_id, scope)),
                    )
                next_version = expected_version + 1
                cursor.execute(
                    """
                    UPDATE prompt_proposals
                    SET title = %s, module_values = %s, version = %s,
                        updated_by = %s, updated_at = NOW()
                    WHERE id = %s AND scope_key = %s
                    """,
                    (
                        title, self._jsonb(modules), next_version, account_slot,
                        proposal_id, scope,
                    ),
                )
                self._record_prompt_event(
                    cursor,
                    operation_id=operation_id,
                    proposal_id=proposal_id,
                    actor_slot=account_slot,
                    action="proposal.update",
                    proposal_version=next_version,
                    metadata={"module_keys": list(modules)},
                )
                proposal = self._prompt_proposal_record(cursor, proposal_id, scope)
            connection.commit()
        return _json_value(proposal)

    def add_prompt_proposal_comment(
        self,
        account_slot: str,
        proposal_value: Any,
        comment_value: Any,
        operation_value: Any,
    ) -> dict:
        proposal_id = _uuid(proposal_value, "proposal_id")
        comment = _proposal_comment(comment_value)
        operation_id = _uuid(operation_value, "operation_id")
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                scope = self._prompt_workspace_scope(cursor)
                self._lock_prompt_proposal(cursor, proposal_id)
                repeated_operation = self._prompt_operation(cursor, operation_id)
                cursor.execute(
                    """
                    SELECT id, body, actor_slot, proposal_version, created_at
                    FROM prompt_proposal_comments
                    WHERE operation_id = %s
                    """,
                    (operation_id,),
                )
                repeated_comment = cursor.fetchone()
                if repeated_comment:
                    if not (
                        repeated_operation
                        and str(repeated_operation["proposal_id"]) == proposal_id
                        and repeated_operation["action"] == "proposal.comment"
                    ):
                        raise EvaluationConflict("That operation ID was already used.")
                    return _json_value(dict(repeated_comment))
                if repeated_operation:
                    raise EvaluationConflict("That operation ID was already used.")
                cursor.execute(
                    """
                    SELECT version FROM prompt_proposals
                    WHERE id = %s AND scope_key = %s
                    FOR SHARE
                    """,
                    (proposal_id, scope),
                )
                proposal = cursor.fetchone()
                if not proposal:
                    raise EvaluationForbidden("That prompt proposal is not available.")
                comment_id = str(uuid.uuid4())
                proposal_version = int(proposal["version"])
                cursor.execute(
                    """
                    INSERT INTO prompt_proposal_comments (
                        id, proposal_id, operation_id, body, actor_slot,
                        proposal_version
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, body, actor_slot, proposal_version, created_at
                    """,
                    (
                        comment_id, proposal_id, operation_id, comment,
                        account_slot, proposal_version,
                    ),
                )
                stored = dict(cursor.fetchone())
                self._record_prompt_event(
                    cursor,
                    operation_id=operation_id,
                    proposal_id=proposal_id,
                    actor_slot=account_slot,
                    action="proposal.comment",
                    proposal_version=proposal_version,
                    metadata={"length": len(comment)},
                )
            connection.commit()
        return _json_value(stored)

    def set_prompt_proposal_status(
        self,
        account_slot: str,
        proposal_value: Any,
        status_value: Any,
        expected_version_value: Any,
        operation_value: Any,
    ) -> dict:
        if account_slot != SHARED_BUCKET_OWNER:
            raise EvaluationForbidden("Only the administrator can change proposal status.")
        proposal_id = _uuid(proposal_value, "proposal_id")
        status = str(status_value or "").strip().lower()
        if status not in {"ready", "archived"}:
            raise EvaluationValidation("Proposal status can only be ready or archived.")
        operation_id = _uuid(operation_value, "operation_id")
        try:
            expected_version = max(1, int(expected_version_value))
        except (TypeError, ValueError) as error:
            raise EvaluationValidation("Proposal versions must be integers.") from error
        action = f"proposal.{status}"
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                scope = self._prompt_workspace_scope(cursor)
                self._lock_prompt_proposal(cursor, proposal_id)
                repeated = self._prompt_operation(cursor, operation_id)
                if repeated:
                    if (
                        str(repeated["proposal_id"]) == proposal_id
                        and repeated["action"] == action
                    ):
                        return _json_value(
                            self._prompt_proposal_record(cursor, proposal_id, scope)
                        )
                    raise EvaluationConflict("That operation ID was already used.")
                cursor.execute(
                    """
                    SELECT status, version FROM prompt_proposals
                    WHERE id = %s AND scope_key = %s
                    FOR UPDATE
                    """,
                    (proposal_id, scope),
                )
                current = cursor.fetchone()
                if not current:
                    raise EvaluationForbidden("That prompt proposal is not available.")
                if expected_version != int(current["version"]):
                    raise EvaluationConflict(
                        "The proposal changed; refresh before changing its status.",
                        _json_value(self._prompt_proposal_record(cursor, proposal_id, scope)),
                    )
                if status == "ready" and current["status"] != "draft":
                    raise EvaluationValidation("Only a draft can be marked ready.")
                if status == "archived" and current["status"] == "archived":
                    raise EvaluationValidation("That proposal is already archived.")
                next_version = expected_version + 1
                cursor.execute(
                    """
                    UPDATE prompt_proposals
                    SET status = %s, version = %s, updated_by = %s,
                        updated_at = NOW(),
                        ready_at = CASE WHEN %s = 'ready' THEN NOW() ELSE ready_at END,
                        archived_at = CASE WHEN %s = 'archived' THEN NOW() ELSE archived_at END
                    WHERE id = %s AND scope_key = %s
                    """,
                    (
                        status, next_version, account_slot, status, status,
                        proposal_id, scope,
                    ),
                )
                self._record_prompt_event(
                    cursor,
                    operation_id=operation_id,
                    proposal_id=proposal_id,
                    actor_slot=account_slot,
                    action=action,
                    proposal_version=next_version,
                    metadata={"from": current["status"], "to": status},
                )
                proposal = self._prompt_proposal_record(cursor, proposal_id, scope)
            connection.commit()
        return _json_value(proposal)

    def list_buckets(self, account_slot: str) -> list[dict]:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT b.id, b.standard_key, b.label, b.color_key,
                           b.sort_position, b.version, b.archived_at
                    FROM evaluation_buckets b
                    JOIN evaluation_bucket_sets s ON s.id = b.bucket_set_id
                    WHERE s.account_slot = %s
                      AND s.archived_at IS NULL
                      AND b.archived_at IS NULL
                    ORDER BY b.sort_position, b.id
                    """,
                    (SHARED_BUCKET_OWNER,),
                )
                return [_json_value(dict(row)) for row in cursor.fetchall()]

    def create_bucket(
        self,
        account_slot: str,
        label_value: Any,
        color_value: Any,
        operation_value: Any,
    ) -> dict:
        label = " ".join(str(label_value or "").split())
        if not 1 <= len(label) <= 40:
            raise EvaluationValidation("Bucket names must be between 1 and 40 characters.")
        color = str(color_value or "blue")
        if color not in COLOR_KEYS:
            raise EvaluationValidation("Choose an available bucket color.")
        operation_id = _uuid(operation_value, "operation_id")
        bucket_id = str(uuid.uuid4())
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                set_id = self._bucket_set_id(cursor, account_slot)
                cursor.execute(
                    "SELECT COUNT(*) AS bucket_count, "
                    "COALESCE(MAX(sort_position), 0) AS max_position "
                    "FROM evaluation_buckets WHERE bucket_set_id = %s AND archived_at IS NULL",
                    (set_id,),
                )
                bucket_stats = cursor.fetchone()
                if bucket_stats["bucket_count"] >= 8:
                    raise EvaluationValidation("A reviewer can keep up to eight active buckets.")
                cursor.execute(
                    """
                    INSERT INTO evaluation_buckets
                        (id, bucket_set_id, label, color_key, sort_position)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, standard_key, label, color_key,
                              sort_position, version, archived_at
                    """,
                    (bucket_id, set_id, label, color, int(bucket_stats["max_position"]) + 10),
                )
                bucket = dict(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events
                        (id, operation_id, actor_slot, action, bucket_set_id, bucket_id)
                    VALUES (%s, %s, %s, 'bucket.create', %s, %s)
                    """,
                    (str(uuid.uuid4()), operation_id, account_slot, set_id, bucket_id),
                )
            connection.commit()
        return _json_value(bucket)

    @staticmethod
    def _eligible_cte() -> str:
        return """
            WITH eligible AS (
                SELECT c.id, c.last_turn_at, c.client_surface,
                       COUNT(t.id)::INTEGER AS turn_count,
                       MAX(t.sequence)::BIGINT AS transcript_version,
                       (ARRAY_AGG(t.page_context ORDER BY t.sequence DESC))[1] AS page_context,
                       (ARRAY_AGG(t.app_version ORDER BY t.sequence DESC))[1]
                         AS app_version,
                       (ARRAY_AGG(t.prompt_policy_version ORDER BY t.sequence DESC))[1]
                         AS prompt_policy_version
                FROM conversations c
                JOIN conversation_turns t ON t.conversation_id = c.id
                WHERE c.capture_mode = 'transcript'
                  AND c.client_surface IN ('replica', 'wix')
                  AND c.expires_at > NOW()
                  AND c.last_turn_at <= NOW() - (%s * INTERVAL '1 second')
                GROUP BY c.id, c.last_turn_at, c.client_surface
                HAVING BOOL_AND(
                    t.status = 'complete'
                    AND t.privacy_state = 'clear'
                    AND t.review_state = 'ready'
                    AND (SELECT COUNT(*) FROM conversation_messages m WHERE m.turn_id = t.id) = 2
                )
            )
        """

    def list_conversations(self, account_slot: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        query = self._eligible_cte() + """
            SELECT e.id, e.last_turn_at, e.turn_count, e.transcript_version,
                   e.app_version, e.prompt_policy_version, e.client_surface,
                   COALESCE(e.page_context ->> 'title', 'Unknown page') AS page_title,
                   ce.bucket_id, COALESCE(ce.version, 0) AS evaluation_version
            FROM eligible e
            JOIN evaluation_bucket_sets s
              ON s.account_slot = %s AND s.archived_at IS NULL
            LEFT JOIN conversation_evaluations ce
              ON ce.bucket_set_id = s.id AND ce.conversation_id = e.id
            ORDER BY e.last_turn_at DESC, e.id
            LIMIT %s
        """
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(query, (self.min_inactive_seconds, SHARED_BUCKET_OWNER, limit))
                return [_json_value(dict(row)) for row in cursor.fetchall()]

    def get_conversation(self, account_slot: str, conversation_value: Any) -> dict:
        conversation_id = _uuid(conversation_value, "conversation_id")
        query = self._eligible_cte() + """
            SELECT e.id, e.last_turn_at, e.turn_count, e.transcript_version,
                   e.app_version, e.prompt_policy_version, e.client_surface,
                   COALESCE(e.page_context ->> 'title', 'Unknown page') AS page_title,
                   s.id AS bucket_set_id, ce.bucket_id, ce.note,
                   COALESCE(ce.version, 0) AS evaluation_version
            FROM eligible e
            JOIN evaluation_bucket_sets s
              ON s.account_slot = %s AND s.archived_at IS NULL
            LEFT JOIN conversation_evaluations ce
              ON ce.bucket_set_id = s.id AND ce.conversation_id = e.id
            WHERE e.id = %s
        """
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                cursor.execute(
                    query,
                    (self.min_inactive_seconds, SHARED_BUCKET_OWNER, conversation_id),
                )
                conversation = cursor.fetchone()
                if not conversation:
                    raise EvaluationForbidden("That conversation is not available for review.")
                set_id = str(conversation["bucket_set_id"])
                cursor.execute(
                    """
                    SELECT m.id, m.turn_id, m.ordinal, m.role, m.content,
                           m.created_at, t.app_version, t.prompt_policy_version
                    FROM conversation_messages m
                    JOIN conversation_turns t ON t.id = m.turn_id
                    WHERE m.conversation_id = %s
                    ORDER BY t.sequence, m.ordinal
                    """,
                    (conversation_id,),
                )
                messages = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT message_id, category, note, transcript_version, version
                    FROM conversation_annotations
                    WHERE bucket_set_id = %s AND conversation_id = %s
                    ORDER BY created_at, message_id
                    """,
                    (set_id, conversation_id),
                )
                annotations = [dict(row) for row in cursor.fetchall()]
        payload = dict(conversation)
        payload.pop("bucket_set_id", None)
        payload["messages"] = messages
        payload["annotations"] = annotations
        return _json_value(payload)

    def _current_transcript_version(self, cursor, conversation_id: str) -> int:
        cursor.execute(
            """
            SELECT MAX(t.sequence)::BIGINT AS transcript_version
            FROM conversations c
            JOIN conversation_turns t ON t.conversation_id = c.id
            WHERE c.id = %s AND c.capture_mode = 'transcript'
              AND c.client_surface IN ('replica', 'wix') AND c.expires_at > NOW()
              AND c.last_turn_at <= NOW() - (%s * INTERVAL '1 second')
            HAVING BOOL_AND(
                t.status = 'complete' AND t.privacy_state = 'clear'
                AND t.review_state = 'ready'
                AND (SELECT COUNT(*) FROM conversation_messages m WHERE m.turn_id = t.id) = 2
            )
            """,
            (conversation_id, self.min_inactive_seconds),
        )
        row = cursor.fetchone()
        if not row or row["transcript_version"] is None:
            raise EvaluationForbidden("That conversation is not available for review.")
        return int(row["transcript_version"])

    @staticmethod
    def _expected_versions(expected_value: Any, transcript_value: Any) -> tuple[int, int]:
        try:
            return max(0, int(expected_value)), max(0, int(transcript_value))
        except (TypeError, ValueError) as error:
            raise EvaluationValidation("Evaluation versions must be integers.") from error

    def save_note(
        self,
        account_slot: str,
        conversation_value: Any,
        note_value: Any,
        expected_version_value: Any,
        transcript_version_value: Any,
        operation_value: Any,
    ) -> dict:
        conversation_id = _uuid(conversation_value, "conversation_id")
        note = _reviewer_note(note_value, maximum=1000, label="Reviewer note")
        operation_id = _uuid(operation_value, "operation_id")
        expected_version, expected_transcript_version = self._expected_versions(
            expected_version_value, transcript_version_value
        )
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                set_id = self._bucket_set_id(cursor, account_slot)
                self._lock_review_record(cursor, set_id, conversation_id)
                actual_transcript_version = self._current_transcript_version(
                    cursor, conversation_id
                )
                cursor.execute(
                    """
                    SELECT bucket_id, note, transcript_version, version
                    FROM conversation_evaluations
                    WHERE bucket_set_id = %s AND conversation_id = %s
                    FOR UPDATE
                    """,
                    (set_id, conversation_id),
                )
                current = cursor.fetchone()
                current_payload = _json_value(dict(current)) if current else {
                    "bucket_id": None,
                    "note": None,
                    "transcript_version": actual_transcript_version,
                    "version": 0,
                }
                cursor.execute(
                    "SELECT 1 FROM evaluation_audit_events WHERE operation_id = %s",
                    (operation_id,),
                )
                if cursor.fetchone():
                    return current_payload
                if (
                    expected_version != int(current_payload["version"])
                    or expected_transcript_version != actual_transcript_version
                ):
                    raise EvaluationConflict(
                        "The conversation changed; refresh before saving the note.",
                        current_payload,
                    )
                next_version = expected_version + 1
                cursor.execute(
                    """
                    INSERT INTO conversation_evaluations (
                        bucket_set_id, conversation_id, bucket_id, note,
                        transcript_version, version, updated_by
                    ) VALUES (%s, %s, NULL, %s, %s, %s, %s)
                    ON CONFLICT (bucket_set_id, conversation_id) DO UPDATE SET
                        note = EXCLUDED.note,
                        transcript_version = EXCLUDED.transcript_version,
                        version = EXCLUDED.version,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    RETURNING bucket_id, note, transcript_version, version
                    """,
                    (
                        set_id, conversation_id, note, actual_transcript_version,
                        next_version, account_slot,
                    ),
                )
                evaluation = dict(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events (
                        id, operation_id, actor_slot, action,
                        bucket_set_id, conversation_id, metadata
                    ) VALUES (%s, %s, %s, 'conversation.note', %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()), operation_id, account_slot, set_id,
                        conversation_id,
                        self._jsonb({"present": bool(note), "length": len(note or "")}),
                    ),
                )
            connection.commit()
        return _json_value(evaluation)

    def save_annotation(
        self,
        account_slot: str,
        conversation_value: Any,
        message_value: Any,
        category_value: Any,
        note_value: Any,
        expected_version_value: Any,
        transcript_version_value: Any,
        operation_value: Any,
    ) -> dict | None:
        conversation_id = _uuid(conversation_value, "conversation_id")
        message_id = _uuid(message_value, "message_id")
        note = _reviewer_note(note_value, maximum=500, label="Annotation note")
        category = _annotation_category(category_value, allow_empty=not note)
        operation_id = _uuid(operation_value, "operation_id")
        expected_version, expected_transcript_version = self._expected_versions(
            expected_version_value, transcript_version_value
        )
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                set_id = self._bucket_set_id(cursor, account_slot)
                self._lock_review_record(
                    cursor, set_id, conversation_id, message_id
                )
                actual_transcript_version = self._current_transcript_version(
                    cursor, conversation_id
                )
                cursor.execute(
                    "SELECT 1 FROM conversation_messages "
                    "WHERE id = %s AND conversation_id = %s",
                    (message_id, conversation_id),
                )
                if not cursor.fetchone():
                    raise EvaluationForbidden("That message is not available for annotation.")
                cursor.execute(
                    """
                    SELECT message_id, category, note, transcript_version, version
                    FROM conversation_annotations
                    WHERE bucket_set_id = %s AND conversation_id = %s AND message_id = %s
                    FOR UPDATE
                    """,
                    (set_id, conversation_id, message_id),
                )
                current = cursor.fetchone()
                current_payload = _json_value(dict(current)) if current else {
                    "message_id": message_id,
                    "category": None,
                    "note": None,
                    "transcript_version": actual_transcript_version,
                    "version": 0,
                }
                cursor.execute(
                    "SELECT 1 FROM evaluation_audit_events WHERE operation_id = %s",
                    (operation_id,),
                )
                if cursor.fetchone():
                    return current_payload if current else None
                if (
                    expected_version != int(current_payload["version"])
                    or expected_transcript_version != actual_transcript_version
                ):
                    raise EvaluationConflict(
                        "The annotation changed; reopen the transcript before saving.",
                        current_payload,
                    )
                if category is None:
                    cursor.execute(
                        "DELETE FROM conversation_annotations "
                        "WHERE bucket_set_id = %s AND conversation_id = %s AND message_id = %s",
                        (set_id, conversation_id, message_id),
                    )
                    annotation = None
                else:
                    next_version = expected_version + 1
                    cursor.execute(
                        """
                        INSERT INTO conversation_annotations (
                            bucket_set_id, conversation_id, message_id, category, note,
                            transcript_version, version, updated_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (bucket_set_id, conversation_id, message_id) DO UPDATE SET
                            category = EXCLUDED.category,
                            note = EXCLUDED.note,
                            transcript_version = EXCLUDED.transcript_version,
                            version = EXCLUDED.version,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = NOW()
                        RETURNING message_id, category, note, transcript_version, version
                        """,
                        (
                            set_id, conversation_id, message_id, category, note,
                            actual_transcript_version, next_version, account_slot,
                        ),
                    )
                    annotation = dict(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events (
                        id, operation_id, actor_slot, action,
                        bucket_set_id, conversation_id, metadata
                    ) VALUES (%s, %s, %s, 'conversation.annotation', %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()), operation_id, account_slot, set_id,
                        conversation_id,
                        self._jsonb({
                            "message_id": message_id,
                            "category": category,
                            "present": category is not None,
                            "note_length": len(note or ""),
                        }),
                    ),
                )
            connection.commit()
        return _json_value(annotation) if annotation else None

    def move_conversation(
        self,
        account_slot: str,
        conversation_value: Any,
        bucket_value: Any,
        expected_version_value: Any,
        transcript_version_value: Any,
        operation_value: Any,
    ) -> dict:
        conversation_id = _uuid(conversation_value, "conversation_id")
        bucket_id = _uuid(bucket_value, "bucket_id") if bucket_value else None
        operation_id = _uuid(operation_value, "operation_id")
        try:
            expected_version = max(0, int(expected_version_value))
            expected_transcript_version = max(0, int(transcript_version_value))
        except (TypeError, ValueError) as error:
            raise EvaluationValidation("Evaluation versions must be integers.") from error
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=self._dict_row) as cursor:
                set_id = self._bucket_set_id(cursor, account_slot)
                self._lock_review_record(cursor, set_id, conversation_id)
                cursor.execute(
                    "SELECT 1 FROM evaluation_audit_events WHERE operation_id = %s",
                    (operation_id,),
                )
                repeated = cursor.fetchone() is not None
                if bucket_id:
                    cursor.execute(
                        "SELECT 1 FROM evaluation_buckets "
                        "WHERE id = %s AND bucket_set_id = %s AND archived_at IS NULL",
                        (bucket_id, set_id),
                    )
                    if not cursor.fetchone():
                        raise EvaluationValidation("That bucket is not available.")
                actual_transcript_version = self._current_transcript_version(
                    cursor, conversation_id
                )
                cursor.execute(
                    """
                    SELECT bucket_id, transcript_version, version
                    FROM conversation_evaluations
                    WHERE bucket_set_id = %s AND conversation_id = %s
                    FOR UPDATE
                    """,
                    (set_id, conversation_id),
                )
                current = cursor.fetchone()
                current_payload = _json_value(dict(current)) if current else {
                    "bucket_id": None,
                    "transcript_version": actual_transcript_version,
                    "version": 0,
                }
                if repeated:
                    return current_payload
                if (
                    expected_version != int(current_payload["version"])
                    or expected_transcript_version != actual_transcript_version
                ):
                    raise EvaluationConflict(
                        "The conversation changed; refresh before moving it.",
                        current_payload,
                    )
                next_version = expected_version + 1
                cursor.execute(
                    """
                    INSERT INTO conversation_evaluations (
                        bucket_set_id, conversation_id, bucket_id,
                        transcript_version, version, updated_by
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bucket_set_id, conversation_id) DO UPDATE SET
                        bucket_id = EXCLUDED.bucket_id,
                        transcript_version = EXCLUDED.transcript_version,
                        version = EXCLUDED.version,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    RETURNING bucket_id, transcript_version, version
                    """,
                    (
                        set_id,
                        conversation_id,
                        bucket_id,
                        actual_transcript_version,
                        next_version,
                        account_slot,
                    ),
                )
                evaluation = dict(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO evaluation_audit_events (
                        id, operation_id, actor_slot, action,
                        bucket_set_id, conversation_id, bucket_id
                    ) VALUES (%s, %s, %s, 'conversation.move', %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()), operation_id, account_slot,
                        set_id, conversation_id, bucket_id,
                    ),
                )
            connection.commit()
        return _json_value(evaluation)
