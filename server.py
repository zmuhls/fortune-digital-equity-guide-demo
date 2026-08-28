#!/usr/bin/env python3
"""Source-bounded Digital Equity Website Guide and local model proxy.

The browser receives no provider credential. A complete public-site index is
searched locally for each question, and only the most relevant approved
records are sent to Ollama Cloud. The server validates the model's source ID,
JSON contract, and privacy boundary, then preserves the model-authored answer.
"""

import collections
from datetime import date, datetime, timedelta, timezone
import http.server
import http.cookies
import json
import math
import mimetypes
import os
import pathlib
import re
import socketserver
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

from live_calendar import LiveCalendarCache

from conversation_store import (
    CaptureUnavailable,
    ConversationLimit,
    ConversationRecorder,
    IdempotencyConflict,
    SCHEMA_VERSION,
    response_with_ids,
)
from evaluation_store import (
    AuthenticationFailed,
    COOKIE_NAME,
    EVALUATION_SCHEMA_VERSION,
    EvaluationConflict,
    EvaluationForbidden,
    EvaluationStore,
    EvaluationUnavailable,
    EvaluationValidation,
)
from prompt_policy import (
    PROMPT_BEHAVIOR_RELEASE,
    PROMPT_POLICY_VERSION,
    RETRY_INSTRUCTIONS,
    build_retry_prompt,
)
from source_selector import ASK as SELECTOR_ASK
from source_selector import SYSTEM_PROMPT as SELECTOR_SYSTEM_PROMPT
from source_selector import build_prompt as build_selector_prompt
from source_selector import normalize_answer
from source_selector import parse_response as parse_selector_response


HERE = pathlib.Path(__file__).parent
PUBLIC_SITE_ROOT = HERE / "_site"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8790"))
MODEL = os.environ.get("FORTUNE_MODEL", os.environ.get("TOOLKIT_MODEL", "glm-5.2"))
KEY = os.environ.get("OLLAMA_API_KEY", "").strip()
ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get("FORTUNE_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}
MAX_BODY = 64 * 1024
MAX_HISTORY = 6
MAX_QUESTION_CHARS = 600
MAX_RETRIEVED = 10
MAX_MODEL_EXCERPT_CHARS = 700
MAX_MESSAGE_WORDS = 40
MAX_REASON_WORDS = 18
MAX_EVIDENCE_WORDS = 40
MAX_EVIDENCE_SENTENCES = 2
MODEL_SEED = 42
MODEL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["pick", "answer"],
    "additionalProperties": False,
}


class ModelResponseRejected(RuntimeError):
    """The provider replied, but no safe participant-facing answer survived validation."""

def bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


MODEL_CALLS_PER_HOUR = bounded_env_int(
    "FORTUNE_MODEL_CALLS_PER_HOUR",
    default=30,
    minimum=1,
    maximum=500,
)
MODEL_CALLS_PER_DAY = bounded_env_int(
    "FORTUNE_MODEL_CALLS_PER_DAY",
    default=300,
    minimum=1,
    maximum=5000,
)
CHAT_REQUESTS_PER_HOUR = bounded_env_int(
    "FORTUNE_CHAT_REQUESTS_PER_HOUR",
    default=120,
    minimum=10,
    maximum=1000,
)
CHAT_REQUESTS_PER_DAY = bounded_env_int(
    "FORTUNE_CHAT_REQUESTS_PER_DAY",
    default=2000,
    minimum=100,
    maximum=20000,
)
MODEL_WARMUP_COOLDOWN = bounded_env_int(
    "FORTUNE_MODEL_WARMUP_COOLDOWN",
    default=900,
    minimum=60,
    maximum=3600,
)
MODEL_KEEP_ALIVE = os.environ.get("FORTUNE_MODEL_KEEP_ALIVE", "30m").strip() or "30m"
CONVERSATION_RECORDER = ConversationRecorder(prompt_version=PROMPT_POLICY_VERSION)
EVALUATION_STORE = EvaluationStore()
EVALUATION_ASSETS = {
    "/evaluation": HERE / "evaluation.html",
    "/evaluation/": HERE / "evaluation.html",
    "/evaluation/index.html": HERE / "evaluation.html",
    "/evaluation/assets/evaluation.css": HERE / "evaluation.css",
    "/evaluation/assets/evaluation.js": HERE / "evaluation.js",
}

CONTACT_URL = "https://www.fortunedigitalequity.org/contact"
CALENDAR_URL = "https://www.fortunedigitalequity.org/calendar"
WORKSHOPS_URL = "https://www.fortunedigitalequity.org/workshops"
DEVICES_URL = "https://www.fortunedigitalequity.org/devices"
SUPPORT_URL = "https://www.fortunedigitalequity.org/support"
PRACTICE_URL = "https://www.fortunedigitalequity.org/practice"
ROOT_URL = "https://www.fortunedigitalequity.org/"

LEGACY_PATH_ALIASES = {
    "/about/partners": "/about",
    "/individual": "/support",
    "/reserve": "/calendar",
    "/trainings": "/workshops",
}

with (HERE / "knowledge.json").open(encoding="utf-8") as handle:
    KNOWLEDGE = json.load(handle)

SITE_INDEX_PATH = HERE / "site-index.json"
if SITE_INDEX_PATH.exists():
    with SITE_INDEX_PATH.open(encoding="utf-8") as handle:
        SITE_INDEX = json.load(handle)
else:
    SITE_INDEX = {
        "generated_at": None,
        "unique_urls": len(KNOWLEDGE["public_sources"]),
        "authority_counts": {"answer": len(KNOWLEDGE["public_sources"])},
        "pages": [],
    }


def canonical_url(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.hostname not in {"fortunedigitalequity.org", "www.fortunedigitalequity.org"}:
        return ""
    path = parsed.path.rstrip("/") or "/"
    path = LEGACY_PATH_ALIASES.get(path, path)
    return urllib.parse.urlunsplit(("https", "www.fortunedigitalequity.org", path, "", ""))


def origin_is_allowed(origin, host):
    origin = str(origin or "").rstrip("/")
    host = str(host or "").strip()
    if not origin:
        return True
    same_origin = {f"http://{host}", f"https://{host}"} if host else set()
    return origin in ALLOWED_ORIGINS or origin in same_origin


class ModelCallBudget:
    """Bound public model use without retaining questions or chat history."""

    def __init__(self, per_hour, per_day, clock=time.time):
        self.per_hour = per_hour
        self.per_day = per_day
        self.clock = clock
        self._lock = threading.Lock()
        self._hourly = collections.defaultdict(collections.deque)
        self._day = None
        self._daily_count = 0

    def claim(self, client_id):
        now = self.clock()
        day = int(now // 86400)
        client_id = str(client_id or "unknown")[:200]
        with self._lock:
            if day != self._day:
                self._day = day
                self._daily_count = 0
                self._hourly.clear()
            recent = self._hourly[client_id]
            cutoff = now - 3600
            while recent and recent[0] <= cutoff:
                recent.popleft()
            if len(recent) >= self.per_hour or self._daily_count >= self.per_day:
                return False
            recent.append(now)
            self._daily_count += 1
            return True


MODEL_CALL_BUDGET = ModelCallBudget(MODEL_CALLS_PER_HOUR, MODEL_CALLS_PER_DAY)
CHAT_REQUEST_BUDGET = ModelCallBudget(CHAT_REQUESTS_PER_HOUR, CHAT_REQUESTS_PER_DAY)
LOGIN_REQUEST_BUDGET = ModelCallBudget(20, 500)


class ModelWarmup:
    """Load the model once per cooldown and collapse concurrent warm-up calls."""

    def __init__(self, cooldown, clock=time.monotonic, wait_timeout=120):
        self.cooldown = cooldown
        self.clock = clock
        self.wait_timeout = wait_timeout
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._last_ready = None
        self._in_flight = False

    def status(self):
        with self._lock:
            if self._in_flight:
                return "warming"
            if self._last_ready is not None and self.clock() - self._last_ready < self.cooldown:
                return "ready"
            return "idle"

    def mark_ready(self):
        with self._lock:
            self._last_ready = self.clock()

    def ensure(self, loader):
        with self._lock:
            if self._last_ready is not None and self.clock() - self._last_ready < self.cooldown:
                return False
            if self._in_flight:
                event = self._event
                owns_load = False
            else:
                self._in_flight = True
                self._event = threading.Event()
                event = self._event
                owns_load = True

        if owns_load:
            try:
                loader()
            except Exception:
                with self._lock:
                    self._in_flight = False
                    event.set()
                raise
            with self._lock:
                self._last_ready = self.clock()
                self._in_flight = False
                event.set()
            return True

        if not event.wait(self.wait_timeout):
            raise RuntimeError("Model warm-up timed out")
        with self._lock:
            if self._last_ready is None or self.clock() - self._last_ready >= self.cooldown:
                raise RuntimeError("Model warm-up did not finish")
        return False


MODEL_WARMUP = ModelWarmup(MODEL_WARMUP_COOLDOWN)


def ollama_request(payload):
    request = urllib.request.Request(
        "https://ollama.com/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error.read()
        raise RuntimeError("Ollama Cloud returned an error") from error


def preload_model():
    """Send Ollama's documented empty request and retain the loaded model."""

    ollama_request({
        "model": MODEL,
        "stream": False,
        "keep_alive": MODEL_KEEP_ALIVE,
    })


def warm_model_quietly():
    if not KEY:
        return
    try:
        MODEL_WARMUP.ensure(preload_model)
    except Exception:
        pass


def warm_calendar_quietly():
    calendar = SOURCE_BY_ID.get("calendar")
    if calendar:
        CALENDAR_CACHE.refresh(calendar)


def calendar_static_blocks(blocks):
    """Remove the flattened agenda; dated rows are retained separately."""

    result = []
    skipping_agenda = False
    for block in blocks:
        value = str(block or "").strip()
        if value == "Regular Class Schedule":
            skipping_agenda = True
            continue
        if skipping_agenda and value == "Downloads":
            skipping_agenda = False
        if not skipping_agenda:
            result.append(block)
    return result


def build_sources():
    sources = {}
    id_by_url = {}
    for reviewed in KNOWLEDGE["public_sources"]:
        source = dict(reviewed)
        source.update({
            "authority": "answer",
            "authority_reason": "staff-reviewed compact source record",
            "description": "",
            "headings": [],
            "blocks": list(reviewed.get("facts", [])),
            "internal_links": [],
            "status": 200,
            "volatile": bool(reviewed.get("volatile_fields")),
            "sitemap_kind": "reviewed",
        })
        source["url"] = canonical_url(source["url"])
        sources[source["id"]] = source
        id_by_url[source["url"]] = source["id"]

    for page in SITE_INDEX.get("pages", []):
        url = canonical_url(page.get("url"))
        if not url:
            continue
        reviewed_id = id_by_url.get(url)
        if reviewed_id:
            source = sources[reviewed_id]
            source["description"] = page.get("description", "")
            source["headings"] = page.get("headings", [])
            page_blocks = list(page.get("blocks", []))
            if reviewed_id == "calendar":
                page_blocks = calendar_static_blocks(page_blocks)
                source["calendar_events"] = list(page.get("calendar_events", []))
                source["source_captured_at"] = page.get("source_captured_at")
            source["blocks"] = list(source["blocks"]) + page_blocks
            source["internal_links"] = page.get("internal_links", [])
            source["lastmod"] = page.get("lastmod", "")
            source["site_index_id"] = page.get("id")
            continue
        source = dict(page)
        source["url"] = url
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            continue
        sources[source_id] = source
        id_by_url[url] = source_id
    return sources, id_by_url


SOURCE_BY_ID, SOURCE_ID_BY_URL = build_sources()
CALENDAR_CACHE = LiveCalendarCache(
    ttl_seconds=bounded_env_int(
        "FORTUNE_CALENDAR_REFRESH_SECONDS",
        default=900,
        minimum=60,
        maximum=86400,
    )
)
ANSWER_SOURCES = [
    source for source in SOURCE_BY_ID.values()
    if source.get("authority") == "answer" and source.get("status", 200) == 200
]

_REASONING_BLOCK = re.compile(
    r"<think\b[^>]*>.*?</think\s*>"
    r"|<thinking\b[^>]*>.*?</thinking\s*>"
    r"|◁think▷.*?◁/think▷",
    re.IGNORECASE | re.DOTALL,
)
_CLOSE_TAG = re.compile(r"</think\s*>|</thinking\s*>|◁/think▷", re.IGNORECASE)
_ORPHAN_OPEN = re.compile(r"<think\b[^>]*>|<thinking\b[^>]*>|◁think▷", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?")
_VISUAL_SCAFFOLD = (
    re.compile(r"^icon representing\b", re.I),
    re.compile(r"^(?:image|photo|photograph)\s+(?:of|showing)\b", re.I),
    re.compile(r"^qr ?code\b", re.I),
    re.compile(r"^ended\s+ended\b", re.I),
    re.compile(r"^your content has been submitted\b", re.I),
    re.compile(r"^submit another question\b", re.I),
    re.compile(r"^an error occurred\b", re.I),
    re.compile(r"^a digital navigator helping\b", re.I),
    re.compile(r"^participant being helped\b", re.I),
    re.compile(r"^the crowd at the annual fortune society tech fair\b", re.I),
    re.compile(r"^.+\s+badge$", re.I),
    re.compile(r"^.+\s+clip art$", re.I),
    re.compile(r"^.+\.(?:gif|jpe?g|png|webp)$", re.I),
)

_PERSONAL_PATTERNS = [
    re.compile(
        r"\b(?:social security|ssn|date of birth|dob|password|passcode|my health|my diagnosis)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:my|their|participant'?s?)\s+(?:fortune\s+)?"
        r"(?:id|case number|name|address|phone|email)\b",
        re.I,
    ),
    re.compile(r"(?<!\d)\d{3}(?:[-‐‑‒–—.\s]?\d{3})(?!\d)"),
    re.compile(r"\b\d{3}[-. ]?\d{2}[-. ]?\d{4}\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b(?:my phone|call me at|my number is)\b", re.I),
    re.compile(r"\b(?:my address is|i live at)\b", re.I),
]

_HUMAN_HANDOFF_PATTERNS = [
    re.compile(r"\b(?:parole|probation|legal|lawyer|attorney|court|case-specific|case manager)\b", re.I),
    re.compile(r"\b(?:housing|shelter|health|medical|doctor|benefits|snap|medicaid)\b", re.I),
    re.compile(r"\b(?:emergency|crisis|unsafe|suicid(?:e|al)|self-harm|hurt myself|harm someone)\b", re.I),
]

_SPANISH_MARKERS = {
    "abre", "ayuda", "clase", "clases", "como", "computadora", "computadoras",
    "confirmarlo", "con", "curso", "donde", "encontre", "espanol", "favor", "gracias", "hola",
    "informacion", "necesito", "para", "pagina", "personal", "por", "puedo",
    "publicas", "pude", "que", "quiero", "registrarme", "telefono", "una",
}
_ENGLISH_MARKERS = {
    "class", "classes", "computer", "device", "do", "help", "how", "internet",
    "laptop", "need", "phone", "please", "register", "thanks", "the", "want",
    "what", "where", "with",
}

PARTICIPANT_COPY = {
    "en": {
        "privacy_message": "Remove personal information and try again.",
        "privacy_reason": "Use Contact for personal help.",
    },
    "es": {
        "privacy_message": "Quita los datos personales e inténtalo de nuevo.",
        "privacy_reason": "Usa Contacto para ayuda personal.",
    },
}

STOPWORDS = {
    "a", "about", "am", "an", "and", "are", "at", "be", "can", "could",
    "do", "does", "for", "from", "get", "have", "help", "here", "how", "i",
    "in", "info", "information", "is", "it", "me", "my", "of", "on", "or",
    "page", "please", "provide", "show", "something", "tell", "the", "there",
    "this", "to", "want", "what", "when", "where", "which", "with", "would", "you",
}

CORE_IDS = [source_id for source_id in ("home", "trainings", "devices", "individual", "calendar", "contact") if source_id in SOURCE_BY_ID]


def source_id_for_path(path):
    return SOURCE_ID_BY_URL.get(canonical_url(ROOT_URL.rstrip("/") + path), "")


CERTIFICATIONS_ID = source_id_for_path("/certifications")
ASSESSMENTS_ID = source_id_for_path("/assessments")
PARTNERS_ID = source_id_for_path("/about")
IMPACT_ID = source_id_for_path("/about/impact")
INTRO_EMAIL_ID = source_id_for_path("/service-page/intro-to-email")
INTRO_EXCEL_ID = source_id_for_path("/service-page/intro-to-microsoft-excel")
UNDERSTANDING_COMPUTERS_ID = source_id_for_path("/service-page/understanding-computers")
CANVA_DESIGN_TOOLS_ID = source_id_for_path("/service-page/canva-design-tools")
NAVIGATING_SMARTPHONE_ID = source_id_for_path("/service-page/navigating-your-smartphone")
MANAGING_SMARTPHONE_ID = source_id_for_path("/service-page/managing-your-smartphone")
EXCEL_PRESENTING_ID = source_id_for_path("/service-page/excel-presenting-data")
EXCEL_FORMULAS_ID = source_id_for_path("/service-page/excel-formulas-functions")
EXCEL_FORMATTING_ID = source_id_for_path("/service-page/excel-formatting-data")
EXCEL_ORGANIZING_ID = source_id_for_path("/service-page/excel-organizing-data")
DIGITAL_SAFETY_COMPUTERS_ID = source_id_for_path("/service-page/digital-safety-computers")
DIGITAL_SAFETY_EMAIL_ID = source_id_for_path("/service-page/digital-safety-email")
DIGITAL_SAFETY_MOBILE_ID = source_id_for_path("/service-page/digital-safety-mobile-devices")
DIGITAL_SAFETY_ONLINE_ID = source_id_for_path("/service-page/digital-safety-online")
JOB_SEARCH_ID = source_id_for_path("/service-page/job-searching-online")
TECH_FAIR_QA_ID = source_id_for_path("/techfair/qa")
PRACTICE_ID = source_id_for_path("/practice")
SPANISH_BASIC_ID = source_id_for_path("/service-page/alfabetización-digital-básica-en-español")

# Stable semantic names used by the conversation tests and routing rules now
# point at the current public pages.  The older route slugs disappeared from
# Wix revision 2063; these aliases do not reintroduce them as destinations.
INTRO_COMPUTERS_ID = UNDERSTANDING_COMPUTERS_ID
INTRO_CANVA_ID = CANVA_DESIGN_TOOLS_ID
INTRO_SMARTPHONE_ID = NAVIGATING_SMARTPHONE_ID
SMARTPHONE_PART_TWO_ID = MANAGING_SMARTPHONE_ID
WORD_CERTIFICATION_ID = CERTIFICATIONS_ID
EXCEL_CHARTS_ID = EXCEL_PRESENTING_ID

SPECIFIC_CLASS_TERMS = {
    "advanced", "ai", "alfabetizacion", "android", "apple", "assessment",
    "assessments", "basic", "beginner", "canva", "certification",
    "certifications", "chart", "charts", "computacion", "correo", "email",
    "electronico", "excel", "formula", "formulas", "job", "microsoft",
    "phone", "powerpoint", "practice", "resume", "robotics", "safety",
    "scam", "scams", "smartphone", "smartphones", "spanish", "spreadsheet",
    "spreadsheets", "word", "zoom",
}

HISTORY_TOPIC_TERMS = SPECIFIC_CLASS_TERMS.union({
    "appointment", "device", "devices", "digital", "distribution", "eligibility",
    "equity", "impact", "individual", "internet", "laptop", "lifeline",
    "organizing", "partners", "program", "programs", "support", "tech",
    "tutoring", "wifi",
})


def fold_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in normalized if not unicodedata.combining(character)).lower()


_QUESTION_ALIASES = {
    "emails": "email",
    "halp": "help",
    "labtop": "laptop",
    "labtops": "laptops",
    "resumes": "resume",
    "lern": "learn",
    "computr": "computer",
    "whare": "where",
}


def semantic_question(value):
    """Return the participant's useful intent without markup or prompt attacks."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\balert\s*\([^)]*\)\s*;?", " ", text, flags=re.I)
    text = re.sub(
        r"\bignore\s+(?:all\s+|any\s+|the\s+|your\s+|previous\s+|these\s+)*"
        r"(?:rules?|instructions?|prompts?)\s*(?:and|,|\.)?\s*",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(?:tell|show|reveal|give)\s+(?:me\s+)?(?:the\s+)?(?:your\s+)?"
        r"(?:hidden\s+|system\s+|developer\s+|internal\s+)*(?:prompt|instructions?|rules?)\b",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\binvent\b", " ", text, flags=re.I)
    text = re.sub(r"\bone[- ]on[- ]one\b", "one-to-one", text, flags=re.I)
    for typo, replacement in _QUESTION_ALIASES.items():
        text = re.sub(rf"\b{re.escape(typo)}\b", replacement, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ,.;:!?-")


def tokens(value, keep_stopwords=False):
    values = _TOKEN.findall(fold_text(value))
    values = [value[:-2] if value.endswith("'s") else value for value in values]
    values = [_QUESTION_ALIASES.get(item, item) for item in values]
    if keep_stopwords:
        return values
    return [value for value in values if len(value) > 1 and value not in STOPWORDS]


_QUERY_TERM_GROUPS = (
    frozenset({"address", "addresses"}),
    frozenset({"background", "experience"}),
    frozenset({"calendar", "hours", "schedule"}),
    frozenset({"class", "classes"}),
    frozenset({"device", "devices"}),
    frozenset({"eligible", "eligibility", "qualify", "qualified", "requirements"}),
    frozenset({"format", "formatting", "technique", "techniques"}),
    frozenset({"laptop", "laptops"}),
    frozenset({"phone", "phones", "smartphone", "smartphones"}),
    frozenset({"register", "registered", "registering", "registration"}),
    frozenset({"skill", "skills"}),
    frozenset({"sort", "sorted", "sorting"}),
    frozenset({"filter", "filtered", "filtering", "filters"}),
    frozenset({"table", "tables"}),
    frozenset({"duplicate", "duplicates"}),
    frozenset({"workshop", "workshops"}),
)


def expanded_query_terms(value):
    terms = set(tokens(value))
    for group in _QUERY_TERM_GROUPS:
        if terms.intersection(group):
            terms.update(group)
    return terms


_SOURCE_BOILERPLATE_PHRASES = (
    "double click on the text box",
    "this space is a great opportunity",
    "every website has a story",
    "use tab to navigate",
    "loading days",
    "book now",
)
_SOURCE_BOILERPLATE_EXACT = {
    "ashley jones",
    "don francis",
    "filler",
    "finding inspiration in every turn",
    "founder & ceo",
    "our clients",
    "our story",
    "tech lead",
}


def is_source_boilerplate(value):
    folded = fold_text(str(value or "")).strip(" .")
    return (
        folded in _SOURCE_BOILERPLATE_EXACT
        or any(phrase in folded for phrase in _SOURCE_BOILERPLATE_PHRASES)
    )


def source_has_template_content(source):
    return any(
        is_source_boilerplate(value)
        for value in source.get("blocks", [])
    )


def source_is_placeholder_template(source):
    folded = fold_text("\n".join(str(value) for value in source.get("blocks", [])))
    return (
        "every website has a story" in folded
        and "double click on the text box" in folded
        and ("don francis" in folded or "ashley jones" in folded)
    )


def searchable_text(source):
    values = [source.get("title", ""), source.get("description", "")]
    values.extend(source.get("headings", []))
    values.extend(source.get("facts", []))
    values.extend(source.get("blocks", []))
    values.extend(
        event.get("label", "")
        for event in source.get("calendar_events", [])
        if isinstance(event, dict)
    )
    template_contaminated = source_has_template_content(source)
    return " ".join(
        str(value)
        for value in values
        if not is_source_boilerplate(value)
        and not (
            template_contaminated
            and fold_text(str(value)).strip() in {"about us", "meet the team"}
        )
    )


RETRIEVABLE_SOURCES = [
    source for source in ANSWER_SOURCES
    if not source_is_placeholder_template(source)
]
SOURCE_TERMS = {
    source["id"]: collections.Counter(tokens(searchable_text(source)))
    for source in RETRIEVABLE_SOURCES
}
DOCUMENT_FREQUENCY = collections.Counter()
for source_terms in SOURCE_TERMS.values():
    DOCUMENT_FREQUENCY.update(source_terms.keys())


def strip_reasoning(text):
    if not text:
        return text
    cleaned = _REASONING_BLOCK.sub("", text)
    closes = list(_CLOSE_TAG.finditer(cleaned))
    if closes:
        cleaned = cleaned[closes[-1].end():]
    return _ORPHAN_OPEN.sub("", cleaned).strip()


def clean_evidence_fragment(text):
    """Remove visual-only page scaffolding before it reaches an answer."""

    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"^[*#]+\s*", "", value)
    value = re.sub(r"^(?:and|but|however),?\s+", "", value, flags=re.I)
    if (
        not value
        or "©" in value
        or fold_text(value).startswith("copyright ")
        or is_source_boilerplate(value)
        or any(pattern.search(value) for pattern in _VISUAL_SCAFFOLD)
    ):
        return ""
    return value


def contains_personal_details(text):
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return any(pattern.search(normalized) for pattern in _PERSONAL_PATTERNS)


def needs_human_handoff(text):
    return any(pattern.search(text or "") for pattern in _HUMAN_HANDOFF_PATTERNS)


def detect_language(text):
    """Return a coarse, non-sensitive language hint for response routing."""

    value = fold_text(text)
    words = set(tokens(value, keep_stopwords=True))
    if not words:
        return (
            "other"
            if any(character.isalpha() and ord(character) > 127 for character in str(text or ""))
            else "und"
        )
    spanish_score = len(words.intersection(_SPANISH_MARKERS))
    english_score = len(words.intersection(_ENGLISH_MARKERS))
    if re.search(r"[¿¡ñ]", str(text or "").lower()) or spanish_score > english_score:
        return "es"
    if english_score or re.fullmatch(r"[\x00-\x7f\s\W]+", str(text or "")):
        return "en"
    return "other"


def participant_copy(key, language_code):
    language = "es" if language_code == "es" else "en"
    return PARTICIPANT_COPY[language][key]


def request_kind(question):
    question = semantic_question(question)
    if contains_personal_details(question):
        return "privacy"
    if needs_human_handoff(question):
        return "sensitive"
    return "retrieval"


def interaction_context(question, history=None):
    history = list(history or [])
    return {
        "chat_stage": "follow_up" if any(item.get("role") == "user" for item in history) else "opening",
        "request_kind": request_kind(question),
        "request_language": detect_language(question),
        "prompt_policy_version": PROMPT_POLICY_VERSION,
    }


def clip_words(text, limit):
    normalized = re.sub(r"\s*[—–]\s*", ", ", str(text or ""))
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    words = normalized.strip().split()
    if len(words) <= limit:
        return " ".join(words)
    prefix = " ".join(words[:limit])
    endings = list(re.finditer(r"[.!?](?:[\"']?)(?=\s|$)", prefix))
    if endings:
        sentence = prefix[:endings[-1].end()].strip()
        if len(sentence.split()) >= max(12, int(limit * 0.4)):
            return sentence
    return prefix.rstrip(".,;:") + "…"


def clip_evidence_chars(text, limit):
    """Fit source evidence to a character budget without dropping the block."""

    value = str(text or "").strip()
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit < 40:
        return ""
    prefix = value[:limit].rstrip()
    endings = list(re.finditer(r"[.!?](?:[\"']?)(?=\s|$)", prefix))
    if endings and endings[-1].end() >= max(80, int(limit * 0.45)):
        return prefix[:endings[-1].end()].strip()
    if " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip(".,;:") + "…"


def query_focused_evidence_fragment(text, query_terms, limit):
    """Keep query-matching source sentences when a long block must be clipped."""

    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    sentences = [
        sentence.strip()
        for sentence in re.split(
            r"(?<!a\.m\.)(?<!p\.m\.)(?<=[.!?])\s+",
            value,
            flags=re.I,
        )
        if sentence.strip()
    ]
    ranked = []
    for index, sentence in enumerate(sentences):
        overlap = len(set(query_terms).intersection(expanded_query_terms(sentence)))
        if overlap:
            ranked.append((overlap, -index, index, sentence))
    ranked.sort(reverse=True)
    if ranked and ranked[0][0] >= 2:
        chosen = []
        used = 0
        for _, _, index, sentence in ranked:
            separator = 1 if chosen else 0
            remaining = limit - used - separator
            if remaining <= 0:
                break
            fragment = clip_evidence_chars(sentence, remaining)
            if not fragment:
                continue
            chosen.append((index, fragment))
            used += separator + len(fragment)
        if chosen:
            return "\n".join(fragment for _, fragment in sorted(chosen))
    return clip_evidence_chars(value, limit)


def device_use_support_intent(text):
    """Distinguish help using a device from requests to obtain one."""

    value = fold_text(semantic_question(text))
    device_terms = {
        "android", "apple", "computer", "computadora", "device", "dispositivo",
        "ipad", "laptop", "phone", "smartphone", "tablet", "telefono",
    }
    words = set(tokens(value, keep_stopwords=True))
    if not words.intersection(device_terms):
        return False
    return bool(
        re.search(
            r"\b(?:help (?:me )?(?:use|using|with)|learn(?:ing)? (?:how )?to use|"
            r"teach (?:me )?(?:how )?to use|using|set ?up|navigate|personalize|"
            r"troubleshoot|not working|problem|issue|repair|replace|cracked|"
            r"screen|fix|broken)\b",
            value,
        )
    )


def individual_support_intent(text):
    """Recognize explicit requests for Fortune's one-to-one support options."""

    value = fold_text(semantic_question(text))
    return bool(re.search(
        r"\b(?:one-to-one|1-on-1|individual (?:help|support)|tutor(?:ing)?|"
        r"office hours?|support desk|open (?:computer )?lab)\b",
        value,
    ))


def device_distribution_intent(text):
    """Recognize named device programs without treating generic enrollment as one."""

    value = fold_text(semantic_question(text))
    if re.search(
        r"\b(?:acp|affordable connectivity program|lifeline|computers 4 people|"
        r"device distribution|mobile distribution|phone service|free (?:smart)?phones?|"
        r"smartphone distribution|laptop referral)\b",
        value,
    ):
        return True
    words = set(tokens(value, keep_stopwords=True))
    return bool(
        words.intersection({
            "computer", "device", "laptop", "phone", "phones", "smartphone", "smartphones",
        })
        and words.intersection({
            "available", "eligible", "free", "get", "obtain", "qualify", "receive",
        })
    )


def content_detail_intent(text):
    """Recognize follow-ups asking for concrete topics rather than a page summary."""

    value = fold_text(semantic_question(text))
    return bool(re.search(
        r"\b(?:what (?:would|will|do) i learn|what does (?:it|that|the .+?) cover|"
        r"what (?:is|are) (?:covered|the topics?)|what does (?:it|that) teach)\b",
        value,
    ))


def retired_class_intent(text):
    """Recognize named class routes that are absent from the current Wix site."""

    value = fold_text(semantic_question(text))
    words = set(tokens(value, keep_stopwords=True))
    resume_class = "resume" in words and "ai" in words
    pivot_class = (
        bool(words.intersection({"pivot", "pivottable", "pivottables"}))
        and bool(words.intersection({"class", "course", "training", "workshop"}))
        and not bool(words.intersection({"certification", "certifications", "certified"}))
    )
    return resume_class or pivot_class


def registration_intent(text):
    """Recognize an explicit request to register or reserve a class."""

    value = fold_text(semantic_question(text))
    return bool(re.search(
        r"\b(?:register|registered|registering|registration|sign up|reserve|"
        r"registrarme|registro|inscribirme)\b",
        value,
    ))


def schedule_intent(text):
    """Recognize dates, locations, and operating hours, not class duration."""

    value = fold_text(semantic_question(text))
    words = set(tokens(value, keep_stopwords=True))
    if re.search(r"\b(?:how many hours?|how long|duration|class length)\b", value):
        return False
    return bool(re.search(
        r"\b(?:calendar|current dates?|class dates?|this week|next class|"
        r"when (?:is|are|does|do|will)|hours?|schedule|locations?|where is|"
        r"calendario|fechas?|cuando|donde)\b",
        value,
    )) or bool(
        words.intersection({"today", "tomorrow", "tonight"})
        and words.intersection({
            "calendar", "class", "classes", "course", "courses", "event", "events",
            "session", "sessions", "training", "trainings", "workshop", "workshops",
        })
    )


def exact_named_source_ids(text):
    """Resolve a public page title named in the question before broad directories.

    This is title-based routing only: it selects a current approved record and
    never supplies participant-facing factual prose.
    """

    value = " ".join(tokens(fold_text(semantic_question(text)), keep_stopwords=True))
    matches = []
    for source in RETRIEVABLE_SOURCES:
        title = re.sub(
            r"\s*[|·]\s*FS Digital Equity\s*$",
            "",
            str(source.get("title") or ""),
            flags=re.I,
        )
        title_value = " ".join(tokens(fold_text(title), keep_stopwords=True))
        title_terms = title_value.split()
        if len(title_terms) < 2:
            continue
        aliases = {title_value, title_value.replace(" and ", " ")}
        if any(re.search(rf"(?:^| )({re.escape(alias)})(?: |$)", value) for alias in aliases):
            matches.append((len(title_terms), source["id"]))
    matches.sort(key=lambda row: (-row[0], row[1]))
    return [source_id for _, source_id in matches]


def likely_source_ids(text, fallback=True):
    lowered = fold_text(semantic_question(text))
    word_set = set(tokens(lowered, keep_stopwords=True))
    retired_class = retired_class_intent(lowered)
    ranked = []
    def add(source_id):
        if source_id and source_id in SOURCE_BY_ID and source_id not in ranked:
            ranked.append(source_id)

    # Action-specific routes come first. A real schedule or registration
    # request can supersede a named class because those details live on the
    # calendar/contact pages.
    if registration_intent(lowered):
        add("contact")
        add("calendar")
    if schedule_intent(lowered):
        add("calendar")

    # An exact public title outranks broad program, support, and directory
    # language. This remains routing only; all visible facts come from the
    # selected current source record at model time.
    for source_id in exact_named_source_ids(lowered):
        add(source_id)

    if (
        word_set.intersection({"program", "programs"})
        and word_set.intersection({"describe", "does", "offer", "offers", "overview", "provide", "provides"})
    ):
        add("home")
    if device_use_support_intent(lowered):
        add("individual")
    if individual_support_intent(lowered):
        add("individual")
    if device_distribution_intent(lowered):
        add("devices")
    if retired_class:
        add("trainings")
        add("contact")
    spreadsheet_terms = word_set.intersection({
        "excel", "spreadsheet", "spreadsheets", "worksheet", "worksheets",
    })
    if spreadsheet_terms and not retired_class:
        formatting_focus = word_set.intersection({
            "border", "borders", "cell", "cells", "currency", "date", "dates",
            "format", "formatting", "number", "numbers", "percent", "percentage",
            "percentages", "read", "readable", "style", "styles",
        })
        organizing_focus = word_set.intersection({
            "duplicate", "duplicates", "filter", "organize", "organizing", "record",
            "records", "sort", "sorting",
        })
        presenting_focus = word_set.intersection({
            "chart", "charts", "layout", "layouts", "pdf", "print", "printing",
            "scale", "scaling", "sparkline", "sparklines", "visual", "visuals",
        })
        formula_focus = word_set.intersection({"formula", "formulas", "function", "functions"})
        focus_groups = [formatting_focus, organizing_focus, presenting_focus, formula_focus]
        focused_count = sum(bool(group) for group in focus_groups)
        # Preserve every explicitly named Excel topic as a model candidate.
        # A question can intentionally compare two classes; sending it only
        # the generic directory makes that comparison less grounded.
        if formatting_focus:
            add(EXCEL_FORMATTING_ID)
        if organizing_focus:
            add(EXCEL_ORGANIZING_ID)
        if presenting_focus:
            add(EXCEL_PRESENTING_ID)
        if formula_focus:
            add(EXCEL_FORMULAS_ID)
        if (
            focused_count == 0
            and word_set.intersection({"beginner", "basic", "basics", "intro", "introduction", "new", "start", "starting"})
        ):
            add(INTRO_EXCEL_ID)
        if focused_count == 0 and not word_set.intersection({
            "present", "presenting", "sort", "sorting", "duplicate", "duplicates",
            "filter", "read",
        }):
            add(INTRO_EXCEL_ID)
    if word_set.intersection({"scam", "scams", "fraud", "phishing"}):
        if word_set.intersection({"email", "correo", "electronico"}):
            add(DIGITAL_SAFETY_EMAIL_ID)
        elif word_set.intersection({"mobile", "phone", "smartphone", "telefono"}):
            add(DIGITAL_SAFETY_MOBILE_ID)
        elif word_set.intersection({"computer", "computadora"}):
            add(DIGITAL_SAFETY_COMPUTERS_ID)
        else:
            add(DIGITAL_SAFETY_ONLINE_ID)
            add(DIGITAL_SAFETY_EMAIL_ID)
    if word_set.intersection({"attachment", "attachments", "adjunto", "adjuntos"}):
        add(INTRO_EMAIL_ID)
    if "word" in word_set and word_set.intersection({"certification", "certifications", "certified"}):
        add(WORD_CERTIFICATION_ID)
    if word_set.intersection({"certification", "certifications", "certified"}):
        add(CERTIFICATIONS_ID)
    if (
        word_set.intersection({"smartphone", "smartphones", "android", "apple", "phone"})
        and word_set.intersection({"after", "next", "part", "two"})
    ):
        add(SMARTPHONE_PART_TWO_ID)
    if (
        word_set.intersection({"email", "correo", "electronico"})
        and word_set.intersection({"advanced", "after", "next", "organize", "folders", "templates"})
    ):
        add(INTRO_EMAIL_ID)
        add("trainings")
    if (
        word_set.intersection({"email", "correo", "electronico"})
        and word_set.intersection({"beginning", "beginner", "basic", "intro", "introduction"})
    ):
        add(INTRO_EMAIL_ID)
    if (
        "computer" in word_set
        and word_set.intersection({"barely", "beginner", "basic", "intro", "introduction", "starting"})
    ):
        add(INTRO_COMPUTERS_ID)
    if (
        "canva" in word_set
        and "design" in word_set
        and word_set.intersection({"background", "experience", "prior"})
    ):
        add(CANVA_DESIGN_TOOLS_ID)
    if "canva" in word_set and word_set.intersection({"beginner", "intro", "introduction"}):
        add(INTRO_CANVA_ID)
    if (
        word_set.intersection({"smartphone", "smartphones", "phone"})
        and word_set.intersection({"beginner", "class", "learn", "learning", "new"})
    ):
        add(INTRO_SMARTPHONE_ID)
    if "chart" in word_set or "charts" in word_set:
        add(EXCEL_CHARTS_ID)
    if "excel" in word_set and word_set.intersection({"formula", "formulas"}):
        add(EXCEL_FORMULAS_ID)
    if (
        "excel" in word_set
        and word_set.intersection({"date", "dates", "number", "numbers", "readable"})
    ):
        add(EXCEL_FORMATTING_ID)
    if (
        "excel" in word_set
        and word_set.intersection({"duplicate", "duplicates", "record", "records", "sorting"})
    ):
        add(EXCEL_ORGANIZING_ID)
    if "job" in word_set and word_set.intersection({"search", "searching", "online"}):
        add(JOB_SEARCH_ID)
    if "assessment" in word_set or "assessments" in word_set:
        add(ASSESSMENTS_ID)
        if not ASSESSMENTS_ID:
            add("trainings")
            add("contact")
    if "practice" in word_set and word_set.intersection({"class", "exercise", "exercises", "skill", "skills"}):
        add(PRACTICE_ID)
    if "partner" in word_set or "partners" in word_set:
        add(PARTNERS_ID)
    if "impact" in word_set:
        add(IMPACT_ID)
    if "team" in word_set and word_set.intersection({"digital", "equity"}):
        add(PARTNERS_ID)
    if (
        "tech fair" in lowered
        and word_set.intersection({"speaker", "panel"})
        and word_set.intersection({"ask", "question", "questions", "submit"})
    ):
        add(TECH_FAIR_QA_ID)
    if (
        (
            word_set.intersection({"espanol", "spanish"})
            and word_set.intersection({"alfabetizacion", "basic", "basica", "computacion"})
        )
        or word_set.intersection({"alfabetizacion", "computacion"})
        and word_set.intersection({"basic", "basica"})
    ):
        add(SPANISH_BASIC_ID)
    if re.search(r"\b(?:what is|about|explain)\b.*\bdigital equity program\b", lowered):
        add("home")

    if (
        word_set.intersection({"attend", "attendance"})
        and word_set.intersection({"all", "every", "month", "scheduled"})
    ):
        add("home")
        add("contact")
    if (
        word_set.intersection({"assistance", "help", "skill", "skills", "topic", "topics"})
        and (
            "not listed" in lowered
            or word_set.intersection({"catalog", "uncatalogued"})
        )
    ):
        add("home")
        add("contact")
        add("individual")
    if (
        word_set.intersection({"laptop", "laptops"})
        and (
            word_set.intersection({"all", "any", "automatic", "automatically", "every"})
            or "automatically qualify" in lowered
        )
    ):
        add("home")
        add("contact")
        add("devices")

    rules = [
        ("individual", ("one-to-one", "one to one", "tutor", "tutoring", "tech support", "computer lab", "appointment", "individual help", "repair", "fix", "broken", "ayuda individual", "tutoria")),
        ("devices", ("device", "laptop", "computer to keep", "cellphone", "cell phone", "phone service", "lifeline", "ipad", "computadora", "telefono", "dispositivo")),
        ("contact", ("contact", "staff", "call", "email address", "not listed", "housing", "health", "parole", "benefit", "contacto", "personal")),
    ]
    for source_id, needles in rules:
        if any(needle in lowered for needle in needles):
            add(source_id)
    generic_class_intent = bool(
        word_set.intersection({"class", "classes", "workshop", "workshops", "training", "trainings", "course", "courses", "learn", "clase", "clases", "curso", "aprender"})
    )
    if generic_class_intent and not word_set.intersection(SPECIFIC_CLASS_TERMS):
        add("trainings")
    if ranked or not fallback:
        return ranked
    return [source_id for source_id in ("home", "contact") if source_id in SOURCE_BY_ID]


def source_evidence_score(query, source):
    query = semantic_question(query)
    query_folded = fold_text(query).strip(" ?.!")
    query_terms = tokens(query)
    expansions = {
        "advanced": ("advanced", "part", "pt"),
        "basica": ("basic", "beginner", "intro", "introduction"),
        "basic": ("beginner", "intro", "introduction"),
        "beginner": ("basic", "intro", "introduction"),
        "coding": ("coder", "coders", "programming"),
        "correo": ("email",),
        "robot": ("robotics", "coder", "coders"),
        "spanish": ("espanol", "alfabetizacion"),
        "wifi": ("internet", "browsing", "browser"),
        "ayuda": ("help", "support"),
        "clase": ("class", "workshop", "training"),
        "clases": ("class", "workshop", "training"),
        "computadora": ("computer", "device", "laptop"),
        "curso": ("class", "workshop", "training"),
        "donde": ("where", "location"),
        "electronico": ("email",),
        "espanol": ("spanish", "alfabetizacion"),
        "attachment": ("attachments", "email"),
        "attachments": ("attachment", "email"),
        "fraud": ("safety", "online"),
        "phishing": ("safety", "email", "online"),
        "scam": ("safety", "online"),
        "scams": ("safety", "online"),
        "spreadsheet": ("excel", "worksheet", "workbook"),
        "spreadsheets": ("excel", "worksheet", "workbook"),
        "learning": ("learn", "intro", "introduction"),
        "new": ("intro", "introduction", "beginner"),
        "registrarme": ("register", "reserve", "sign up"),
        "smartphone": ("smartphones", "tablet", "tablets"),
        "smartphones": ("smartphone", "tablet", "tablets"),
        "telefono": ("phone", "device", "lifeline"),
    }
    for term in list(query_terms):
        query_terms.extend(expansions.get(term, ()))
    manual = likely_source_ids(query, fallback=False)
    source_id = source["id"]
    term_counts = SOURCE_TERMS.get(source_id, collections.Counter())
    title_terms = collections.Counter(tokens(source.get("title", "")))
    heading_terms = collections.Counter(tokens(" ".join(source.get("headings", []))))
    matched_terms = {term for term in query_terms if term in term_counts}
    score = 0.0
    for term in query_terms:
        if term not in term_counts:
            continue
        inverse_frequency = math.log(1 + len(ANSWER_SOURCES) / (1 + DOCUMENT_FREQUENCY[term]))
        score += inverse_frequency * (1 + math.log(1 + term_counts[term]))
        score += title_terms[term] * 5.5 + heading_terms[term] * 2.5
    if source_id in manual:
        score += max(12, 24 - manual.index(source_id) * 3)
    if (
        source_id == WORD_CERTIFICATION_ID
        and "word" in set(query_terms)
        and set(query_terms).intersection({"certification", "certifications", "certified"})
    ):
        score += 30
    title = fold_text(
        re.sub(
            r"\s*[|·]\s*FS Digital Equity\s*$",
            "",
            source.get("title", ""),
            flags=re.I,
        )
    ).strip(" ?.!")
    title_aliases = {
        title,
        title.replace("&", "and"),
        re.sub(r"\band\b", "&", title),
    }
    exact_title_match = bool(query_folded and query_folded in title_aliases)
    if exact_title_match:
        score += 60
    if len(query_folded) > 5 and query_folded in title:
        score += 20

    title_or_heading_match = any(title_terms[term] or heading_terms[term] for term in matched_terms)
    genuine_match = (
        exact_title_match
        or source_id in manual
        or len(matched_terms) >= 2
        or title_or_heading_match
    )
    return score if genuine_match else 0.0


def retrieve_sources(query, limit=MAX_RETRIEVED):
    scored = []
    for source in RETRIEVABLE_SOURCES:
        score = source_evidence_score(query, source)
        if score > 0:
            scored.append((score, source))
    preferred = {
        source_id: index
        for index, source_id in enumerate(likely_source_ids(query, fallback=False))
    }
    scored.sort(key=lambda item: (
        0 if item[1]["id"] in preferred else 1,
        preferred.get(item[1]["id"], len(preferred)),
        -item[0],
        item[1].get("title", ""),
    ))
    result = []
    seen_urls = set()
    for _, source in scored:
        if source["url"] in seen_urls:
            continue
        result.append(source)
        seen_urls.add(source["url"])
        if len(result) == limit:
            break
    return result


_CALENDAR_MONTH_NAMES = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        start=1,
    )
}


def calendar_evidence_blocks(source, query, today=None):
    """Prefer live schedule blocks or intact upcoming snapshot event rows."""

    if source.get("id") != "calendar":
        return []
    current = today or datetime.now(timezone.utc).date()
    blocks = []
    if source.get("calendar_source") == "live_downloadable_calendar":
        schedule = source.get("calendar_schedule") or {}
        location = schedule.get("location") or {}
        blocks.extend(
            value
            for value in (
                str(schedule.get("month") or "").strip(),
                str(schedule.get("theme") or "").strip(),
                str(schedule.get("default_hours") or "").strip(),
                str(location.get("name") or "").strip(),
                str(location.get("address") or "").strip(),
                *[str(value).strip() for value in schedule.get("support", [])],
            )
            if value
        )
        if not source.get("calendar_events"):
            live_count = max(0, int(source.get("calendar_live_block_count") or 0))
            return list(source.get("blocks", []))[:live_count]
    else:
        blocks = [
            str(block).strip()
            for block in source.get("blocks", [])
            if re.search(
                r"(?:available classes|training schedule|tue, wed|\b2:00 pm to 3:30 pm\b|"
                r"by request only)",
                str(block or ""),
                flags=re.I,
            )
        ]
    events = []
    for event in source.get("calendar_events", []):
        if not isinstance(event, dict):
            continue
        try:
            event_date = date.fromisoformat(str(event.get("date") or ""))
        except ValueError:
            continue
        label = str(event.get("label") or "").strip()
        if label and event_date >= current:
            events.append((event_date, label))
    events.sort(key=lambda row: (row[0], row[1]))

    query_value = fold_text(semantic_question(query))
    if "tomorrow" in query_value:
        target = current + timedelta(days=1)
        events = [row for row in events if row[0] == target]
    elif re.search(r"\btoday\b", query_value):
        events = [row for row in events if row[0] == current]
    elif "next week" in query_value:
        start = current + timedelta(days=7 - current.weekday())
        end = start + timedelta(days=6)
        events = [row for row in events if start <= row[0] <= end]
    elif "this week" in query_value:
        end = current + timedelta(days=6 - current.weekday())
        events = [row for row in events if current <= row[0] <= end]
    else:
        exact_date = None
        for name, number in _CALENDAR_MONTH_NAMES.items():
            match = re.search(rf"\b{re.escape(name)}\s+(\d{{1,2}})\b", query_value)
            if not match:
                continue
            try:
                exact_date = date(current.year, number, int(match.group(1)))
                if exact_date < current:
                    exact_date = date(current.year + 1, number, int(match.group(1)))
            except ValueError:
                exact_date = None
            break
        numeric_date = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", query_value)
        if not exact_date and numeric_date:
            year = int(numeric_date.group(3) or current.year)
            if year < 100:
                year += 2000
            try:
                exact_date = date(year, int(numeric_date.group(1)), int(numeric_date.group(2)))
            except ValueError:
                exact_date = None
        if exact_date:
            if re.search(r"\bafter\b", query_value):
                events = [row for row in events if row[0] > exact_date]
            elif re.search(r"\b(?:from|starting|since|on or after)\b", query_value):
                events = [row for row in events if row[0] >= exact_date]
            else:
                events = [row for row in events if row[0] == exact_date]
            blocks.extend(label for _, label in events[:24])
            return blocks
        requested_months = {
            number
            for name, number in _CALENDAR_MONTH_NAMES.items()
            if re.search(rf"\b{re.escape(name)}\b", query_value)
        }
        if requested_months:
            events = [row for row in events if row[0].month in requested_months]
        else:
            weekday_names = {
                name.lower(): index
                for index, name in enumerate(
                    ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
                )
            }
            requested_weekdays = {
                index
                for name, index in weekday_names.items()
                if re.search(rf"\b{re.escape(name)}\b", query_value)
            }
            if requested_weekdays:
                events = [row for row in events if row[0].weekday() in requested_weekdays]
            query_terms = expanded_query_terms(query).difference({
                "calendar", "class", "classes", "date", "dates", "event", "events",
                "next", "schedule", "scheduled", "today", "tomorrow", "upcoming",
                "week", "when",
            })
            matching = [
                row for row in events
                if query_terms.intersection(expanded_query_terms(row[1]))
            ]
            if matching:
                events = matching
    blocks.extend(label for _, label in events[:24])
    return blocks


def source_excerpt(source, query, limit=1800):
    query_terms = expanded_query_terms(query)
    query_value = fold_text(semantic_question(query))
    if re.search(
        r"\b(?:register|registered|registration|reserve|sign up|enroll|"
        r"registrarme|registro|inscribirme)\b",
        query_value,
    ):
        query_terms.update({"attend", "class", "register", "registered", "registration"})
    availability_requested = bool(re.search(
        r"\b(?:available|availability|current|currently|does .+ have|eligible|"
        r"eligibility|is there|offered|qualify|requirements?|status|still|today|"
        r"tomorrow|when)\b",
        query_value,
    ))
    calendar_blocks = calendar_evidence_blocks(source, query)
    raw_blocks = (
        calendar_blocks
        + [source.get("description", "")]
        + list(source.get("facts", []))
        + list(source.get("blocks", []))
    )
    cleaned_blocks = [clean_evidence_fragment(block) for block in raw_blocks]
    priorities = collections.defaultdict(float)
    for index in range(len(calendar_blocks)):
        priorities[index] = 180 - min(index, 60)
    headings = {
        fold_text(clean_evidence_fragment(value))
        for value in source.get("headings", [])
        if clean_evidence_fragment(value)
    }

    # Keep an FAQ answer next to the matching public question. Wix exposes
    # those as adjacent blocks; scoring each block independently can otherwise
    # retain the question while truncating its answer.
    faq_matches = []
    for index, block in enumerate(cleaned_blocks):
        if not block.endswith("?"):
            continue
        overlap = len(query_terms.intersection(expanded_query_terms(block)))
        if overlap < 2:
            continue
        faq_matches.append((overlap, index))
    matched_faq_indices = set()
    if faq_matches:
        overlap, index = max(faq_matches, key=lambda row: (row[0], -row[1]))
        matched_faq_indices.add(index)
        priorities[index] = max(priorities[index], 140 + overlap)
        if index + 1 < len(cleaned_blocks) and cleaned_blocks[index + 1]:
            matched_faq_indices.add(index + 1)
            priorities[index + 1] = max(priorities[index + 1], 139 + overlap)

    # When the question names a source heading, keep that contiguous section
    # together. This preserves eligibility and availability details without
    # encoding any participant-facing answer in the router.
    active_section = False
    section_offset = 0
    matched_section_indices = set()
    for index, block in enumerate(cleaned_blocks):
        block_value = fold_text(block)
        if block_value in headings:
            active_section = bool(query_terms.intersection(expanded_query_terms(block)))
            section_offset = 0
        if not active_section:
            continue
        matched_section_indices.add(index)
        priorities[index] = max(priorities[index], 90 - min(section_offset, 30))
        section_offset += 1

    list_requested = bool(re.search(
        r"\b(?:which|what|list|name|names|option|options|certification|certifications)\b",
        query_value,
    ))
    if list_requested:
        for index, block in enumerate(cleaned_blocks):
            if (
                2 <= len(block.split()) <= 7
                and re.search(r"\b(?:19|20)\d{2}\b", block)
            ):
                priorities[index] = max(priorities[index], 110)

    candidates = []
    template_contaminated = source_has_template_content(source)
    for index, block in enumerate(cleaned_blocks):
        if (
            matched_faq_indices
            and index not in matched_faq_indices
            and index >= len(calendar_blocks)
        ):
            continue
        if (
            not matched_faq_indices
            and matched_section_indices
            and index not in matched_section_indices
            and index != 0
            and index >= len(calendar_blocks)
        ):
            continue
        if (
            template_contaminated
            and fold_text(str(block)).strip() in {"about us", "meet the team"}
        ):
            continue
        if not block:
            continue
        if re.search(r"\b(?:collage|logo)$", block, flags=re.I):
            continue
        block_value = fold_text(block)
        overlap = len(query_terms.intersection(tokens(block)))
        status_bonus = (
            12
            if availability_requested
            and re.search(
                r"\b(?:not available|no longer|on hold|under redevelopment|coming soon)\b",
                block_value,
            )
            else 0
        )
        candidates.append((priorities[index] + overlap + status_bonus, -index, block))
    candidates.sort(reverse=True)
    selected = []
    length = 0
    for _, _, block in candidates:
        if block in selected:
            continue
        separator = 1 if selected else 0
        remaining = limit - length - separator
        if remaining <= 0:
            break
        fragment = query_focused_evidence_fragment(
            block,
            query_terms,
            min(remaining, 900),
        )
        if not fragment:
            continue
        selected.append(fragment)
        length += separator + len(fragment)
        if length >= limit:
            break
    return "\n".join(selected)


def grounded_evidence_sentences(
    source,
    query,
    limit=MAX_EVIDENCE_WORDS,
    max_sentences=MAX_EVIDENCE_SENTENCES,
    require_overlap=False,
    focus_query=None,
    prior_answer=None,
):
    """Select short factual sentences that already exist in an approved record."""
    query = semantic_question(query)
    query_terms = expanded_query_terms(query)
    focus_terms = expanded_query_terms(focus_query or query)
    focus_text = fold_text(focus_query or query)
    if re.search(
        r"\b(?:when|date|dates|calendar|schedule|scheduled|this week|next class)\b",
        focus_text,
    ):
        query_terms.update({"availability", "calendar", "current", "date", "schedule"})
        focus_terms.update({"availability", "calendar", "current", "date", "schedule"})
    if re.search(r"\b(?:where|location|locations|address|office)\b", focus_text):
        query_terms.update({"address", "location", "office"})
        focus_terms.update({"address", "location", "office"})
    if re.search(r"\b(?:register|registration|reserve|sign up)\b", focus_text):
        query_terms.update({"register", "registration", "reserve"})
        focus_terms.update({"register", "registration", "reserve"})
    if re.search(r"\b(?:class|classes|course|courses|workshop|workshops)\b", focus_text):
        query_terms.update({"class", "classes", "course", "courses", "workshop", "workshops"})
    prior_terms = set(tokens(prior_answer or "", keep_stopwords=True))
    if device_use_support_intent(query):
        query_terms.add("support")
    rows = []
    values = [source.get("description", "")] + list(source.get("facts", [])) + list(source.get("blocks", []))
    template_contaminated = source_has_template_content(source)
    status_terms = {
        "on hold": 18,
        "not available": 24,
        "can no longer be booked": 24,
        "no longer be booked": 24,
        "ended": 20,
        "coming soon": 12,
        "changed": 3,
        "limited": 7,
        "may need to wait": 7,
        "availability can change": 7,
        "confirm": 2,
    }
    status_requested = bool(
        set(tokens(focus_query or query, keep_stopwords=True)).intersection({
            "available", "availability", "current", "date", "eligible", "eligibility",
            "free", "get", "schedule", "time", "wait", "week", "when", "booked", "offered",
        })
        or re.search(
            r"\b(?:is there|does fortune have|do you have)\b",
            fold_text(focus_query or query),
        )
    )
    purpose_requested = bool(re.search(
        r"\b(?:what does|what is .+ for|purpose)\b",
        fold_text(query),
    ))
    eligibility_requested = bool(
        set(tokens(focus_query or query, keep_stopwords=True)).intersection({
            "eligible", "eligibility", "qualify", "qualified", "requirements",
        })
    )
    seen = set()
    source_labels = {
        fold_text(clean_evidence_fragment(value)).strip(" .!?:;-")
        for value in [source.get("title", ""), *source.get("headings", [])]
        if clean_evidence_fragment(value)
    }
    short_title = re.sub(
        r"\s*[|·]\s*FS Digital Equity\s*$",
        "",
        str(source.get("title", "")),
        flags=re.I,
    ).strip()
    for value_index, value in enumerate(values):
        if (
            template_contaminated
            and fold_text(str(value)).strip() in {"about us", "meet the team"}
        ):
            continue
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if not value or is_source_boilerplate(value):
            continue
        sentences = re.split(r"(?<!a\.m\.)(?<!p\.m\.)(?<=[.!?])\s+", value, flags=re.I)
        for sentence_index, sentence in enumerate(sentences):
            sentence = clean_evidence_fragment(
                re.sub(r"[\u200b-\u200d\ufeff]", "", sentence)
            )
            sentence = re.sub(r"^Home Service list\s+", "", sentence, flags=re.I)
            if short_title:
                sentence = re.sub(
                    rf"^{re.escape(short_title)}\s+",
                    "",
                    sentence,
                    flags=re.I,
                )
            sentence = re.sub(r"\s+Upcoming Sessions All Locations\s*$", "", sentence, flags=re.I)
            if len(sentence.split()) < 4 or len(sentence) > 620:
                continue
            if sentence.endswith("?") and re.match(
                r"^(?:are you|looking for|need |new to|ready to|so what|want )",
                fold_text(sentence),
            ):
                continue
            if sentence.endswith("!") and re.search(
                r"\b(?:join|ready|you|your)\b",
                fold_text(sentence),
            ):
                continue
            key = fold_text(sentence)
            if key in seen:
                continue
            if key.strip(" .!?:;-") in source_labels:
                continue
            seen.add(key)
            sentence_terms = set(tokens(sentence))
            sentence_words = set(tokens(sentence, keep_stopwords=True))
            if prior_terms and len(sentence_words) >= 5:
                repeated_share = len(sentence_words.intersection(prior_terms)) / len(sentence_words)
                if repeated_share >= 0.8:
                    continue
            matched_terms = query_terms.intersection(sentence_terms)
            meaningful_matches = matched_terms.difference({
                "current", "fortune", "information", "page", "program", "society", "staff",
            })
            title_overlap = len(query_terms.intersection(tokens(source.get("title", ""))))
            overlap_score = sum(
                3 + math.log(1 + len(ANSWER_SOURCES) / (1 + DOCUMENT_FREQUENCY[term]))
                for term in matched_terms
            )
            focus_bonus = 8 * len(focus_terms.intersection(sentence_terms))
            status_bonus = (
                max((bonus for term, bonus in status_terms.items() if term in key), default=0)
                if status_requested and matched_terms
                else 0
            )
            status = status_bonus > 0
            title_bonus = title_overlap * 2 if matched_terms else 0
            generic_summary_penalty = (
                8
                if re.match(r"^(?:detailed\s+)?information\s+(?:about|on)\b", key)
                else 0
            )
            purpose_bonus = (
                2 * len(sentence_terms.intersection({
                    "help", "learn", "provide", "provides", "providing", "receive",
                    "resource", "support", "train", "training", "use",
                }))
                if purpose_requested
                else 0
            )
            eligibility_bonus = (
                14
                if eligibility_requested
                and sentence_terms.intersection({
                    "active", "attendee", "attendees", "previous", "qualify",
                    "required", "requirements", "workshop", "workshops",
                })
                else 0
            )
            score = (
                overlap_score
                + focus_bonus
                + title_bonus
                + status_bonus
                + purpose_bonus
                + eligibility_bonus
                - generic_summary_penalty
                - (value_index * 0.01 + sentence_index * 0.001)
            )
            if matched_terms and not meaningful_matches:
                score = min(score, 0)
            rows.append((score, status, sentence))

    rows.sort(key=lambda row: (-row[0], not row[1]))
    positive_rows = [row for row in rows if row[0] > 0]
    if positive_rows:
        rows = positive_rows
    elif require_overlap:
        return ""
    selected = []
    selected_term_sets = []
    word_count = 0
    for _, _, sentence in rows:
        words = sentence.split()
        sentence_term_set = set(tokens(sentence))
        if any(
            len(sentence_term_set.intersection(existing))
            / max(1, min(len(sentence_term_set), len(existing)))
            >= 0.75
            for existing in selected_term_sets
        ):
            continue
        if selected and word_count + len(words) > limit:
            continue
        selected.append(sentence)
        selected_term_sets.append(sentence_term_set)
        word_count += len(words)
        if len(selected) == max_sentences or word_count >= limit:
            break
    return " ".join(selected)


def distinctive_query_terms(query):
    """Keep the rare terms that distinguish this request inside the site corpus."""

    request_words = {
        "after", "ask", "asks", "cover", "covered", "covers", "else", "explain",
        "begin", "begun", "current", "find", "help", "hours", "instead", "learn", "making", "now", "its",
        "need", "offered", "read", "regular", "status", "still", "switch", "switching",
        "option", "options", "say", "says", "show", "shows", "start", "started", "teach", "teaches", "use", "uses", "who",
        "today", "tomorrow", "class", "classes", "course", "courses", "workshop", "workshops",
    }
    known = {
        term: DOCUMENT_FREQUENCY[term]
        for term in tokens(semantic_question(query))
        if DOCUMENT_FREQUENCY[term] > 0 and term not in request_words
    }
    if not known:
        return set()
    rarest = min(known.values())
    return {
        term for term, frequency in known.items()
        if frequency <= max(3, rarest * 3)
    }


def source_supports_query(source, query):
    """Require every distinctive request term to exist in the selected record."""

    focus_terms = distinctive_query_terms(query)
    if not focus_terms:
        return True
    source_terms = SOURCE_TERMS.get(source.get("id"), {})
    support_aliases = {
        "background": {"experience", "prior"},
        "class": {"classes"},
        "classes": {"class"},
        "experience": {"background", "prior"},
        "laptop": {"laptops"},
        "laptops": {"laptop"},
        "one-on": {"one-to-one"},
        "one-to-one": {"one-on"},
        "resume": {"resumes"},
        "resumes": {"resume"},
        "skill": {"skills"},
        "skills": {"skill"},
        "qualify": {"eligible", "eligibility", "qualified"},
        "requirements": {"eligible", "eligibility", "qualify", "qualified"},
        "workshop": {"workshops"},
        "workshops": {"workshop"},
    }
    return all(
        source_terms.get(term, 0)
        or any(source_terms.get(alias, 0) for alias in support_aliases.get(term, set()))
        for term in focus_terms
    )


def source_payload(sources):
    seen = set()
    result = []
    for source in sources:
        if isinstance(source, str):
            source = SOURCE_BY_ID.get(source)
        if not source or source.get("authority") != "answer" or source["url"] in seen:
            continue
        result.append({"id": source["id"], "title": source["title"], "url": source["url"]})
        seen.add(source["url"])
    return result


def link_record(url, label=None):
    url = canonical_url(url)
    if not url:
        return None
    source_id = SOURCE_ID_BY_URL.get(url)
    source = SOURCE_BY_ID.get(source_id, {})
    title = label or source.get("title") or urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].replace("-", " ").title()
    return {"title": title, "url": url}


def sanitize_page_context(value):
    if not isinstance(value, dict):
        return {"url": "", "path": "", "title": ""}
    return {
        "url": canonical_url(value.get("url")),
        "path": clip_words(value.get("path"), 20)[:160],
        "title": clip_words(value.get("title"), 24)[:200],
    }


def capture_page_context(value):
    """Return server-owned page metadata suitable for persistence."""

    context = sanitize_page_context(value)
    source_id = SOURCE_ID_BY_URL.get(context["url"], "")
    source = SOURCE_BY_ID.get(source_id)
    if not source:
        return {"source_id": "", "url": "", "path": "", "title": "", "authority": ""}
    return {
        "source_id": source["id"],
        "url": source["url"],
        "path": urllib.parse.urlsplit(source["url"]).path or "/",
        "title": source["title"],
        "authority": source["authority"],
    }


def approved_current_page_source(page_context):
    context = sanitize_page_context(page_context)
    source_id = SOURCE_ID_BY_URL.get(context["url"], "")
    source = SOURCE_BY_ID.get(source_id)
    if (
        not source
        or source.get("authority") != "answer"
        or source.get("status", 200) != 200
        or source_is_placeholder_template(source)
    ):
        return None
    return source


def contextualize_sources(retrieved, page_context):
    result = list(retrieved)
    current = approved_current_page_source(page_context)
    if current:
        result = [current] + [source for source in result if source["url"] != current["url"]]
    return result[:MAX_RETRIEVED]


def question_refers_to_current_page(question):
    value = fold_text(semantic_question(question))
    patterns = (
        r"\b(?:this|the current) (?:page|class|service|program|event|workshop)\b",
        r"\b(?:on|from) this page\b",
        r"\bwhat is here\b",
        r"\bwhere should i go next\b",
        r"\bwhat do i do next\b",
        r"\bmain information here\b",
        r"\b(?:help|information|service|program|class|workshop) "
        r"(?:described |listed |shown |mentioned |explained )?here\b",
        r"\b(?:described|listed|shown|mentioned|explained) (?:on this page|here)\b",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def question_needs_history_context(question):
    """Identify a follow-up whose nouns live in a previous safe user turn."""

    value = fold_text(semantic_question(question))
    patterns = (
        r"\b(?:it|its|that|those|they|them|there)\b",
        r"\b(?:this|that) class\b",
        r"\b(?:which|is there) one\b",
        r"\bwhat (?:else|about|are they for|kind of (?:help|class|workshop))\b",
        r"\bwhat kinds? of (?:help|support|classes|services|workshops)\b",
        r"\bwhat (?:are|is) (?:the )?(?:regular )?(?:class |support |office )?"
        r"(?:hours|schedule)\b",
        r"\b(?:can|do) i walk in\b",
        r"\bwhen is it offered\b",
        r"\bdo i need\b",
        r"\bhow do i confirm whether i qualify\b",
        r"\bque aprenderia\b",
        r"\bque mas\b",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def history_topic_question(history):
    """Return the latest explicit topic from the bounded, privacy-clean history."""

    fallback = ""
    for item in reversed(list(history or [])):
        if item.get("role") != "user":
            continue
        content = semantic_question(item.get("content"))
        if not content:
            continue
        if not fallback:
            fallback = content
        word_set = set(tokens(content, keep_stopwords=True))
        if word_set.intersection(HISTORY_TOPIC_TERMS):
            return content
    return fallback


def explicit_follow_up_domain(question):
    """Return the domain of a broad but independently routable new question."""

    value = fold_text(semantic_question(question))
    if re.search(
        r"\b(?:what|which) kinds? of (?:classes|courses|trainings|workshops)\b|"
        r"\b(?:class|course|training|workshop) catalog\b|"
        r"\bwhat (?:classes|courses|trainings|workshops) (?:are )?(?:available|offered)\b",
        value,
    ):
        return "catalog"
    if re.search(
        r"\b(?:calendar|current schedule|regular class hours|class schedule|"
        r"schedule of classes|what (?:are|is) (?:the )?class hours)\b",
        value,
    ):
        return "schedule"
    if re.search(
        r"\bwhat kinds? of (?:help|support|services)\b|"
        r"\bwhat (?:help|support|services) (?:are|is) (?:available|offered)\b",
        value,
    ):
        return "support"
    return ""


def routing_topic_domain(question):
    """Classify only the broad domains needed to avoid stale-topic carryover."""

    value = fold_text(semantic_question(question))
    explicit = explicit_follow_up_domain(value)
    if explicit:
        return explicit
    words = set(tokens(value, keep_stopwords=True))
    if device_use_support_intent(value) or individual_support_intent(value):
        return "support"
    if device_distribution_intent(value):
        return "device"
    if re.search(r"\b(?:calendar|current schedule|class schedule|regular class hours)\b", value):
        return "schedule"
    if exact_named_source_ids(value) or words.intersection(
        SPECIFIC_CLASS_TERMS.union({"class", "classes", "course", "courses", "training", "trainings", "workshop", "workshops"})
    ):
        return "catalog"
    return ""


def contextual_routing_question(question, history=None):
    """Add only the latest safe topic to genuinely elliptical retrieval turns."""

    question = semantic_question(question)
    if not question_needs_history_context(question):
        return question
    topic = history_topic_question(history)
    new_domain = explicit_follow_up_domain(question)
    if new_domain:
        prior_domain = routing_topic_domain(topic)
        # A broad catalog question is independently routable even after a
        # specific class. Schedule/support questions retain context only when
        # the preceding turn was already in that same domain.
        if new_domain == "catalog" or prior_domain != new_domain:
            return question
    # A turn can contain conversational words such as "there" while still
    # naming a complete, independently routable topic.  In that case the new
    # topic must win over the previous exchange.  Calendar and registration
    # routes are intentionally excluded because phrases such as "when is it
    # offered?" still need the class named in history.
    explicit_sources = likely_source_ids(question, fallback=False)
    generic_follow_up_ids = {"calendar", "contact", "trainings"}
    if any(source_id not in generic_follow_up_ids for source_id in explicit_sources):
        return question
    if not topic or fold_text(topic) == fold_text(question):
        return question
    return f"{topic}. Follow-up: {question}"


def guided_class_sources(question):
    """Resolve only the guide's explicit class-choice prompts."""

    prompt = " ".join(tokens(question, keep_stopwords=True))
    destination_by_prompt = {
        "class topics": WORKSHOPS_URL,
        "dates locations": CALENDAR_URL,
        "register": CONTACT_URL,
        "temas": WORKSHOPS_URL,
        "fechas y lugares": CALENDAR_URL,
        "inscribirme": CONTACT_URL,
    }
    source_id = SOURCE_ID_BY_URL.get(destination_by_prompt.get(prompt, ""), "")
    source = SOURCE_BY_ID.get(source_id)
    if not source or source.get("authority") != "answer" or source.get("status", 200) != 200:
        return []
    return [source]


def registration_sources(question):
    value = fold_text(semantic_question(question))
    if not re.search(
        r"\b(?:register|registration|sign up|reserve|registrarme|registro|inscribirme)\b",
        value,
    ):
        return []
    return [
        SOURCE_BY_ID[source_id]
        for source_id in ("contact", "calendar")
        if source_id in SOURCE_BY_ID
        and SOURCE_BY_ID[source_id].get("authority") == "answer"
        and SOURCE_BY_ID[source_id].get("status", 200) == 200
    ]


def current_faq_sources(question):
    """Route the four current public FAQs to pages that actually contain them."""

    value = fold_text(semantic_question(question))
    words = set(tokens(value, keep_stopwords=True))
    source_ids = []
    if (
        re.search(r"\bwalk (?:in|into)\b|\bwalk-?ins?\b", value)
        and re.search(r"\bregister(?:ed|ing)?\b|\bregistration\b", value)
    ):
        source_ids = ["home"]
    elif (
        words.intersection({"attend", "attendance"})
        and words.intersection({"all", "every", "month", "scheduled"})
    ):
        source_ids = ["home"]
    elif (
        words.intersection({"assistance", "help", "skill", "skills", "topic", "topics"})
        and ("not listed" in value or words.intersection({"catalog", "uncatalogued"}))
    ):
        source_ids = ["home"]
    elif (
        words.intersection({"laptop", "laptops"})
        and (
            words.intersection({"all", "any", "automatic", "automatically", "every"})
            or "automatically qualify" in value
        )
    ):
        source_ids = ["home"]
    return [
        SOURCE_BY_ID[source_id]
        for source_id in source_ids
        if source_id in SOURCE_BY_ID
        and SOURCE_BY_ID[source_id].get("authority") == "answer"
        and SOURCE_BY_ID[source_id].get("status", 200) == 200
    ]


def retired_class_sources(question):
    """Keep removed named classes on current discovery and contact evidence."""

    if not retired_class_intent(question):
        return []
    return [
        SOURCE_BY_ID[source_id]
        for source_id in ("trainings", "contact")
        if source_id in SOURCE_BY_ID
        and SOURCE_BY_ID[source_id].get("authority") == "answer"
        and SOURCE_BY_ID[source_id].get("status", 200) == 200
    ]


def retrieval_plan(question, page_context=None):
    """Choose the narrowest approved evidence scope that can answer a question."""
    question = semantic_question(question)
    guided = guided_class_sources(question)
    if guided:
        return "site", guided

    current = approved_current_page_source(page_context)
    if current and question_refers_to_current_page(question):
        contextual_parts = re.split(r"\.\s*Follow-up:\s*", question, maxsplit=1, flags=re.I)
        if len(contextual_parts) == 1:
            return "page", [current]
        topic_sources = retrieve_sources(contextual_parts[0])
        if not topic_sources or topic_sources[0]["url"] == current["url"]:
            return "page", [current]
    faq = current_faq_sources(question)
    if faq:
        return "site", faq
    registration = registration_sources(question)
    if registration:
        return "site", registration
    retired = retired_class_sources(question)
    if retired:
        return "site", retired

    site_sources = retrieve_sources(question)
    if current and site_sources and site_sources[0]["url"] == current["url"]:
        return "page", [current]
    if site_sources:
        return "site", site_sources
    return "staff", []


def related_links(question, sources, limit=3):
    lowered = fold_text(question)
    candidates = []
    if any(word in lowered for word in ("device", "laptop", "phone", "computer to keep", "lifeline")):
        candidates.extend([(DEVICES_URL, "Review device programs"), (CONTACT_URL, "Confirm eligibility with staff"), (SUPPORT_URL, "Find device help")])
    elif any(word in lowered for word in ("class", "workshop", "training", "learn", "course", "register", "sign up")):
        candidates.extend([(CALENDAR_URL, "View the current calendar"), (CONTACT_URL, "Registration details"), (WORKSHOPS_URL, "Browse workshops")])
    elif any(word in lowered for word in ("support", "tutor", "appointment", "lab", "fix", "troubleshoot")):
        candidates.extend([(SUPPORT_URL, "See individual support"), (CALENDAR_URL, "Check current hours"), (CONTACT_URL, "Ask Digital Equity staff")])
    elif any(word in lowered for word in ("practice", "exercise", "quiz", "assessment")):
        candidates.extend([(PRACTICE_URL, "Open skills practice"), (WORKSHOPS_URL, "Browse workshops"), (CONTACT_URL, "Ask for guidance")])
    else:
        candidates.extend([(WORKSHOPS_URL, "Browse workshops"), (PRACTICE_URL, "Practice digital skills"), (CONTACT_URL, "Ask Digital Equity staff")])

    source_urls = {source["url"] for source in sources}
    for source in sources[:2]:
        for url in source.get("internal_links", []):
            canonical = canonical_url(url)
            linked = SOURCE_BY_ID.get(SOURCE_ID_BY_URL.get(canonical, ""), {})
            if linked.get("authority") in {"answer", "navigation"}:
                candidates.append((canonical, linked.get("title")))

    result = []
    seen = set(source_urls)
    for url, label in candidates:
        record = link_record(url, label)
        if not record or record["url"] in seen:
            continue
        result.append(record)
        seen.add(record["url"])
        if len(result) == limit:
            break
    if not result:
        result = [link_record(CONTACT_URL, "Ask Digital Equity staff")]
    return [record for record in result if record]


def response_contract(
    kind,
    message,
    reason,
    sources,
    question,
    model_called,
    choices=None,
    retrieval_scope=None,
):
    sources = list(sources)
    if retrieval_scope not in {"page", "site", "staff"}:
        retrieval_scope = "staff" if kind in {"privacy", "handoff"} else "site"
    return {
        "kind": kind,
        # Preserve the model's complete sentence or requested list, including
        # meaningful line breaks, instead of applying a fixed length gate.
        "message": normalize_answer(message),
        "reason": clip_words(reason, MAX_REASON_WORDS),
        "sources": source_payload(sources[:3]),
        "related": related_links(question, sources),
        "choices": choices or [],
        "handoff_url": CONTACT_URL,
        "model": MODEL,
        "model_called": model_called,
        "retrieval_scope": retrieval_scope,
        "continuation": {"label": "Ask the live guide", "available": bool(KEY)},
    }


def privacy_response(question="", language_code="en"):
    return response_contract(
        kind="privacy",
        message=participant_copy("privacy_message", language_code),
        reason=participant_copy("privacy_reason", language_code),
        sources=[SOURCE_BY_ID["contact"]],
        question=question or "contact Digital Equity staff",
        model_called=False,
    )


BASE_SYSTEM_PROMPT = SELECTOR_SYSTEM_PROMPT


def retrieval_prompt(
    query,
    sources,
    page_context=None,
    interaction=None,
    previous_answer="",
    current_date="",
):
    records = []
    for source in sources:
        record = {
            "id": source["id"],
            "title": source["title"],
            "url": source["url"],
            "reviewed_on": source.get("lastmod") or KNOWLEDGE["reviewed_on"],
            "volatile": bool(source.get("volatile")),
            "content": source_excerpt(
                source,
                query,
                limit=(3000 if source.get("id") == "calendar" else MAX_MODEL_EXCERPT_CHARS),
            ),
        }
        if source.get("id") == "calendar":
            record.update({
                "calendar_source": source.get("calendar_source") or "rendered_snapshot",
                "source_fetched_at": source.get("source_fetched_at"),
                "source_captured_at": source.get("source_captured_at"),
                "calendar_document_url": source.get("calendar_document_url"),
            })
        records.append(record)
    current = approved_current_page_source(page_context)
    return build_selector_prompt(
        records,
        current_page_id=current["id"] if current else "",
        previous_answer=re.sub(r"\s+", " ", str(previous_answer or "")).strip(),
        current_date=current_date or datetime.now(timezone.utc).date().isoformat(),
    )


def clean_source_title(source):
    return re.sub(
        r"\s*[|·]\s*FS Digital Equity\s*$",
        "",
        str(source.get("title") or "Digital Equity page"),
        flags=re.I,
    ).strip()


def model_clarification_response(
    question,
    model_question,
    retrieval_scope="site",
):
    """Return only a model-authored clarification; never synthesize stock copy."""

    raw_message = str(model_question or "").strip()
    message_text = re.sub(r"\s+", " ", raw_message).strip()
    message = message_text
    folded = fold_text(message).lstrip("¿").strip()
    if (
        not message
        or re.search(r"https?://|www\.", message, flags=re.I)
        or re.search(
            r"\b(?:system|developer|hidden).{0,32}(?:prompt|message|instruction|rules|safety)|"
            r"\b(?:ignore|reveal|override) (?:the )?(?:prompt|instructions|rules|safety)\b",
            folded,
        )
        or model_requests_personal_details(message)
    ):
        raise ModelResponseRejected("The model did not return a safe clarification")
    response = response_contract(
        kind="clarify",
        message=message,
        reason="",
        sources=[],
        question=question,
        model_called=True,
        choices=[],
        retrieval_scope=retrieval_scope,
    )
    response["related"] = []
    return response


def replay_response_is_current(response):
    """Allow only privacy holds or current model-authored turns to replay."""

    if not isinstance(response, dict) or not str(response.get("message") or "").strip():
        return False
    kind = response.get("kind")
    if kind == "privacy":
        return response.get("model_called") is False
    return (
        kind in {"answer", "clarify", "handoff"}
        and response.get("model_called") is True
        and response.get("prompt_policy_version") == PROMPT_POLICY_VERSION
    )


def model_requests_personal_details(text):
    """Reject model copy that asks the participant to disclose private data."""

    value = fold_text(text)
    request = (
        r"(?:what(?:'s| is)|share|provide|enter|send|give|tell me|write|"
        r"cual es|comparte|proporciona|ingresa|envia|dime|escribe|dame)"
    )
    detail = (
        r"(?:full name|name|phone number|phone|telephone number|email address|e-mail address|"
        r"email|street address|home address|address|date of birth|birthday|age|zip code|"
        r"postal code|fortune id|case number|member id|parole status|probation status|"
        r"social security number|ssn|nombre completo|nombre|numero de telefono|telefono|"
        r"correo electronico|correo|direccion|fecha de nacimiento|edad|codigo postal|"
        r"id de fortune|numero de caso|libertad condicional|seguro social)"
    )
    possessive_detail = (
        r"\b(?:your|tu|su) (?:full name|name|phone number|phone|email address|e-mail address|"
        r"email|street address|home address|address|date of birth|birthday|age|zip code|"
        r"postal code|fortune id|case number|member id|parole status|probation status|ssn|"
        r"nombre completo|nombre|numero de telefono|telefono|correo electronico|correo|"
        r"direccion|fecha de nacimiento|edad|codigo postal|id de fortune|numero de caso|"
        r"libertad condicional|seguro social)\b"
    )
    return bool(
        re.search(rf"\b{request}\b.{{0,48}}\b{detail}\b", value)
        or re.search(possessive_detail, value)
        or re.search(r"\bwhere (?:do|did) you live\b", value)
        or re.search(r"\bhow old are you\b", value)
        or re.search(r"\bare you (?:on )?(?:parole|probation)\b", value)
        or re.search(r"\b(?:who are you|where are you|what is your information)\b", value)
        or re.search(
            r"\bwhich (?:email|phone|address|name)\b.{0,32}\b(?:share|provide|use)\b",
            value,
        )
    )


_GROUNDING_EQUIVALENT_GROUPS = (
    frozenset({"account", "cuenta"}),
    frozenset({"attachment", "attachments", "adjunto", "adjuntos"}),
    frozenset({"available", "availability", "disponible", "disponibles"}),
    frozenset({"background", "experience"}),
    frozenset({"calendar", "hours", "schedule", "time"}),
    frozenset({"class", "classes", "clase", "clases", "course", "curso", "taller"}),
    frozenset({"computer", "computers", "computadora", "computadoras"}),
    frozenset({"device", "devices", "dispositivo", "dispositivos"}),
    frozenset({"eligible", "eligibility", "elegible", "elegibles", "qualify", "qualifies"}),
    frozenset({"email", "correo", "electronico"}),
    frozenset({"free", "gratis", "gratuita", "gratuitas", "gratuito", "gratuitos"}),
    frozenset({"help", "support", "ayuda", "apoyo"}),
    frozenset({"laptop", "laptops", "portatil", "portatiles"}),
    frozenset({"learn", "learning", "aprender"}),
    frozenset({"limited", "limitada", "limitado", "limitadas", "limitados"}),
    frozenset({"message", "messages", "mensaje", "mensajes"}),
    frozenset({"participant", "participants", "participante", "participantes"}),
    frozenset({"phone", "cellphone", "telefono", "celular"}),
    frozenset({"spreadsheet", "spreadsheets", "worksheet", "worksheets", "hoja", "hojas"}),
    frozenset({"training", "trainings", "workshop", "workshops", "capacitacion"}),
    frozenset({"mon", "monday", "mondays"}),
    frozenset({"tue", "tues", "tuesday", "tuesdays"}),
    frozenset({"wed", "wednesday", "wednesdays"}),
    frozenset({"thu", "thur", "thurs", "thursday", "thursdays"}),
    frozenset({"fri", "friday", "fridays"}),
    frozenset({"sat", "saturday", "saturdays"}),
    frozenset({"sun", "sunday", "sundays"}),
)

_RISKY_QUALIFIER_GROUPS = (
    frozenset({"available", "availability", "disponible", "disponibles"}),
    frozenset({"eligible", "eligibility", "elegible", "elegibles", "qualify", "qualifies"}),
    frozenset({"free", "gratis", "gratuita", "gratuitas", "gratuito", "gratuitos"}),
    frozenset({
        "guarantee", "guaranteed", "garantia", "garantizado", "garantizada",
        "garantizados", "garantizadas",
    }),
    frozenset({"immediate", "immediately", "inmediato", "inmediata", "inmediatamente"}),
    frozenset({"limited", "limitada", "limitado", "limitadas", "limitados"}),
    frozenset({"unlimited", "ilimitada", "ilimitado", "ilimitadas", "ilimitados"}),
    frozenset({"within", "dentro"}),
)

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "cero": "0", "uno": "1", "dos": "2", "tres": "3",
    "cuatro": "4", "cinco": "5", "seis": "6",
    "siete": "7", "ocho": "8", "nueve": "9", "diez": "10", "once": "11",
    "doce": "12",
}

_ONE_TO_ONE_LABEL_PATTERN = re.compile(
    r"\b(?:"
    r"(?:1|one)\s*-\s*(?:on|to)\s*-\s*(?:1|one)|"
    r"1\s*[:/]\s*1|"
    r"1\s+(?:on|to)\s+1|"
    r"one\s+(?:on|to)\s+one"
    r")\b",
    re.I,
)

_CLAIM_UNITS = {
    "class": "class", "classes": "class", "clase": "class", "clases": "class",
    "day": "day", "days": "day", "dia": "day", "dias": "day",
    "device": "device", "devices": "device", "dispositivo": "device", "dispositivos": "device",
    "hour": "hour", "hours": "hour", "hora": "hour", "horas": "hour",
    "laptop": "laptop", "laptops": "laptop", "portatil": "laptop", "portatiles": "laptop",
    "minute": "minute", "minutes": "minute", "minuto": "minute", "minutos": "minute",
    "month": "month", "months": "month", "mes": "month", "meses": "month",
    "participant": "person", "participants": "person", "people": "person", "person": "person",
    "participante": "person", "participantes": "person", "persona": "person", "personas": "person",
    "phone": "phone", "phones": "phone", "telefono": "phone", "telefonos": "phone",
    "session": "session", "sessions": "session", "sesion": "session", "sesiones": "session",
    "week": "week", "weeks": "week", "semana": "week", "semanas": "week",
    "workshop": "workshop", "workshops": "workshop", "taller": "workshop", "talleres": "workshop",
    "year": "year", "years": "year", "ano": "year", "anos": "year",
}

_UNIVERSAL_CLAIM_PATTERNS = (
    re.compile(r"\b(?:anyone|everyone|every participant|all participants)\b", re.I),
    re.compile(r"\b(?:cualquier persona|para todos|todas las personas|todos los participantes)\b", re.I),
)

_NEGATIVE_STATUS_PATTERN = re.compile(
    r"\b(?:not (?:currently )?(?:available|offered)|unavailable|"
    r"(?:currently )?on hold|no longer (?:available|bookable|offered|provided|distributed)|"
    r"can no longer be booked|coming soon|"
    r"no (?:esta|está) (?:actualmente )?(?:disponible|ofrecid[ao]s?)|"
    r"(?:actualmente )?en pausa|indisponible|"
    r"ya no (?:esta|está) disponible|ya no se (?:ofrece|proporciona|distribuye)|"
    r"proximamente|próximamente)\b",
    re.I,
)
_POSITIVE_PROVISION_PATTERN = re.compile(
    r"\b(?:offers?|provides?|distributes?|supplies?|"
    r"ofrece(?:n)?|proporciona(?:n)?|distribuye(?:n)?|suministra(?:n)?)\b",
    re.I,
)
_INFORMATION_ABOUT_SERVICE_PATTERN = re.compile(
    r"\b(?:offers?|provides?)\s+(?:information|details?|guidance)\s+(?:about|on)|"
    r"\b(?:ofrece(?:n)?|proporciona(?:n)?)\s+(?:informacion|información|detalles?)\s+"
    r"(?:acerca de|sobre)",
    re.I,
)
_STATUS_TOPIC_IGNORED_TERMS = {
    "access", "available", "availability", "class", "classes", "coming", "current",
    "currently", "device", "devices",
    "digital", "distribute", "distributed", "distributes", "distributing",
    "distribution", "equity", "fortune", "free", "help", "hold", "offer", "offered",
    "offering", "offers", "program", "programs", "provide", "provided", "provides",
    "providing", "service", "services", "society", "soon", "supplied", "supplies",
    "supply", "supplying", "support", "tools", "training", "trainings", "website",
    "workshop", "workshops",
}

_ENTITY_PATTERN = re.compile(
    r"\b[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9'’-]*"
    r"(?:\s+(?:(?:and|de|del|of|the|to|y)\s+)?"
    r"[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9'’-]*)*"
)


def _expanded_grounding_terms(value):
    terms = set(tokens(value))
    for group in _GROUNDING_EQUIVALENT_GROUPS:
        if terms.intersection(group):
            terms.update(group)
    return terms


def _claim_numbers(value):
    folded = fold_text(value)
    found = set(re.findall(r"(?<![\w])\d+(?![\w])", folded))
    # Spelled-out numbers count only when they actually quantify a guarded
    # unit. This keeps pronouns such as "create one during the session" from
    # becoming a false claim of exactly one session.
    found.update(number for number, _unit in _claim_number_unit_pairs(value))
    return found


def _claim_number_unit_pairs(value):
    # One-to-one labels describe the format of support, not a count. Without
    # removing the label first, wording such as "1-on-1 tutoring sessions" is
    # misread as a claim that exactly one session is offered.
    value = _ONE_TO_ONE_LABEL_PATTERN.sub("individual", str(value or ""))
    # Clock times are separately checked by _claim_numbers. Removing the whole
    # time here prevents the minute value in "3:30 PM ... sessions" from being
    # misread as a claim about 30 sessions.
    value_without_times = re.sub(
        r"\b\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?\b",
        " ",
        fold_text(value),
        flags=re.I,
    )
    words = tokens(value_without_times, keep_stopwords=True)
    pairs = set()
    barriers = {
        "after", "and", "before", "by", "during", "for", "in", "of", "or",
        "through", "to", "with",
    }
    for index, word in enumerate(words):
        number = word if word.isdigit() else _NUMBER_WORDS.get(word)
        if number is None:
            continue
        unit = None
        for candidate in words[index + 1:index + 6]:
            if candidate in barriers:
                break
            if candidate in _CLAIM_UNITS:
                unit = _CLAIM_UNITS[candidate]
                break
        if unit:
            pairs.add((number, unit))
    return pairs


def _qualifier_polarities(value, group):
    words = tokens(value, keep_stopwords=True)
    negatives = {"cannot", "cant", "never", "no", "not", "nunca", "sin"}
    leading_response_no = bool(re.match(r"^\s*no\s*[,;:]", str(value), re.I))
    polarities = set()
    for index, word in enumerate(words):
        if word not in group:
            continue
        prior_start = max(0, index - 3)
        negative_positions = {
            position
            for position in range(prior_start, index)
            if words[position] in negatives
        }
        if leading_response_no:
            negative_positions.discard(0)
        polarities.add("negative" if negative_positions else "positive")
    return polarities


def _source_qualifier_polarities(value, group):
    """Recognize bounded source phrases that entail current availability."""

    polarities = _qualifier_polarities(value, group)
    if "available" not in group:
        return polarities
    folded = fold_text(value)
    if re.search(
        r"\b(?:not (?:currently )?available|unavailable|currently on hold|on hold|"
        r"no longer (?:available|bookable|offered)|can no longer be booked|coming soon)\b",
        folded,
    ):
        return {"negative"}
    if polarities:
        return polarities
    if re.search(
        r"\b(?:office hours?|support hours?|walk-?in|by appointment|"
        r"by request(?: only)?|schedule an appointment|currently offered|"
        r"is offered|are offered|"
        r"offers?|provides?|provided)\b",
        folded,
    ):
        return {"positive"}
    return set()


def _answer_conflicts_with_negative_status(answer, source_claim_text):
    """Reject a current provision claim that reverses a source status."""

    ignored_terms = _expanded_grounding_terms(
        " ".join(_STATUS_TOPIC_IGNORED_TERMS)
    )
    source_lines = [
        line.strip()
        for line in str(source_claim_text or "").splitlines()
        if tokens(line, keep_stopwords=True)
    ]
    negative_contexts = []
    for index, line in enumerate(source_lines):
        if not _NEGATIVE_STATUS_PATTERN.search(line):
            continue
        context = " ".join(source_lines[max(0, index - 1):index + 1])
        terms = _expanded_grounding_terms(context).difference(ignored_terms)
        if terms:
            negative_contexts.append(terms)
    if not negative_contexts:
        return False

    answer_sentences = re.split(r"(?<=[.!?])\s+|\n+", str(answer or ""))
    for sentence in answer_sentences:
        if (
            not _POSITIVE_PROVISION_PATTERN.search(sentence)
            or _NEGATIVE_STATUS_PATTERN.search(sentence)
            or _INFORMATION_ABOUT_SERVICE_PATTERN.search(sentence)
        ):
            continue
        provision_terms = _expanded_grounding_terms(sentence).difference(ignored_terms)
        if any(len(provision_terms.intersection(context)) >= 2 for context in negative_contexts):
            return True
    return False


def _negative_status_sentence_first(answer, source_claim_text):
    """Keep a model's source-backed status caveat inside the concise word cap."""

    if not _NEGATIVE_STATUS_PATTERN.search(str(source_claim_text or "")):
        return str(answer or "")
    sentences = [
        sentence.strip()
        for sentence in re.findall(r"[^.!?]+(?:[.!?]+|$)", str(answer or ""))
        if sentence.strip()
    ]
    status_index = next(
        (
            index
            for index, sentence in enumerate(sentences)
            if _NEGATIVE_STATUS_PATTERN.search(sentence)
        ),
        -1,
    )
    if status_index <= 0:
        return str(answer or "")
    return " ".join(
        [sentences[status_index]]
        + sentences[:status_index]
        + sentences[status_index + 1:]
    )


def _named_entities_are_supported(answer, source_text):
    source_terms = _expanded_grounding_terms(source_text)
    source_words = re.findall(r"\b[A-Za-z][A-Za-z0-9]*\b", str(source_text or ""))
    for match in _ENTITY_PATTERN.finditer(answer):
        # "One-on-one" is a service format label, not a proper name.
        if re.fullmatch(
            r"(?:one|1)[- ]on[- ](?:one|1)",
            fold_text(match.group(0)),
        ):
            continue
        entity_terms = _expanded_grounding_terms(match.group(0)).difference({
            "and", "de", "del", "of", "the", "to", "y",
        })
        prefix = answer[:match.start()].rstrip()
        at_sentence_start = not prefix or prefix[-1:] in ".!?"
        if len(entity_terms) == 1 and at_sentence_start:
            continue
        # A model may naturally expand a location acronym already printed by
        # the source (for example, Long Island City for LIC). Accept only an
        # exact multiword initialism present in that same source record.
        entity_words = [
            word
            for word in tokens(match.group(0), keep_stopwords=True)
            if word not in {"and", "de", "del", "of", "the", "to", "y"}
        ]
        initialism = "".join(word[0] for word in entity_words if word)
        if len(initialism) >= 2 and initialism in source_terms:
            continue
        # The inverse is equally natural: a source may spell out a name such
        # as "Artificial Intelligence" while the model uses "AI". Accept a
        # short all-caps acronym only when a title-cased expansion with the
        # same initials appears in this exact source record.
        raw_entity = re.sub(r"[^A-Za-z]", "", match.group(0))
        if re.fullmatch(r"[A-Z]{2,6}", raw_entity):
            if any(
                "".join(word[0] for word in source_words[index:index + len(raw_entity)]).upper()
                == raw_entity
                and all(word[:1].isupper() for word in source_words[index:index + len(raw_entity)])
                for index in range(0, len(source_words) - len(raw_entity) + 1)
            ):
                continue
        if any(term not in source_terms for term in entity_terms):
            return False
    return True


def answer_expresses_evidence_limit(answer):
    """Recognize a concise refusal to claim a detail the source does not confirm."""

    value = fold_text(answer)
    return bool(re.search(
        r"\b(?:can(?:not|'t)|could(?: not|n't)|does(?: not|n't)|is(?: not|n't)|"
        r"not (?:confirmed|listed|specified|shown)|no (?:information|details?))\b",
        value,
    ))


def answers_near_duplicate(answer, prior_answer):
    """Catch a follow-up that merely repeats the latest guide response."""

    current = set(tokens(answer, keep_stopwords=True))
    prior = set(tokens(prior_answer, keep_stopwords=True))
    if not current or not prior:
        return False
    current_text = " ".join(tokens(answer, keep_stopwords=True))
    prior_text = " ".join(tokens(prior_answer, keep_stopwords=True))
    if current_text == prior_text:
        return True
    current_sentences = [
        set(tokens(sentence, keep_stopwords=True))
        for sentence in re.split(r"(?<=[.!?])\s+", str(answer or ""))
    ]
    prior_sentences = [
        set(tokens(sentence, keep_stopwords=True))
        for sentence in re.split(r"(?<=[.!?])\s+", str(prior_answer or ""))
    ]
    sentence_matches = []
    for current_sentence in current_sentences:
        if len(current_sentence) < 5:
            continue
        sentence_matches.append(any(
            len(prior_sentence) >= 5
            and len(current_sentence.intersection(prior_sentence))
            / min(len(current_sentence), len(prior_sentence)) >= 0.85
            for prior_sentence in prior_sentences
        ))
    if sentence_matches and all(sentence_matches):
        return True
    if any(sentence_matches) and not all(sentence_matches):
        return False
    containment = len(current.intersection(prior)) / max(1, min(len(current), len(prior)))
    union = len(current.union(prior))
    return min(len(current), len(prior)) >= 6 and containment >= 0.82 and (
        len(current.intersection(prior)) / max(1, union)
    ) >= 0.62


def question_requests_prior_detail(question, prior_answer):
    """Allow a grounded confirmation when the user asks about an earlier detail."""

    value = fold_text(semantic_question(question))
    if prior_answer and re.search(
        r"\b(?:what|which) (?:does|do|did|will|would) "
        r"(?:(?:that|this|the) (?:class|course|workshop|program|service|page|option)|it|they) "
        r"(?:cover|include|teach|offer|mean|say)\b",
        value,
    ):
        return True
    question_terms = expanded_query_terms(question).difference({
        "answer", "detail", "details", "kind", "page", "tell",
    })
    prior_terms = expanded_query_terms(prior_answer)
    overlap = question_terms.intersection(prior_terms)
    return len(overlap) >= 2 and len(overlap) >= min(3, len(question_terms))


def model_answer_is_grounded(answer, source, question=""):
    """Offline evaluation diagnostic; never a participant-facing response gate."""

    answer = clip_words(re.sub(r"<[^>]+>", " ", str(answer or "")), MAX_MESSAGE_WORDS)
    if not answer or re.search(r"https?://|www\.", answer, flags=re.I):
        return False
    source_text = searchable_text(source)
    source_claim_text = (
        source_excerpt(source, question, limit=MAX_MODEL_EXCERPT_CHARS)
        if question else source_text
    ) or source_text
    if not _claim_numbers(answer).issubset(_claim_numbers(source_text)):
        return False
    if not _claim_number_unit_pairs(answer).issubset(_claim_number_unit_pairs(source_text)):
        return False
    route_identity = urllib.parse.urlsplit(source.get("url", "")).path.replace("-", " ")
    route_identity = re.sub(r"\btechfair\b", "tech fair", route_identity, flags=re.I)
    # The guide may name the site owner even when a service-page excerpt uses
    # only the shorter "FS Digital Equity" label. This does not authorize any
    # program claim; every other answer term still has to come from the record.
    entity_context = (
        f"{source_text} {route_identity} Fortune The Fortune Society "
        "Fortune Society Digital Equity"
    )
    if question and answer_expresses_evidence_limit(answer):
        entity_context += " " + str(question)
    if not _named_entities_are_supported(answer, entity_context):
        return False
    for pattern in _UNIVERSAL_CLAIM_PATTERNS:
        if pattern.search(answer) and not any(row.search(source_text) for row in _UNIVERSAL_CLAIM_PATTERNS):
            return False
    if _answer_conflicts_with_negative_status(answer, source_claim_text):
        return False
    for group in _RISKY_QUALIFIER_GROUPS:
        answer_polarities = _qualifier_polarities(answer, group)
        if answer_polarities and not answer_polarities.issubset(
            _source_qualifier_polarities(source_claim_text, group)
        ):
            return False
    generic = {
        "answer", "digital", "equity", "fortune", "guide", "information",
        "page", "program", "society", "website", "guia", "informacion",
        "pagina", "programa",
    }
    answer_terms = _expanded_grounding_terms(answer).difference(generic)
    source_terms = _expanded_grounding_terms(source_text).difference(generic)
    if len(answer_terms.intersection(source_terms)) < min(2, len(answer_terms)):
        return False
    return True


def parse_model_selection(
    raw,
    question,
    retrieved=None,
    retrieval_scope="site",
    interaction=None,
    routing_question=None,
    prior_answer=None,
    require_answer=False,
):
    retrieved = list(retrieved or retrieve_sources(question))
    interaction = dict(interaction or {})
    language_code = interaction.get("request_language") or "en"
    allowed = {source["id"]: source for source in retrieved}
    parsed = parse_selector_response(raw, allowed)
    if not parsed:
        raise ModelResponseRejected("The model returned an invalid response")
    selected_id = parsed["pick"]
    if selected_id == SELECTOR_ASK:
        if require_answer:
            raise ModelResponseRejected("The model asked instead of providing a safe handoff")
        return model_clarification_response(
            question,
            parsed["answer"],
            retrieval_scope,
        )
    selected = allowed[selected_id]
    answer_text = parsed["answer"]
    if model_requests_personal_details(answer_text):
        raise ModelResponseRejected("The model asked for participant information")
    # The selected ID is the grounding contract. The model has already seen the
    # full candidate record, and the system prompt forbids outside facts. A
    # second lexical classifier rejected valid paraphrases in production, so
    # participant-facing prose now passes through intact.
    message = str(answer_text or "").strip()
    reason = (
        "La respuesta viene de una página aprobada."
        if language_code == "es"
        else "From an approved Digital Equity page."
    )
    return response_contract(
        kind="answer",
        message=message,
        reason=reason,
        sources=[selected],
        question=question,
        model_called=True,
        retrieval_scope=retrieval_scope,
    )


def model_selection_retry_reason(
    raw,
    retrieved,
    interaction=None,
    prior_answer="",
    question="",
    routing_question="",
    require_answer=False,
):
    """Validate only the provider contract and participant privacy boundary.

    The model is responsible for reading the supplied records and writing the
    answer.  Do not run its prose through a second lexical classifier: natural
    paraphrases and complete calendar answers must not become operational
    failures merely because they use different words or exceed an arbitrary
    word count.
    """

    interaction = dict(interaction or {})
    allowed = {source["id"]: source for source in retrieved}
    parsed = parse_selector_response(raw, allowed)
    if not parsed:
        return "invalid response"
    if parsed["pick"] == SELECTOR_ASK:
        if require_answer:
            return "resolved source can answer"
        try:
            model_clarification_response(question, parsed["answer"])
        except ModelResponseRejected:
            if model_requests_personal_details(parsed["answer"]):
                return "personal detail request"
            return "invalid response"
        return ""
    if model_requests_personal_details(parsed["answer"]):
        return "personal detail request"
    return ""


def sanitize_history(history):
    clean = []
    if not isinstance(history, list):
        return clean
    for item in history[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content and not contains_personal_details(content):
            clean.append({"role": role, "content": content[:1600]})
    return clean


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self._request_started = time.monotonic()
        self._request_id = str(uuid.uuid4())
        super().__init__(*args, directory=str(PUBLIC_SITE_ROOT), **kwargs)

    def list_directory(self, path):
        self.send_error(404)
        return None

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if not origin_is_allowed(origin, self.headers.get("Host", "")):
            self.send_error(403)
            return
        self.send_response(204)
        self._cors_headers(origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path in EVALUATION_ASSETS:
            self._serve_evaluation_asset(parsed.path)
            return
        if parsed.path == "/api/evaluation/status":
            self._json(200, EVALUATION_STORE.public_status())
            return
        if parsed.path == "/api/evaluation/session":
            account, token = self._evaluation_account()
            if not account:
                self._json(401, {"error": "Sign in to continue."})
                return
            self._json(200, {
                "account": account,
                "csrf_token": EVALUATION_STORE.csrf_token(token),
            })
            return
        if parsed.path == "/api/evaluation/buckets":
            account, _ = self._require_evaluation_account()
            if not account:
                return
            self._json(200, {
                "buckets": EVALUATION_STORE.list_buckets(account["slot_key"]),
            })
            return
        if parsed.path == "/api/evaluation/conversations":
            account, _ = self._require_evaluation_account()
            if not account:
                return
            query = urllib.parse.parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            self._json(200, {
                "conversations": EVALUATION_STORE.list_conversations(
                    account["slot_key"], limit
                ),
            })
            return
        if parsed.path == "/api/evaluation/prompt-lab":
            account, _ = self._require_evaluation_account()
            if not account:
                return
            self._json(200, {
                "prompt_lab": EVALUATION_STORE.get_prompt_lab(
                    account["slot_key"],
                    PROMPT_POLICY_VERSION,
                    PROMPT_BEHAVIOR_RELEASE,
                ),
            })
            return
        conversation_match = re.fullmatch(
            r"/api/evaluation/conversations/([0-9a-fA-F-]{36})",
            parsed.path,
        )
        if conversation_match:
            account, _ = self._require_evaluation_account()
            if not account:
                return
            try:
                conversation = EVALUATION_STORE.get_conversation(
                    account["slot_key"], conversation_match.group(1)
                )
                self._json(200, {"conversation": conversation})
            except (EvaluationForbidden, EvaluationValidation) as error:
                self._json(404, {"error": str(error)})
            return
        if parsed.path == "/api/evaluation/admin/accounts":
            account, _ = self._require_evaluation_account(role="admin")
            if not account:
                return
            self._json(200, {"accounts": EVALUATION_STORE.list_accounts()})
            return
        if parsed.path == "/health":
            capture_ready = CONVERSATION_RECORDER.check()
            evaluation_ready = EVALUATION_STORE.check()
            service_ready = (
                (not CONVERSATION_RECORDER.required or capture_ready)
                and (not EVALUATION_STORE.enabled or evaluation_ready)
            )
            self._json(200 if service_ready else 503, {
                "status": "ok" if service_ready else "unavailable",
                "model": MODEL,
                "model_enabled": bool(KEY),
                "index_loaded": SITE_INDEX_PATH.exists(),
                "indexed_pages": SITE_INDEX.get("unique_urls", len(SOURCE_BY_ID)),
                "answer_sources": len(ANSWER_SOURCES),
                "authority_counts": SITE_INDEX.get("authority_counts", {}),
                "index_generated_at": SITE_INDEX.get("generated_at"),
                "sources_reviewed_on": KNOWLEDGE["reviewed_on"],
                "prompt_policy": {
                    "version": PROMPT_POLICY_VERSION,
                    "behavior_release": PROMPT_BEHAVIOR_RELEASE,
                },
                "app_version": CONVERSATION_RECORDER.app_version,
                "model_call_limits": {
                    "per_client_hour": MODEL_CALLS_PER_HOUR,
                    "shared_day": MODEL_CALLS_PER_DAY,
                },
                "chat_request_limits": {
                    "per_client_hour": CHAT_REQUESTS_PER_HOUR,
                    "shared_day": CHAT_REQUESTS_PER_DAY,
                    "max_turns_per_conversation": CONVERSATION_RECORDER.max_turns,
                },
                "model_warmup": {
                    "status": MODEL_WARMUP.status(),
                    "cooldown_seconds": MODEL_WARMUP_COOLDOWN,
                    "keep_alive": MODEL_KEEP_ALIVE,
                },
                "calendar_source": CALENDAR_CACHE.status(),
                "conversation_logging": {
                    "capture_mode": CONVERSATION_RECORDER.mode,
                    "database_configured": CONVERSATION_RECORDER.configured,
                    "database_ready": capture_ready,
                    "enabled": CONVERSATION_RECORDER.enabled,
                    "retention_days": CONVERSATION_RECORDER.retention_days,
                    "schema_version": SCHEMA_VERSION,
                },
                "evaluation": {
                    **EVALUATION_STORE.public_status(),
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                },
            })
            return
        if parsed.path == "/api/sources":
            query = urllib.parse.parse_qs(parsed.query)
            include_all = query.get("all") == ["1"]
            sources = ANSWER_SOURCES if include_all else [SOURCE_BY_ID[source_id] for source_id in CORE_IDS]
            self._json(200, {
                "reviewed_on": KNOWLEDGE["reviewed_on"],
                "index_generated_at": SITE_INDEX.get("generated_at"),
                "indexed_pages": SITE_INDEX.get("unique_urls", len(SOURCE_BY_ID)),
                "answer_sources": len(ANSWER_SOURCES),
                "sources": source_payload(sources),
            })
            return
        if parsed.path == "/api/search":
            question = urllib.parse.parse_qs(parsed.query).get("q", [""])[0].strip()
            if not question:
                self._json(400, {"error": "Add a search question."})
                return
            retrieved = retrieve_sources(question)
            self._json(200, {
                "query": question,
                "sources": source_payload(retrieved),
                "related": related_links(question, retrieved),
                "model_called": False,
                "retrieval_scope": "site" if retrieved else "staff",
            })
            return
        super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/evaluation/"):
            self._evaluation_post(path)
            return
        if path not in {"/api/chat", "/api/warmup"}:
            self.send_error(404)
            return
        if not origin_is_allowed(self.headers.get("Origin", ""), self.headers.get("Host", "")):
            self._json(403, {"error": "This browser origin is not allowed."})
            return
        if path == "/api/warmup":
            if not KEY:
                self._json(200, {"status": "disabled", "model": MODEL})
                return
            try:
                warmed = MODEL_WARMUP.ensure(preload_model)
                self._json(200, {
                    "status": "ready",
                    "model": MODEL,
                    "warmed": warmed,
                })
            except Exception:
                self._json(503, {
                    "status": "unavailable",
                    "model": MODEL,
                })
            return
        turn = None
        question = ""
        interaction = {
            "chat_stage": "opening",
            "request_kind": "unknown",
            "request_language": "und",
            "prompt_policy_version": PROMPT_POLICY_VERSION,
        }
        started_at = time.monotonic()
        model_attempted = False
        model_attempts = 0
        retrieval_scope = "staff"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                self._json(400, {"error": "Request size is invalid."})
                return
            request = json.loads(self.rfile.read(length))
            question = str(request.get("message") or "").strip()
            page_context = sanitize_page_context(request.get("page_context"))
            safe_history = sanitize_history(request.get("history"))
            if not question:
                self._json(400, {"error": "Write a question first."})
                return
            if len(question) > MAX_QUESTION_CHARS:
                self._json(400, {
                    "error": f"Question must be {MAX_QUESTION_CHARS} characters or fewer."
                })
                return
            if not CHAT_REQUEST_BUDGET.claim(self._client_identifier()):
                self._json(
                    429,
                    {"error": "The guide has reached its request limit. Please try again later."},
                    headers={"Retry-After": "60"},
                )
                return
            routing_question = contextual_routing_question(question, safe_history)
            interaction = interaction_context(question, safe_history)
            turn = CONVERSATION_RECORDER.begin_turn(
                question=question,
                conversation_id=request.get("conversation_id"),
                conversation_token=request.get("conversation_token"),
                client_event_id=request.get("client_event_id"),
                page_context=capture_page_context(page_context),
                client_surface=request.get("client_surface"),
                history_context=safe_history,
                interaction_context=interaction,
            )
            if turn.duplicate_response:
                token = CONVERSATION_RECORDER.conversation_token(turn.conversation_id)
                if replay_response_is_current(turn.duplicate_response):
                    duplicate = dict(turn.duplicate_response)
                    duplicate["conversation_token"] = token
                    self._json(200, duplicate)
                elif turn.duplicate_response.get("error"):
                    self._json(409, {
                        "error": turn.duplicate_response["error"],
                        "idempotency_complete": True,
                        "conversation_id": turn.conversation_id,
                        "conversation_token": token,
                        "turn_id": turn.turn_id,
                        "client_event_id": turn.client_event_id,
                    })
                else:
                    self._json(409, {
                        "error": "This turn completed, but its answer text was not retained in this capture mode.",
                        "idempotency_complete": True,
                        "conversation_id": turn.conversation_id,
                        "conversation_token": token,
                        "turn_id": turn.turn_id,
                        "client_event_id": turn.client_event_id,
                    })
                return
            if turn.in_progress:
                self._json(
                    409,
                    {
                        "error": "This question is still being processed. Retry shortly with the same client event ID.",
                        "idempotency_complete": False,
                        "conversation_id": turn.conversation_id,
                        "turn_id": turn.turn_id,
                        "client_event_id": turn.client_event_id,
                    },
                    headers={"Retry-After": "2"},
                )
                return
            if contains_personal_details(question):
                self._chat_json(
                    200,
                    privacy_response(question, interaction["request_language"]),
                    turn,
                    question,
                    started_at,
                    privacy_state="blocked",
                    interaction=interaction,
                )
                return
            sensitive_request = needs_human_handoff(question)
            if sensitive_request:
                retrieval_scope = "staff"
                retrieved = [SOURCE_BY_ID["contact"]]
            else:
                retrieval_scope, retrieved = retrieval_plan(routing_question, page_context)
                if not retrieved:
                    retrieval_scope = "site"
            require_model_answer = sensitive_request
            # Retrieval supplies approved evidence for factual answers. When it
            # finds no evidence, the model receives an empty candidate set and can
            # respond conversationally with ASK; generic pages are never injected
            # merely to make an ordinary turn pass a factual-grounding filter.
            prior_answer = next(
                (
                    item.get("content", "")
                    for item in reversed(safe_history)
                    if item.get("role") == "assistant"
                ),
                "",
            )
            if not KEY:
                self._chat_failure(
                    503,
                    "Guide unavailable. Try again.",
                    turn,
                    started_at,
                    error_code="model_disabled",
                    interaction=interaction,
                    retrieval_scope=retrieval_scope,
                    model_called=False,
                    privacy_state=("sensitive_handoff" if sensitive_request else "clear"),
                )
                return
            client_identifier = self._client_identifier()
            if not MODEL_CALL_BUDGET.claim(client_identifier):
                self._chat_failure(
                    429,
                    "Guide busy. Try again shortly.",
                    turn,
                    started_at,
                    error_code="usage_limit",
                    interaction=interaction,
                    retrieval_scope=retrieval_scope,
                    model_called=False,
                    privacy_state=("sensitive_handoff" if sensitive_request else "clear"),
                    headers={"Retry-After": "60"},
                )
                return
            model_sources = [
                CALENDAR_CACHE.source(source)
                if source.get("id") == "calendar"
                else source
                for source in retrieved
            ]
            messages = [{"role": "system", "content": retrieval_prompt(
                routing_question,
                model_sources,
                page_context,
                interaction,
                previous_answer=prior_answer,
            )}]
            model_question = semantic_question(question) or (
                "Ask one short question about what the participant needs from "
                "the Digital Equity site."
            )
            messages.append({
                "role": "user",
                "content": model_question[:MAX_QUESTION_CHARS],
            })
            model_attempted = True
            model_attempts = 1
            raw = self._ollama(messages)
            retry_reason = model_selection_retry_reason(
                raw,
                model_sources,
                interaction,
                prior_answer,
                question,
                routing_question,
                require_model_answer,
            )
            if retry_reason and MODEL_CALL_BUDGET.claim(client_identifier):
                retry_messages = [
                    {
                        "role": "system",
                        "content": build_retry_prompt(
                            messages[0]["content"], retry_reason
                        ),
                    },
                    messages[1],
                ]
                raw = self._ollama(retry_messages)
                model_attempts = 2
            final_validation_reason = model_selection_retry_reason(
                raw,
                model_sources,
                interaction,
                prior_answer,
                question,
                routing_question,
                require_model_answer,
            )
            if (
                final_validation_reason
                and require_model_answer
                and MODEL_CALL_BUDGET.claim(client_identifier)
            ):
                raw = self._ollama([
                    {
                        "role": "system",
                        "content": build_retry_prompt(
                            messages[0]["content"], "personal detail request"
                        ),
                    },
                    messages[1],
                ])
                model_attempts = 3
                final_validation_reason = model_selection_retry_reason(
                    raw,
                    model_sources,
                    interaction,
                    prior_answer,
                    question,
                    routing_question,
                    require_model_answer,
                )
            response = parse_model_selection(
                raw,
                question,
                model_sources,
                retrieval_scope,
                interaction,
                routing_question=routing_question,
                prior_answer=prior_answer,
                require_answer=require_model_answer,
            )
            if sensitive_request:
                response["kind"] = "handoff"
                response["retrieval_scope"] = "staff"
            self._log_model_validation(
                attempts=model_attempts,
                first_reason=retry_reason or "accepted",
                final_reason=final_validation_reason or "accepted",
                response_kind=response.get("kind") or "unknown",
            )
            self._chat_json(
                200,
                response,
                turn,
                question,
                started_at,
                privacy_state=("sensitive_handoff" if sensitive_request else "clear"),
                interaction=interaction,
            )
        except ModelResponseRejected:
            self._log_model_validation(
                attempts=model_attempts,
                first_reason=retry_reason if "retry_reason" in locals() else "rejected",
                final_reason=(
                    final_validation_reason
                    if "final_validation_reason" in locals()
                    else "rejected"
                ),
                response_kind="error",
            )
            self._chat_failure(
                502,
                "Guide unavailable. Try again.",
                turn,
                started_at,
                error_code="model_response_rejected",
                interaction=interaction,
                retrieval_scope=retrieval_scope,
                model_called=model_attempted,
                privacy_state=(
                    "sensitive_handoff"
                    if "sensitive_request" in locals() and sensitive_request
                    else "clear"
                ),
            )
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "The request could not be read."})
        except CaptureUnavailable:
            self._json(503, {
                "error": "The guide could not safely record this question. Please try again shortly."
            })
        except IdempotencyConflict:
            self._json(409, {
                "error": "This client event ID was already used for a different question."
            })
        except ConversationLimit:
            self._json(429, {
                "error": "This conversation reached its turn limit. Start again from the current page."
            })
        except Exception:
            self._chat_failure(
                503,
                "Guide unavailable. Try again.",
                turn,
                started_at,
                error_code="model_unavailable",
                interaction=interaction,
                retrieval_scope=retrieval_scope,
                model_called=model_attempted,
                privacy_state=(
                    "sensitive_handoff"
                    if "sensitive_request" in locals() and sensitive_request
                    else "clear"
                ),
            )

    def do_PUT(self):
        path = urllib.parse.urlsplit(self.path).path
        placement_match = re.fullmatch(
            r"/api/evaluation/conversations/([0-9a-fA-F-]{36})/placement",
            path,
        )
        note_match = re.fullmatch(
            r"/api/evaluation/conversations/([0-9a-fA-F-]{36})/note",
            path,
        )
        annotation_match = re.fullmatch(
            r"/api/evaluation/conversations/([0-9a-fA-F-]{36})/annotations/([0-9a-fA-F-]{36})",
            path,
        )
        prompt_match = re.fullmatch(
            r"/api/evaluation/prompt-proposals/([0-9a-fA-F-]{36})",
            path,
        )
        prompt_status_match = re.fullmatch(
            r"/api/evaluation/prompt-proposals/([0-9a-fA-F-]{36})/status",
            path,
        )
        if not (
            placement_match or note_match or annotation_match
            or prompt_match or prompt_status_match
        ):
            self.send_error(404)
            return
        account, _ = self._require_evaluation_account(
            mutation=True,
            role="admin" if prompt_status_match else None,
        )
        if not account:
            return
        try:
            request = self._read_json()
            if placement_match:
                evaluation = EVALUATION_STORE.move_conversation(
                    account["slot_key"],
                    placement_match.group(1),
                    request.get("bucket_id"),
                    request.get("expected_version"),
                    request.get("expected_transcript_version"),
                    request.get("operation_id"),
                )
                self._json(200, {"evaluation": evaluation})
            elif note_match:
                evaluation = EVALUATION_STORE.save_note(
                    account["slot_key"],
                    note_match.group(1),
                    request.get("note"),
                    request.get("expected_version"),
                    request.get("expected_transcript_version"),
                    request.get("operation_id"),
                )
                self._json(200, {"evaluation": evaluation})
            elif annotation_match:
                annotation = EVALUATION_STORE.save_annotation(
                    account["slot_key"],
                    annotation_match.group(1),
                    annotation_match.group(2),
                    request.get("category"),
                    request.get("note"),
                    request.get("expected_version"),
                    request.get("expected_transcript_version"),
                    request.get("operation_id"),
                )
                self._json(200, {"annotation": annotation})
            elif prompt_match:
                proposal = EVALUATION_STORE.update_prompt_proposal(
                    account["slot_key"],
                    prompt_match.group(1),
                    request.get("title"),
                    request.get("module_values"),
                    request.get("expected_version"),
                    request.get("operation_id"),
                )
                self._json(200, {"proposal": proposal})
            else:
                proposal = EVALUATION_STORE.set_prompt_proposal_status(
                    account["slot_key"],
                    prompt_status_match.group(1),
                    request.get("status"),
                    request.get("expected_version"),
                    request.get("operation_id"),
                )
                self._json(200, {"proposal": proposal})
        except EvaluationConflict as error:
            self._json(409, {"error": str(error), "current": error.current})
        except EvaluationForbidden as error:
            self._json(404, {"error": str(error)})
        except (EvaluationValidation, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error) or "The request could not be read."})
        except EvaluationUnavailable:
            self._json(503, {"error": "Evaluation access is unavailable."})

    def _evaluation_post(self, path):
        if not self._evaluation_origin_allowed():
            self._json(403, {"error": "This browser request is not allowed."})
            return
        try:
            if path == "/api/evaluation/auth/login":
                if not LOGIN_REQUEST_BUDGET.claim(self._client_identifier()):
                    self._json(
                        429,
                        {"error": "Too many sign-in attempts. Try again later."},
                        headers={"Retry-After": "60"},
                    )
                    return
                request = self._read_json()
                result = EVALUATION_STORE.login(
                    request.get("email"), request.get("password")
                )
                token = result.pop("session_token")
                self._json(
                    200,
                    result,
                    headers={"Set-Cookie": self._session_cookie(token)},
                )
                return
            if path == "/api/evaluation/invitations/claim":
                request = self._read_json()
                result = EVALUATION_STORE.claim_invitation(
                    request.get("token"),
                    request.get("email"),
                    request.get("display_name"),
                    request.get("password"),
                )
                token = result.pop("session_token")
                self._json(
                    200,
                    result,
                    headers={"Set-Cookie": self._session_cookie(token)},
                )
                return
            if path == "/api/evaluation/auth/logout":
                account, token = self._require_evaluation_account(mutation=True)
                if not account:
                    return
                EVALUATION_STORE.logout(token)
                self._json(
                    200,
                    {"status": "signed_out"},
                    headers={"Set-Cookie": self._expired_session_cookie()},
                )
                return
            if path == "/api/evaluation/buckets":
                account, _ = self._require_evaluation_account(mutation=True)
                if not account:
                    return
                request = self._read_json()
                bucket = EVALUATION_STORE.create_bucket(
                    account["slot_key"],
                    request.get("label"),
                    request.get("color_key"),
                    request.get("operation_id"),
                )
                self._json(201, {"bucket": bucket})
                return
            if path == "/api/evaluation/prompt-proposals":
                account, _ = self._require_evaluation_account(mutation=True)
                if not account:
                    return
                request = self._read_json()
                proposal = EVALUATION_STORE.create_prompt_proposal(
                    account["slot_key"],
                    request.get("title"),
                    request.get("module_values"),
                    PROMPT_POLICY_VERSION,
                    request.get("proposal_id"),
                    request.get("operation_id"),
                )
                self._json(201, {"proposal": proposal})
                return
            comment_match = re.fullmatch(
                r"/api/evaluation/prompt-proposals/([0-9a-fA-F-]{36})/comments",
                path,
            )
            if comment_match:
                account, _ = self._require_evaluation_account(mutation=True)
                if not account:
                    return
                request = self._read_json()
                comment = EVALUATION_STORE.add_prompt_proposal_comment(
                    account["slot_key"],
                    comment_match.group(1),
                    request.get("comment"),
                    request.get("operation_id"),
                )
                self._json(201, {"comment": comment})
                return
            invitation_match = re.fullmatch(
                r"/api/evaluation/admin/accounts/(admin|editor-[123])/invitation",
                path,
            )
            if invitation_match:
                account, _ = self._require_evaluation_account(
                    mutation=True, role="admin"
                )
                if not account:
                    return
                request = self._read_json()
                token = EVALUATION_STORE.issue_invitation(
                    invitation_match.group(1),
                    email=request.get("email"),
                    actor_slot=account["slot_key"],
                    operation_id=request.get("operation_id"),
                )
                self._json(201, {
                    "invitation_path": (
                        "/evaluation#invite="
                        + urllib.parse.quote(token, safe="")
                    ),
                    "expires_in_seconds": EVALUATION_STORE.invite_seconds,
                })
                return
            self.send_error(404)
        except AuthenticationFailed as error:
            self._json(401, {"error": str(error)})
        except EvaluationConflict as error:
            self._json(409, {"error": str(error), "current": error.current})
        except EvaluationForbidden as error:
            self._json(403, {"error": str(error)})
        except (EvaluationValidation, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error) or "The request could not be read."})
        except EvaluationUnavailable:
            self._json(503, {"error": "Evaluation access is unavailable."})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise EvaluationValidation("Request size is invalid.") from error
        if length < 1 or length > MAX_BODY:
            raise EvaluationValidation("Request size is invalid.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise EvaluationValidation("The request must be a JSON object.")
        return value

    def _evaluation_origin_allowed(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if not origin or fetch_site not in {"", "none", "same-origin"}:
            return False
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError:
            return False
        host = self.headers.get("Host", "").strip().lower()
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() == host
            and not parsed.username
            and not parsed.password
        )

    def _session_token(self):
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return ""
        try:
            cookies = http.cookies.SimpleCookie()
            cookies.load(cookie_header)
            morsel = cookies.get(COOKIE_NAME)
            return morsel.value if morsel else ""
        except http.cookies.CookieError:
            return ""

    def _evaluation_account(self):
        token = self._session_token()
        return EVALUATION_STORE.authenticate(token), token

    def _require_evaluation_account(self, *, mutation=False, role=None):
        account, token = self._evaluation_account()
        if not account:
            self._json(401, {"error": "Sign in to continue."})
            return None, ""
        if role and account.get("role") != role:
            self._json(403, {"error": "This account cannot use that action."})
            return None, ""
        if mutation:
            if not self._evaluation_origin_allowed():
                self._json(403, {"error": "This browser request is not allowed."})
                return None, ""
            if not EVALUATION_STORE.csrf_matches(
                token, self.headers.get("X-CSRF-Token", "")
            ):
                self._json(403, {"error": "Refresh the page and try again."})
                return None, ""
        return account, token

    def _session_cookie(self, token):
        return (
            f"{COOKIE_NAME}={token}; Path=/; "
            f"Max-Age={EVALUATION_STORE.absolute_seconds}; "
            "Secure; HttpOnly; SameSite=Strict"
        )

    @staticmethod
    def _expired_session_cookie():
        return (
            f"{COOKIE_NAME}=; Path=/; Max-Age=0; "
            "Secure; HttpOnly; SameSite=Strict"
        )

    def _serve_evaluation_asset(self, path):
        asset = EVALUATION_ASSETS[path]
        try:
            body = asset.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript", "text/javascript"
        }:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; connect-src 'self'; "
            "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self' https://static.wixstatic.com; object-src 'none'; "
            "script-src 'self'; style-src 'self'",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chat_json(
        self,
        status,
        response,
        turn,
        question,
        started_at,
        *,
        privacy_state="clear",
        error_code=None,
        interaction=None,
    ):
        interaction = dict(interaction or {})
        response = dict(response)
        response.update({
            "chat_stage": interaction.get("chat_stage") or "unknown",
            "request_kind": interaction.get("request_kind") or "unknown",
            "request_language": interaction.get("request_language") or "und",
            "response_language": detect_language(response.get("message") or ""),
            "prompt_policy_version": interaction.get("prompt_policy_version") or PROMPT_POLICY_VERSION,
        })
        enriched = response_with_ids(
            response,
            turn,
            mode=turn.capture_mode,
            stored=turn.persisted,
            conversation_token=CONVERSATION_RECORDER.conversation_token(
                turn.conversation_id
            ),
        )
        CONVERSATION_RECORDER.complete_turn(
            turn,
            question=question,
            response=enriched,
            privacy_state=privacy_state,
            latency_ms=round((time.monotonic() - started_at) * 1000),
            error_code=error_code,
        )
        self._json(status, enriched)

    def _chat_failure(
        self,
        status,
        message,
        turn,
        started_at,
        *,
        error_code,
        interaction=None,
        retrieval_scope="staff",
        model_called=False,
        privacy_state="clear",
        headers=None,
    ):
        """Return an operational error without fabricating an assistant turn."""

        if turn is not None:
            try:
                CONVERSATION_RECORDER.fail_turn(
                    turn,
                    latency_ms=round((time.monotonic() - started_at) * 1000),
                    error_code=error_code,
                    model=MODEL,
                    model_called=model_called,
                    retrieval_scope=retrieval_scope,
                    privacy_state=privacy_state,
                    interaction_context=dict(interaction or {}),
                )
            except CaptureUnavailable:
                self._json(
                    503,
                    {
                        "error": "The guide could not safely record this question. Please try again shortly.",
                        "model_called": bool(model_called),
                    },
                )
                return
        self._json(
            status,
            {
                "error": message,
                "model_called": bool(model_called),
            },
            headers=headers,
        )

    def _ollama(self, messages):
        data = ollama_request({
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": MODEL_OUTPUT_SCHEMA,
            "keep_alive": MODEL_KEEP_ALIVE,
            "options": {
                "temperature": 0,
                "seed": MODEL_SEED,
            },
        })
        MODEL_WARMUP.mark_ready()
        return data.get("message", {}).get("content") or ""

    def _client_identifier(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        try:
            return self.client_address[0]
        except (AttributeError, IndexError, TypeError):
            return "unknown"

    def _log_model_validation(self, *, attempts, first_reason, final_reason, response_kind):
        """Log bounded validator outcomes without question or response content."""

        request_id = getattr(self, "_request_id", "")
        if not request_id:
            return
        print(json.dumps({
            "event": "model_validation",
            "request_id": request_id,
            "attempts": int(attempts),
            "first_reason": str(first_reason)[:80],
            "final_reason": str(final_reason)[:80],
            "response_kind": str(response_kind)[:24],
        }, separators=(",", ":")), flush=True)

    def _cors_headers(self, origin=None):
        origin = (origin or self.headers.get("Origin", "")).rstrip("/")
        if origin and origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, status, value, *, headers=None):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        for name, header_value in (headers or {}).items():
            self.send_header(str(name), str(header_value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("X-Request-ID", self._request_id)
        super().end_headers()

    def log_message(self, _format, *args):
        if not str(_format).startswith('"%s"'):
            return
        try:
            status = int(args[1]) if len(args) > 1 else None
        except (TypeError, ValueError):
            status = None
        path = urllib.parse.urlsplit(getattr(self, "path", "")).path
        path = re.sub(
            r"/[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}",
            "/:id",
            path,
        )
        print(json.dumps({
            "event": "http_request",
            "request_id": self._request_id,
            "method": getattr(self, "command", ""),
            "path": path[:160],
            "status": status,
            "duration_ms": round((time.monotonic() - self._request_started) * 1000),
        }, separators=(",", ":")), flush=True)


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    CONVERSATION_RECORDER.open()
    EVALUATION_STORE.open()
    print("Digital Equity Website Guide")
    print("  http://%s:%d" % (HOST, PORT))
    print("  model=%s  key=%s  indexed_pages=%d  answer_sources=%d" % (
        MODEL,
        "set" if KEY else "MISSING",
        SITE_INDEX.get("unique_urls", len(SOURCE_BY_ID)),
        len(ANSWER_SOURCES),
    ))
    try:
        with ThreadingServer((HOST, PORT), Handler) as server:
            threading.Thread(
                target=warm_model_quietly,
                name="fortune-model-warmup",
                daemon=True,
            ).start()
            threading.Thread(
                target=warm_calendar_quietly,
                name="digital-equity-calendar-warmup",
                daemon=True,
            ).start()
            server.serve_forever()
    finally:
        EVALUATION_STORE.close()
        CONVERSATION_RECORDER.close()
