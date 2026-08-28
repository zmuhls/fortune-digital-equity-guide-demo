#!/usr/bin/env python3
"""Build text views of reviewed Fortune sources and the isolated Website Guide."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import pathlib
import re
import shutil
import tempfile
import urllib.parse
from typing import Any
from html.parser import HTMLParser


ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "site-index.json"
MANIFEST_PATH = ROOT / "replica-manifest.json"
SNAPSHOT_ROOT = ROOT / "replica-snapshots"
CALENDAR_SOURCE_PATH = ROOT / "calendar-source.json"
SIDECAR_TEMPLATE_PATH = ROOT / "index.html"
OUTPUT_PATH = ROOT / "_site"
ALLOWED_HOSTS = {"fortunedigitalequity.org", "www.fortunedigitalequity.org"}
CALENDAR_ROUTE_PATH = "/calendar"
CALENDAR_SOURCE_URL = "https://www.fortunedigitalequity.org/calendar"
SHARED_ASSETS = (
    "styles.css",
    "guide-core.js",
    "app.js",
    "site.js",
    "config.js",
    "site-index.json",
    "replica-manifest.json",
    "replica-shell.css",
    "replica-shell.js",
    "embed-frame.js",
)
SIDECAR_OUTPUT = "sidecar.html"
REPLICA_MARKER = 'data-fortune-replica="true"'
REPLICA_SHELL_CSS_VERSION = "20260828-calendar-view-1"
REPLICA_SHELL_JS_VERSION = "20260820-text-source-1"
FORBIDDEN_SNAPSHOT_PATTERNS = (
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"<\s*(?:object|embed|iframe|form|template)\b", re.IGNORECASE),
    re.compile(r"\son[a-z][a-z0-9_-]*\s*=", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"wix-viewer-model", re.IGNORECASE),
    re.compile(r"X-XSRF-TOKEN", re.IGNORECASE),
)
CSP = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "connect-src 'self'; "
    "font-src 'none'; "
    "frame-src 'self'; "
    "img-src 'none'; "
    "media-src 'none'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "form-action 'none'"
)

VISUAL_SCAFFOLD = (
    re.compile(r"^(?:an?\s+)?icon representing\b", re.I),
    re.compile(r"^(?:image|photo|photograph)\s+(?:of|showing)\b", re.I),
    re.compile(r"^qr ?code\b", re.I),
    re.compile(r"^a digital navigator helping\b", re.I),
    re.compile(r"^participant being helped\b", re.I),
    re.compile(r"^the crowd at the annual fortune society tech fair\b", re.I),
    re.compile(r"^profile photo of\b", re.I),
    re.compile(r"^an animation of\b", re.I),
    re.compile(r"^a series of .+ icons?\b", re.I),
    re.compile(r"^a collage of .+ photos?\b", re.I),
    re.compile(r"^logo for\b", re.I),
    re.compile(r"^.{1,120}\bicon drawn by artificial intelligence$", re.I),
    re.compile(r"^.{1,120}\b(?:logo|icon|stock image|splash image|collage)$", re.I),
    re.compile(r"^.+\s+(?:badge|clip art)$", re.I),
    re.compile(r"^.+\.(?:gif|jpe?g|png|webp)$", re.I),
)
BOILERPLATE_PHRASES = (
    "double click on the text box",
    "this space is a great opportunity",
    "every website has a story",
    "use tab to navigate",
    "loading days",
    "book now",
)
TRANSIENT_SNAPSHOT_PATTERNS = (
    re.compile(r"^no sessions in (?:the )?next \d+ days\.?$", re.I),
    re.compile(r"^.+\([A-Z]{2,5}\) time zone: .+$", re.I),
)
FAQ_HEADING = "frequently asked questions"


class _SnapshotSemanticParser(HTMLParser):
    """Project inert, reviewed main-frame HTML into a small semantic tree.

    Wix's layout is almost entirely ``div`` based, but its rendered captures
    retain headings, lists, links, breadcrumbs, and the native disclosures
    created by the capture step.  This parser deliberately ignores Wix classes
    and ids so the projection follows the public document rather than a page
    specific visual template.
    """

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
    _TEXT_TAGS = {
        "address", "blockquote", "dd", "dt", "figcaption", "p",
        "summary",
    }
    _SKIP_ATTRIBUTES = {
        "data-replica-embed-placeholder",
        "data-replica-static-preview-note",
        "data-replica-static-preview",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[dict] = []
        self.footer_links: list[dict[str, str]] = []
        self._containers: list[list[dict]] = [self.nodes]
        self._main_depth = 0
        self._skip_depth = 0
        self._skip_roots: set[int] = set()
        self._nav_depth = 0
        self._list_stack: list[tuple[str, dict]] = []
        self._item_stack: list[dict] = []
        self._details_stack: list[dict] = []
        self._text_frames: list[dict] = []
        self._anchor_stack: list[dict] = []
        self._div_frames: list[dict] = []
        self._footer_depth = 0
        self._footer_anchor_stack: list[dict] = []

    @staticmethod
    def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name.casefold(): value or "" for name, value in attrs}

    @staticmethod
    def _is_main(tag: str, attrs: dict[str, str]) -> bool:
        return tag == "main" and (
            attrs.get("id") == "PAGES_CONTAINER"
            or attrs.get("data-main-content", "").casefold() == "true"
        )

    def _append(self, node: dict) -> None:
        self._containers[-1].append(node)

    def _nearest_detail(self) -> dict | None:
        return self._details_stack[-1] if self._details_stack else None

    def _inside_summary(self) -> bool:
        return any(frame["tag"] == "summary" for frame in self._text_frames)

    def _mark_div_content(self) -> None:
        if self._div_frames:
            self._div_frames[-1]["has_content_child"] = True

    def _start_text_frame(self, tag: str) -> None:
        anchor = self._anchor_stack[-1] if self._anchor_stack else None
        if anchor is not None:
            anchor["has_semantic_child"] = True
        self._text_frames.append({
            "tag": tag,
            "text": [],
            "href": anchor["href"] if anchor else "",
            "live_action": bool(anchor and anchor["live_action"]),
            "links": [],
        })

    def _finish_text_frame(self, tag: str) -> None:
        if not self._text_frames:
            return
        frame = self._text_frames.pop()
        if frame["tag"] != tag:
            return
        text = clean_source_fragment(" ".join(str(value) for value in frame["text"]))
        if not text:
            return
        links = [link for link in frame["links"] if link.get("label")]
        href = frame["href"]
        if not href and len(links) == 1 and clean_link_label(links[0]["label"]) == text:
            href = links[0]["href"]
        if tag == "summary":
            detail = self._nearest_detail()
            if detail is not None:
                detail["summary"] = text
            return
        node_type = "heading" if tag.startswith("h") else "paragraph"
        node = {
            "type": node_type,
            "text": text,
            "href": href,
            "live_action": bool(frame["live_action"]),
            "links": links,
        }
        if node_type == "heading":
            node["level"] = int(tag[1])
        self._append(node)

    def _finish_anchor(self) -> None:
        if not self._anchor_stack:
            return
        anchor = self._anchor_stack.pop()
        label = clean_link_label(" ".join(str(value) for value in anchor["text"]))
        if not label:
            label = clean_link_label(anchor["aria_label"])
        if not label:
            return
        link = {
            "href": anchor["href"],
            "label": label,
            "live_action": bool(anchor["live_action"]),
        }
        for frame in self._text_frames:
            frame["links"].append(link)
        if (
            not anchor["has_semantic_child"]
            and not self._text_frames
            and not self._inside_summary()
        ):
            self._append({
                "type": "link",
                "text": label,
                "href": anchor["href"],
                "live_action": bool(anchor["live_action"]),
                "links": [link],
            })

    def _finish_footer_anchor(self) -> None:
        if not self._footer_anchor_stack:
            return
        anchor = self._footer_anchor_stack.pop()
        # Social icons and logo links only expose aria labels. Keep the public
        # text links (for example the phone, email, and Media Kit) rather than
        # recreating the visual footer controls in a text mirror.
        label = clean_link_label(" ".join(str(value) for value in anchor["text"]))
        if label:
            self.footer_links.append({"href": str(anchor["href"]), "label": label})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = self._attributes(attrs)
        if not self._main_depth:
            if self._is_main(tag, values):
                self._main_depth = 1
            return

        self._main_depth += 1
        if self._footer_depth:
            self._footer_depth += 1
            if tag == "a":
                self._footer_anchor_stack.append({"href": values.get("href", ""), "text": []})
            return
        if tag == "footer" or values.get("id", "").casefold() == "site_footer":
            self._footer_depth = 1
            return
        if (
            tag in self._SKIP_TAGS
            or values.get("id", "").casefold() == "site_header"
            or any(name in values for name in self._SKIP_ATTRIBUTES)
            or values.get("data-replica-inert") == "form"
        ):
            self._skip_depth += 1
            self._skip_roots.add(self._main_depth)
            return
        if self._skip_depth:
            return

        if tag == "div":
            self._mark_div_content()
            self._div_frames.append({"text": [], "has_content_child": False})
            return
        if tag == "nav":
            self._nav_depth += 1
        if tag == "a":
            self._mark_div_content()
            self._anchor_stack.append({
                "href": values.get("href", ""),
                "aria_label": values.get("aria-label", ""),
                "text": [],
                "has_semantic_child": False,
                "live_action": values.get("data-replica-live-action", "").casefold() == "true",
            })
            return
        if tag == "img" and self._anchor_stack:
            # The Calendar's official downloadable schedule is an image-only
            # anchor. Preserve its meaningful alt text without turning ordinary
            # decorative images into source prose.
            anchor = self._anchor_stack[-1]
            href = str(anchor["href"])
            parsed = urllib.parse.urlsplit(href)
            if (
                parsed.scheme == "https"
                and parsed.hostname in ALLOWED_HOSTS
                and parsed.path.casefold().endswith(".pdf")
            ):
                label = clean_link_label(values.get("alt", ""))
                if label:
                    anchor["text"].append(label)
            return
        if tag == "details":
            self._mark_div_content()
            node = {"type": "details", "summary": "", "content": [], "open": "open" in values}
            self._append(node)
            self._details_stack.append(node)
            self._containers.append(node["content"])
            return
        if tag in {"ul", "ol"}:
            self._mark_div_content()
            node = {
                "type": "list",
                "ordered": tag == "ol",
                "breadcrumb": tag == "ol" and self._nav_depth > 0,
                "items": [],
            }
            self._append(node)
            self._list_stack.append((tag, node))
            self._containers.append(node["items"])
            return
        if tag == "li" and self._list_stack:
            self._mark_div_content()
            node = {"type": "item", "content": [], "text": [], "links": []}
            self._append(node)
            self._item_stack.append(node)
            self._containers.append(node["content"])
            return
        if tag in self._TEXT_TAGS or re.fullmatch(r"h[1-6]", tag):
            self._mark_div_content()
            self._start_text_frame(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._main_depth:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if not self._main_depth:
            return
        if self._footer_depth:
            if tag == "a":
                self._finish_footer_anchor()
            self._footer_depth -= 1
            self._main_depth -= 1
            return
        if self._main_depth in self._skip_roots:
            self._skip_depth -= 1
            self._skip_roots.remove(self._main_depth)
            self._main_depth -= 1
            return
        if not self._skip_depth:
            if tag == "a":
                self._finish_anchor()
            elif tag == "div" and self._div_frames:
                frame = self._div_frames.pop()
                if not frame["has_content_child"]:
                    text = clean_source_fragment(" ".join(str(value) for value in frame["text"]))
                    if text:
                        self._append({"type": "paragraph", "text": text, "href": ""})
            elif tag in self._TEXT_TAGS or re.fullmatch(r"h[1-6]", tag):
                self._finish_text_frame(tag)
            elif tag == "li" and self._item_stack:
                item = self._item_stack.pop()
                self._containers.pop()
                if not item["content"]:
                    text = clean_source_fragment(" ".join(str(value) for value in item["text"]))
                    if text:
                        self._append({"type": "paragraph", "text": text, "href": ""})
            elif tag in {"ul", "ol"} and self._list_stack:
                self._list_stack.pop()
                self._containers.pop()
            elif tag == "details" and self._details_stack:
                self._details_stack.pop()
                self._containers.pop()
            elif tag == "nav" and self._nav_depth:
                self._nav_depth -= 1
        self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._main_depth or self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._footer_depth:
            if self._footer_anchor_stack:
                self._footer_anchor_stack[-1]["text"].append(text)
            return
        for frame in self._text_frames:
            frame["text"].append(text)
        if self._anchor_stack:
            self._anchor_stack[-1]["text"].append(text)
        if self._item_stack:
            self._item_stack[-1]["text"].append(text)
        if self._div_frames:
            self._div_frames[-1]["text"].append(text)


class _HeaderNavigationParser(HTMLParser):
    """Extract the public, captured site navigation without Wix controls."""

    _STRUCTURAL_TAGS = {"nav", "ul", "ol", "li", "details", "summary", "a"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.navigation_roots: list[dict] = []
        self._header_depth = 0
        self._nav_depth = 0
        self._stack: list[dict] = []

    @staticmethod
    def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name.casefold(): value or "" for name, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = self._attributes(attrs)
        if not self._header_depth:
            if values.get("id", "").casefold() == "site_header":
                self._header_depth = 1
            return

        self._header_depth += 1
        if tag == "nav" and not self._nav_depth:
            self._nav_depth = 1
        elif self._nav_depth:
            self._nav_depth += 1
        if not self._nav_depth or tag not in self._STRUCTURAL_TAGS:
            return
        node = {"tag": tag, "attrs": values, "text": [], "children": []}
        if self._stack:
            self._stack[-1]["children"].append(node)
        else:
            self.navigation_roots.append(node)
        self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if not self._header_depth:
            return
        if self._nav_depth:
            if tag in self._STRUCTURAL_TAGS and self._stack:
                for index in range(len(self._stack) - 1, -1, -1):
                    if self._stack[index]["tag"] == tag:
                        self._stack = self._stack[:index]
                        break
            self._nav_depth -= 1
        self._header_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._nav_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        for node in reversed(self._stack):
            if node["tag"] in {"a", "summary"}:
                node["text"].append(text)
                break


class BuildError(RuntimeError):
    """Raised when a source or generated artifact fails a publication gate."""


def route_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise BuildError(f"route is outside the approved HTTPS host: {url!r}")
    if parsed.query or parsed.fragment:
        raise BuildError(f"route contains a query or fragment: {url!r}")
    path = parsed.path.rstrip("/") or "/"
    parts = pathlib.PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in parts[1:]):
        raise BuildError(f"route contains an unsafe path component: {url!r}")
    return path


def load_routes() -> list[dict]:
    try:
        document = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read {INDEX_PATH}: {error}") from error

    pages = document.get("pages")
    declared_count = document.get("unique_urls")
    if not isinstance(pages, list) or not pages:
        raise BuildError("site-index.json must contain a non-empty pages list")
    if declared_count != len(pages):
        raise BuildError(
            f"site-index.json declares {declared_count!r} URLs but contains {len(pages)} pages"
        )

    routes = []
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for position, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise BuildError(f"page {position} is not an object")
        url = str(page.get("url") or "").strip()
        page_id = str(page.get("id") or "").strip()
        if not url or not page_id:
            raise BuildError(f"page {position} is missing url or id")
        path = route_path(url)
        if path in seen_paths:
            raise BuildError(f"duplicate route path: {path}")
        if page_id in seen_ids:
            raise BuildError(f"duplicate page id: {page_id}")
        seen_paths.add(path)
        seen_ids.add(page_id)
        routes.append({
            "path": path,
            "sourceUrl": url,
            "pageId": page_id,
            "page": page,
        })

    if "/" not in seen_paths:
        raise BuildError("site-index.json does not contain the root route")
    return sorted(routes, key=lambda route: route["path"])


def _calendar_string(value: object, field: str, maximum: int = 500) -> str:
    """Return a bounded source string or stop the static build safely."""

    result = clean_source_fragment(value)
    if not result or len(result) > maximum:
        raise BuildError(f"current calendar source has an invalid {field}")
    return result


def _calendar_event_list(value: object, field: str, *, agenda: bool) -> list[dict[str, str]]:
    """Validate the normalized, source-captured event fields rendered in static HTML."""

    if not isinstance(value, list) or not 1 <= len(value) <= 60:
        raise BuildError(f"current calendar source has no usable {field}")
    required = ("date", "date_label", "title")
    if agenda:
        required += ("time", "duration", "location", "registration_url")
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BuildError(f"current calendar source has an invalid {field} entry")
        entry = {
            name: _calendar_string(item.get(name), f"{field} {name}")
            for name in required
        }
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", entry["date"]):
            raise BuildError(f"current calendar source has an invalid {field} date")
        if agenda and entry["registration_url"] != CALENDAR_SOURCE_URL:
            raise BuildError("current calendar registration must stay on the official calendar")
        result.append(entry)
    return result


def load_calendar_source() -> dict[str, Any]:
    """Load the reviewed current-calendar supplement used by the static view.

    The main replica snapshot is intentionally static and can be older than a
    calendar update. This small, versioned source record keeps Pages and the
    Railway static mirror on the same reviewed official destinations without
    fetching a changing schedule during either build.
    """

    try:
        document = json.loads(CALENDAR_SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read current calendar source: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 3:
        raise BuildError("current calendar source has an unsupported schema")

    source_url = str(document.get("source_url") or "")
    source_parts = urllib.parse.urlsplit(source_url)
    if (
        source_url != CALENDAR_SOURCE_URL
        or source_parts.scheme != "https"
        or source_parts.hostname not in ALLOWED_HOSTS
        or source_parts.path.rstrip("/") != CALENDAR_ROUTE_PATH
    ):
        raise BuildError("current calendar source does not point to the official calendar")

    download = document.get("document")
    if not isinstance(download, dict):
        raise BuildError("current calendar source has no downloadable schedule")
    document_url = str(download.get("url") or "")
    document_parts = urllib.parse.urlsplit(document_url)
    if (
        document_parts.scheme != "https"
        or document_parts.hostname not in ALLOWED_HOSTS
        or not document_parts.path.startswith("/_files/ugd/")
        or not document_parts.path.casefold().endswith(".pdf")
        or document_parts.username
        or document_parts.password
    ):
        raise BuildError("current calendar download is not an approved official PDF")

    captured_at = str(document.get("captured_at") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", captured_at):
        raise BuildError("current calendar source has an invalid capture timestamp")
    content_sha256 = str(download.get("sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", content_sha256):
        raise BuildError("current calendar source has an invalid document hash")
    label = clean_link_label(download.get("label"))
    if not label:
        raise BuildError("current calendar source is missing its public download label")

    agenda = document.get("agenda")
    if not isinstance(agenda, dict):
        raise BuildError("current calendar source has no captured agenda")
    agenda_captured_at = str(agenda.get("captured_at") or "")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z", agenda_captured_at):
        raise BuildError("current calendar agenda has an invalid capture timestamp")
    week = agenda.get("week")
    if not isinstance(week, dict):
        raise BuildError("current calendar agenda has no visible week")
    week_label = _calendar_string(week.get("label"), "agenda week label")
    week_selected = _calendar_string(week.get("selected"), "agenda selected day")
    week_days = week.get("days")
    if not isinstance(week_days, list) or len(week_days) != 7:
        raise BuildError("current calendar agenda must contain seven visible days")
    normalized_days = [_calendar_string(day, "agenda week day") for day in week_days]
    agenda_events = _calendar_event_list(agenda.get("events"), "agenda events", agenda=True)

    schedule = document.get("pdf_schedule")
    if not isinstance(schedule, dict):
        raise BuildError("current calendar source has no readable PDF schedule")
    schedule_title = _calendar_string(schedule.get("title"), "PDF schedule title")
    schedule_month = _calendar_string(schedule.get("month"), "PDF schedule month")
    schedule_theme = clean_source_fragment(schedule.get("theme"))
    default_hours = _calendar_string(schedule.get("default_hours"), "PDF schedule default hours")
    location = schedule.get("location")
    if not isinstance(location, dict):
        raise BuildError("current calendar source has no PDF schedule location")
    schedule_location = {
        "name": _calendar_string(location.get("name"), "PDF schedule location name"),
        "address": _calendar_string(location.get("address"), "PDF schedule location address"),
    }
    schedule_events = _calendar_event_list(schedule.get("events"), "PDF schedule events", agenda=False)
    support = schedule.get("support")
    if not isinstance(support, list) or not support:
        raise BuildError("current calendar source has no PDF support schedule")
    support_lines = [_calendar_string(line, "PDF support schedule") for line in support]
    registration_note = _calendar_string(schedule.get("registration_note"), "PDF registration note")
    return {
        "source_url": source_url,
        "captured_at": captured_at,
        "document": {
            "url": document_url,
            "label": label,
            "sha256": content_sha256,
        },
        "agenda": {
            "captured_at": agenda_captured_at,
            "week": {
                "label": week_label,
                "selected": week_selected,
                "days": normalized_days,
            },
            "events": agenda_events,
        },
        "pdf_schedule": {
            "title": schedule_title,
            "month": schedule_month,
            "theme": schedule_theme,
            "location": schedule_location,
            "default_hours": default_hours,
            "events": schedule_events,
            "support": support_lines,
            "registration_note": registration_note,
        },
    }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_snapshot_path(value: str) -> pathlib.Path:
    pure = pathlib.PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or pure.suffixes[-2:] != [".html", ".gz"]:
        raise BuildError(f"unsafe snapshot file: {value!r}")
    path = ROOT / pathlib.Path(pure.as_posix())
    try:
        path.relative_to(SNAPSHOT_ROOT)
    except ValueError as error:
        raise BuildError(f"snapshot is outside {SNAPSHOT_ROOT.name}: {value!r}") from error
    return path


def load_snapshots(routes: list[dict[str, str]]) -> dict[str, dict]:
    try:
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read {MANIFEST_PATH}: {error}") from error

    pages = document.get("pages")
    if not isinstance(pages, list):
        raise BuildError("replica-manifest.json must contain a pages list")
    if document.get("route_count") != len(pages):
        raise BuildError("replica manifest route_count does not match its pages list")

    by_url: dict[str, dict] = {}
    revisions = set()
    for position, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise BuildError(f"replica manifest page {position} is not an object")
        url = str(page.get("url") or "")
        path = str(page.get("path") or "")
        page_id = str(page.get("id") or "")
        if route_path(url) != path:
            raise BuildError(f"manifest path does not match URL: {url!r}")
        if url in by_url:
            raise BuildError(f"duplicate snapshot URL: {url}")
        if page.get("status") != 200 or page.get("final_url") != url:
            raise BuildError(f"snapshot did not capture a canonical HTTP 200 page: {url}")
        revision = page.get("site_revision")
        if not isinstance(revision, int) or revision <= 0:
            raise BuildError(f"snapshot is missing a numeric Wix revision: {url}")
        revisions.add(revision)

        snapshot_path = _safe_snapshot_path(str(page.get("file") or ""))
        try:
            compressed = snapshot_path.read_bytes()
            expanded = gzip.decompress(compressed)
        except (OSError, gzip.BadGzipFile) as error:
            raise BuildError(f"cannot read snapshot for {url}: {error}") from error
        if len(expanded) != page.get("source_bytes") or _sha256(expanded) != page.get("source_sha256"):
            raise BuildError(f"expanded snapshot hash or size does not match manifest: {url}")
        if len(compressed) != page.get("snapshot_bytes") or _sha256(compressed) != page.get("snapshot_sha256"):
            raise BuildError(f"compressed snapshot hash or size does not match manifest: {url}")
        try:
            snapshot_html = expanded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BuildError(f"snapshot is not UTF-8: {url}") from error
        for pattern in FORBIDDEN_SNAPSHOT_PATTERNS:
            if pattern.search(snapshot_html):
                raise BuildError(f"snapshot contains active or private markup {pattern.pattern!r}: {url}")
        if "</head>" not in snapshot_html.lower() or "</body>" not in snapshot_html.lower():
            raise BuildError(f"snapshot is missing a complete HTML document: {url}")
        if page_id and page_id not in snapshot_path.name:
            raise BuildError(f"snapshot filename does not include its page id: {url}")
        by_url[url] = {**page, "html": snapshot_html}

    if len(revisions) != 1:
        raise BuildError(f"replica spans multiple Wix revisions: {sorted(revisions)}")

    expected = {route["sourceUrl"] for route in routes}
    missing = sorted(expected - by_url.keys())
    extra = sorted(by_url.keys() - expected)
    if missing or extra:
        raise BuildError(
            "replica manifest and site index differ; "
            f"missing={missing[:5]!r}, extra={extra[:5]!r}"
        )
    for route in routes:
        page = by_url[route["sourceUrl"]]
        if page["id"] != route["pageId"] or page["path"] != route["path"]:
            raise BuildError(f"replica identity does not match site index: {route['sourceUrl']}")
    return by_url


def route_destination(site_root: pathlib.Path, path: str) -> pathlib.Path:
    if path == "/":
        return site_root / "index.html"
    return site_root.joinpath(*path.strip("/").split("/"), "index.html")


def clean_source_fragment(value: object) -> str:
    """Mirror the model's visual-scaffolding filter for a readable source view."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^[*#]+\s*", "", text)
    folded = text.casefold().strip(" .")
    if (
        not text
        or "©" in text
        or folded.startswith("copyright ")
        or any(phrase in folded for phrase in BOILERPLATE_PHRASES)
        or any(pattern.fullmatch(text) for pattern in TRANSIENT_SNAPSHOT_PATTERNS)
        or any(pattern.search(text) for pattern in VISUAL_SCAFFOLD)
    ):
        return ""
    text = text.replace(
        "View the live calendar at Fortune.",
        "View the live Digital Equity calendar.",
    )
    text = re.sub(
        r"\bon Fortune(?:'s|’s) live site\b",
        "on the Digital Equity site",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bon the live Fortune page\b",
        "on the live Digital Equity page",
        text,
        flags=re.I,
    )
    return text


def source_fragments(page: dict) -> list[tuple[str, bool]]:
    """Return deduplicated source text, marking captured headings for hierarchy."""

    heading_keys = {
        clean_source_fragment(value).casefold()
        for value in page.get("headings", [])
        if clean_source_fragment(value)
    }
    values = [page.get("description", ""), *page.get("blocks", [])]
    seen: set[str] = set()
    fragments: list[tuple[str, bool]] = []
    title_key = clean_source_fragment(page.get("title", "")).casefold()
    for value in values:
        text = clean_source_fragment(value)
        key = text.casefold()
        if not text or key == title_key or key in seen:
            continue
        seen.add(key)
        is_heading = key in heading_keys
        if (
            text.isupper()
            and len(text.split()) <= 4
            and not is_heading
            and "COMING SOON" not in text
        ):
            continue
        if fragments and not fragments[-1][1]:
            prior, _ = fragments[-1]
            continues_prior = bool(
                re.search(r"\b(?:and|for|of|out of|the|to|with)$", prior, re.I)
                or (text[:1].islower() and not prior.endswith((".", "?", "!", ":")))
            )
            if continues_prior:
                fragments[-1] = (f"{prior} {text}", False)
                continue
        fragments.append((text, is_heading))
    return fragments


def static_href(asset_base: str, path: str) -> str:
    if path == "/":
        return f"{asset_base}index.html"
    return f"{asset_base}{path.strip('/')}/"


def clean_link_label(value: object) -> str:
    """Keep a short, readable label captured from a public link."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or len(text) > 240:
        return ""
    text = {
        "View the live calendar at Fortune.": "View the live Digital Equity calendar.",
        "Continue on Fortune's live site": "Continue on the Digital Equity site",
        "Continue on the live Fortune page": "Continue on the live Digital Equity page",
    }.get(text, text)
    return clean_source_fragment(text)


def approved_route_path(value: object) -> str | None:
    """Map an approved-origin link to the local route it represents."""

    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        return None
    canonical = urllib.parse.urlunsplit((
        "https",
        parsed.hostname,
        parsed.path or "/",
        "",
        "",
    ))
    try:
        return route_path(canonical)
    except BuildError:
        return None


def source_outline_from_fragments(
    fragments: list[tuple[str, bool]],
) -> list[tuple[int, str, str]]:
    """Give each captured heading a stable in-page destination."""

    outline: list[tuple[int, str, str]] = []
    section_number = 0
    faq_number = 0
    for index, (text, is_heading) in enumerate(fragments):
        if not is_heading:
            continue
        if is_faq_heading(text):
            faq_number += 1
            target = f"source-faq-{faq_number}"
        else:
            section_number += 1
            target = f"source-section-{section_number}"
        outline.append((index, text, target))
    return outline


def render_source_outline(fragments: list[tuple[str, bool]]) -> str:
    """Render captured headings as a compact reading index."""

    outline = source_outline_from_fragments(fragments)
    if not outline:
        return ""
    items = "\n".join(
        f'        <li><a href="#{html.escape(target, quote=True)}">{html.escape(text)}</a></li>'
        for _, text, target in outline
    )
    return (
        '    <nav class="source-outline" aria-labelledby="source-outline-heading">\n'
        '      <p class="source-outline__title" id="source-outline-heading">On this page</p>\n'
        '      <ol>\n'
        f"{items}\n"
        '      </ol>\n'
        '    </nav>'
    )


def snapshot_semantic_document(snapshot_html: str) -> tuple[list[dict], list[dict[str, str]]]:
    """Read public page content and textual source-footer links from a snapshot."""

    parser = _SnapshotSemanticParser()
    try:
        parser.feed(snapshot_html)
        parser.close()
    except Exception:
        return [], []
    return parser.nodes, parser.footer_links


def snapshot_semantic_nodes(snapshot_html: str) -> list[dict]:
    """Read only the public page body from a manifest-bound inert snapshot."""

    nodes, _ = snapshot_semantic_document(snapshot_html)
    return nodes


def _direct_children(node: dict, tag: str) -> list[dict]:
    return [child for child in node.get("children", []) if child.get("tag") == tag]


def _first_descendant(node: dict, tag: str) -> dict | None:
    for child in node.get("children", []):
        if child.get("tag") == tag:
            return child
        nested = _first_descendant(child, tag)
        if nested is not None:
            return nested
    return None


def _header_node_text(node: dict) -> str:
    return clean_link_label(" ".join(str(value) for value in node.get("text", [])))


def _links_in_header_list(node: dict) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for item in _direct_children(node, "li"):
        anchor = next(iter(_direct_children(item, "a")), None)
        if anchor is None:
            anchor = _first_descendant(item, "a")
        if anchor is None:
            continue
        label = _header_node_text(anchor)
        href = str(anchor.get("attrs", {}).get("href") or "")
        if label and href:
            links.append({"label": label, "href": href})
    return links


def source_navigation_groups(snapshot_html: str) -> list[dict]:
    """Return the captured global navigation as links and always-visible groups."""

    parser = _HeaderNavigationParser()
    try:
        parser.feed(snapshot_html)
        parser.close()
    except Exception:
        return []
    if not parser.navigation_roots:
        return []
    navigation = next(
        (
            node
            for node in parser.navigation_roots
            if node.get("attrs", {}).get("aria-label", "").casefold() == "site"
        ),
        parser.navigation_roots[0],
    )
    top_list = next(iter(_direct_children(navigation, "ul")), None)
    if top_list is None:
        top_list = _first_descendant(navigation, "ul")
    if top_list is None:
        return []

    groups: list[dict] = []
    for item in _direct_children(top_list, "li"):
        anchor = next(iter(_direct_children(item, "a")), None)
        if anchor is not None:
            label = _header_node_text(anchor)
            href = str(anchor.get("attrs", {}).get("href") or "")
            if label and href:
                groups.append({"label": label, "href": href, "items": []})
            continue
        details = next(iter(_direct_children(item, "details")), None)
        if details is None:
            continue
        summary = next(iter(_direct_children(details, "summary")), None)
        menu_list = next(iter(_direct_children(details, "ul")), None)
        if summary is None or menu_list is None:
            continue
        label = _header_node_text(summary)
        links = _links_in_header_list(menu_list)
        if label and links:
            groups.append({"label": label, "href": "", "items": links})
    return groups


def render_source_navigation(
    snapshot_html: str | None,
    asset_base: str,
    routes: list[dict] | None,
    current_path: str,
) -> str:
    """Render source navigation as plain lists, never as a recreated dropdown."""

    if not snapshot_html or not routes:
        return ""
    routes_by_path = {route["path"]: route for route in routes}
    entries: list[str] = []
    for group in source_navigation_groups(snapshot_html):
        label = str(group["label"])
        href = str(group["href"])
        children = group["items"]
        if href:
            markup = source_link_markup(label, href, asset_base, routes_by_path)
            current = approved_route_path(href) == current_path
            if current and markup.startswith("<a "):
                markup = markup.replace("<a ", '<a aria-current="page" ', 1)
            entries.append(f'        <li class="source-navigation__item">{markup}</li>')
            continue
        child_items: list[str] = []
        for child in children:
            child_label = str(child["label"])
            child_href = str(child["href"])
            markup = source_link_markup(child_label, child_href, asset_base, routes_by_path)
            current = approved_route_path(child_href) == current_path
            if current and markup.startswith("<a "):
                markup = markup.replace("<a ", '<a aria-current="page" ', 1)
            child_items.append(f"            <li>{markup}</li>")
        if child_items:
            children_markup = "\n".join(child_items)
            entries.append(
                '        <li class="source-navigation__group">\n'
                f'          <span class="source-navigation__label">{html.escape(label)}</span>\n'
                '          <ul>\n'
                f"{children_markup}\n"
                '          </ul>\n'
                '        </li>'
            )
    if not entries:
        return ""
    body = "\n".join(entries)
    return (
        '      <nav class="source-navigation" aria-label="Site">\n'
        '        <ul class="source-navigation__top">\n'
        f"{body}\n"
        '        </ul>\n'
        '      </nav>'
    )


def safe_source_href(
    value: object,
    asset_base: str,
    routes_by_path: dict[str, dict],
    *,
    force_external: bool = False,
) -> tuple[str, bool] | None:
    """Return a safe local or outbound destination for a captured source link."""

    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.scheme and raw.startswith("/"):
        raw = urllib.parse.urljoin("https://www.fortunedigitalequity.org/", raw)
        parsed = urllib.parse.urlsplit(raw)
    if force_external and parsed.scheme == "https":
        return raw, True
    local_path = approved_route_path(raw)
    if local_path and local_path in routes_by_path:
        return static_href(asset_base, local_path), False
    if parsed.scheme == "https":
        return raw, True
    if parsed.scheme in {"mailto", "tel"}:
        return raw, False
    return None


def source_link_markup(
    text: str,
    href: object,
    asset_base: str,
    routes_by_path: dict[str, dict],
    *,
    force_external: bool = False,
) -> str:
    """Render a text link only when the captured destination is safe."""

    destination = safe_source_href(
        href,
        asset_base,
        routes_by_path,
        force_external=force_external,
    )
    escaped_text = html.escape(text)
    if destination is None:
        return escaped_text
    target, external = destination
    attributes = ' target="_blank" rel="noreferrer"' if external else ""
    return f'<a href="{html.escape(target, quote=True)}"{attributes}>{escaped_text}</a>'


def source_text_markup(
    text: str,
    links: object,
    asset_base: str,
    routes_by_path: dict[str, dict],
) -> str:
    """Render captured inline links without dropping their surrounding prose."""

    if not isinstance(links, list):
        return html.escape(text)
    cursor = 0
    rendered: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        label = clean_link_label(link.get("label"))
        if not label:
            continue
        position = text.find(label, cursor)
        if position < 0:
            continue
        rendered.append(html.escape(text[cursor:position]))
        rendered.append(source_link_markup(
            label,
            link.get("href"),
            asset_base,
            routes_by_path,
            force_external=bool(link.get("live_action")),
        ))
        cursor = position + len(label)
    if not rendered:
        return html.escape(text)
    rendered.append(html.escape(text[cursor:]))
    return "".join(rendered)


def render_snapshot_text(
    node: dict,
    asset_base: str,
    routes_by_path: dict[str, dict],
) -> str:
    """Render a source node's text, retaining outbound live-action intent."""

    text = str(node.get("text") or "")
    href = node.get("href")
    if href:
        return source_link_markup(
            text,
            href,
            asset_base,
            routes_by_path,
            force_external=bool(node.get("live_action")),
        )
    return source_text_markup(text, node.get("links"), asset_base, routes_by_path)


def _walk_nodes(nodes: list[dict]):
    for node in nodes:
        yield node
        if node.get("type") == "details":
            yield from _walk_nodes(node.get("content", []))
        elif node.get("type") == "list":
            for item in node.get("items", []):
                yield from _walk_nodes(item.get("content", []))


def _walk_reading_sections(nodes: list[dict]):
    """Walk headings in the prose flow, not card/list items."""

    for node in nodes:
        yield node
        if node.get("type") == "details":
            yield from _walk_reading_sections(node.get("content", []))


def _heading_is_sentence(node: dict) -> bool:
    text = str(node.get("text") or "")
    return text.endswith(".") or len(text.split()) > 16


def assign_snapshot_heading_ids(
    nodes: list[dict],
    title: str,
    *,
    primary_heading: bool = True,
) -> list[tuple[str, str]]:
    """Attach local anchors while preserving the source heading order."""

    outline: list[tuple[str, str]] = []
    section_number = 0
    faq_number = 0
    title_key = clean_source_fragment(title).casefold()
    # The approved index may append a site name to the document title.  Match
    # that metadata to an actually visible heading only when the captured
    # heading is a clear title fragment; otherwise retain the first visible
    # source heading rather than publishing the metadata itself.
    title_variants = {
        clean_source_fragment(part).casefold()
        for part in re.split(r"\s+[|]\s+", title)
        if clean_source_fragment(part)
    }
    headings = [
        node
        for node in _walk_reading_sections(nodes)
        if node.get("type") == "heading" and not _heading_is_sentence(node)
    ]
    primary = next(
        (
            node
            for node in headings
            if clean_source_fragment(node.get("text")).casefold() in title_variants
        ),
        None,
    )
    if primary is None:
        primary = next(
            (
                node
                for node in headings
                if (
                    (heading_key := clean_source_fragment(node.get("text")).casefold())
                    and len(heading_key) >= 4
                    and heading_key in title_key
                )
            ),
            headings[0] if headings else None,
        )
    if primary is not None and primary_heading:
        primary["primary"] = True
    for node in headings:
        text = str(node.get("text") or "")
        if is_faq_heading(text):
            faq_number += 1
            target = f"source-faq-{faq_number}"
        else:
            section_number += 1
            target = f"source-section-{section_number}"
        node["id"] = target
        outline.append((text, target))
    return outline


def render_snapshot_outline(outline: list[tuple[str, str]]) -> str:
    if not outline:
        return ""
    items = "\n".join(
        f'        <li><a href="#{html.escape(target, quote=True)}">{html.escape(text)}</a></li>'
        for text, target in outline
    )
    return (
        '    <nav class="source-outline" aria-labelledby="source-outline-heading">\n'
        '      <p class="source-outline__title" id="source-outline-heading">On this page</p>\n'
        '      <ol>\n'
        f"{items}\n"
        '      </ol>\n'
        '    </nav>'
    )


def snapshot_heading_base(nodes: list[dict]) -> int:
    levels = [
        int(node["level"])
        for node in _walk_reading_sections(nodes)
        if node.get("type") == "heading"
        and not node.get("skip")
        and not _heading_is_sentence(node)
    ]
    return min(levels) if levels else 2


def render_snapshot_item(
    item: dict,
    asset_base: str,
    routes_by_path: dict[str, dict],
    heading_base: int,
    *,
    breadcrumb: bool,
) -> str:
    content = item.get("content", [])
    rendered: list[str] = []
    for node in content:
        if node.get("type") in {"heading", "link"}:
            text = str(node.get("text") or "")
            if text:
                rendered.append(render_snapshot_text(node, asset_base, routes_by_path))
        elif node.get("type") == "paragraph":
            text = str(node.get("text") or "")
            if text:
                rendered.append(render_snapshot_text(node, asset_base, routes_by_path))
        elif node.get("type") == "list":
            rendered.append(render_snapshot_list(node, asset_base, routes_by_path, heading_base))
    if not rendered:
        return ""
    separator = "" if breadcrumb else "<br>"
    return f"      <li>{separator.join(rendered)}</li>"


def render_snapshot_list(
    node: dict,
    asset_base: str,
    routes_by_path: dict[str, dict],
    heading_base: int,
) -> str:
    items = [
        render_snapshot_item(
            item,
            asset_base,
            routes_by_path,
            heading_base,
            breadcrumb=bool(node.get("breadcrumb")),
        )
        for item in node.get("items", [])
    ]
    items = [item for item in items if item]
    if not items:
        return ""
    tag = "ol" if node.get("ordered") else "ul"
    class_name = "source-breadcrumb" if node.get("breadcrumb") else "source-list"
    body = "\n".join(items)
    if node.get("breadcrumb"):
        return (
            f'      <nav class="{class_name}" aria-label="Breadcrumb">\n'
            f"        <{tag}>\n{body}\n        </{tag}>\n"
            "      </nav>"
        )
    return f'      <{tag} class="{class_name}">\n{body}\n      </{tag}>'


def render_snapshot_node(
    node: dict,
    asset_base: str,
    routes_by_path: dict[str, dict],
    heading_base: int,
) -> str:
    node_type = node.get("type")
    text = str(node.get("text") or "")
    if node_type == "heading":
        if node.get("skip"):
            return ""
        linked = render_snapshot_text(node, asset_base, routes_by_path)
        if _heading_is_sentence(node):
            return f"      <p>{linked}</p>"
        level = 1 if node.get("primary") else min(
            4,
            max(2, int(node.get("level", heading_base)) - heading_base + 2),
        )
        identifier = f' id="{html.escape(str(node.get("id") or ""), quote=True)}"' if node.get("id") else ""
        return f"      <h{level}{identifier}>{linked}</h{level}>"
    if node_type in {"paragraph", "link"} and text:
        linked = render_snapshot_text(node, asset_base, routes_by_path)
        class_name = ' class="source-standalone-link"' if node_type == "link" else ""
        return f"      <p{class_name}>{linked}</p>"
    if node_type == "list":
        return render_snapshot_list(node, asset_base, routes_by_path, heading_base)
    if node_type == "details":
        summary = clean_source_fragment(node.get("summary"))
        content = render_snapshot_nodes(node.get("content", []), asset_base, routes_by_path, heading_base)
        if not summary or not content:
            return content
        return (
            '      <section class="source-disclosure">\n'
            f"        <h3>{html.escape(summary)}</h3>\n"
            f"{content}\n"
            "      </section>"
        )
    return ""


def render_snapshot_nodes(
    nodes: list[dict],
    asset_base: str,
    routes_by_path: dict[str, dict],
    heading_base: int,
) -> str:
    rendered = [
        render_snapshot_node(node, asset_base, routes_by_path, heading_base)
        for node in nodes
    ]
    return "\n".join(item for item in rendered if item)


def render_snapshot_footer_links(
    footer_links: list[dict[str, str]],
    nodes: list[dict],
    asset_base: str,
    routes_by_path: dict[str, dict],
) -> str:
    """Keep non-duplicated text links from Fortune's captured source footer."""

    rendered_hrefs = {
        str(node.get("href") or "")
        for node in _walk_nodes(nodes)
        if node.get("type") in {"heading", "link", "paragraph"}
        and node.get("href")
    }
    rendered_hrefs.update(
        str(link.get("href") or "")
        for node in _walk_nodes(nodes)
        if isinstance(node.get("links"), list)
        for link in node["links"]
        if isinstance(link, dict) and link.get("href")
    )
    seen = set(rendered_hrefs)
    items: list[str] = []
    for link in footer_links:
        href = str(link.get("href") or "")
        label = clean_link_label(link.get("label"))
        if not href or not label or href in seen:
            continue
        seen.add(href)
        markup = source_link_markup(label, href, asset_base, routes_by_path)
        if markup != html.escape(label):
            items.append(f"        <li>{markup}</li>")
    if not items:
        return ""
    body = "\n".join(items)
    return (
        '      <nav class="source-footer-links" aria-label="Official contact links">\n'
        '        <ul>\n'
        f"{body}\n"
        '        </ul>\n'
        '      </nav>'
    )


def render_snapshot_source(
    snapshot_html: str,
    title: str,
    asset_base: str,
    routes: list[dict],
) -> tuple[str, str, str] | None:
    """Render a content-only, source-faithful projection of a reviewed snapshot."""

    nodes, footer_links = snapshot_semantic_document(snapshot_html)
    if not nodes:
        return None
    outline = assign_snapshot_heading_ids(nodes, title)
    routes_by_path = {route["path"]: route for route in routes}
    content = render_snapshot_nodes(
        nodes,
        asset_base,
        routes_by_path,
        snapshot_heading_base(nodes),
    )
    if not content:
        return None
    footer = render_snapshot_footer_links(footer_links, nodes, asset_base, routes_by_path)
    return render_snapshot_outline(outline), content, footer


def calendar_stable_nodes(nodes: list[dict]) -> list[dict]:
    """Keep durable location and hours text, never a stale agenda projection."""

    start = next(
        (
            index
            for index, node in enumerate(nodes)
            if node.get("type") == "heading"
            and clean_source_fragment(node.get("text")).casefold() == "class locations"
        ),
        None,
    )
    return nodes[start:] if start is not None else []


def calendar_snapshot_heading(snapshot_html: str, fallback: str) -> str:
    """Use the public calendar hero rather than a generic route label."""

    nodes, _ = snapshot_semantic_document(snapshot_html)
    for node in nodes:
        if node.get("type") != "heading":
            continue
        heading = clean_source_fragment(node.get("text"))
        if heading:
            return heading
    return fallback


def _calendar_day_short_label(value: str) -> str:
    """Keep the compact day/date label while retaining the full source label in title."""

    compact = re.match(r"^([A-Za-z]{3})\s+(\d{1,2})\b", value)
    full = re.match(r"^([A-Za-z]{3})[A-Za-z]*,\s+[A-Za-z]+\s+(\d{1,2}),", value)
    match = compact or full
    return f"{match.group(1)} {match.group(2)}" if match else value


def render_current_calendar_view(calendar_source: dict[str, Any]) -> str:
    """Render the reviewed live agenda and official PDF schedule as a real calendar view.

    The static page presents source-captured class information, but never
    recreates the Wix booking flow.  Every registration action stays on the
    official calendar where availability and personal-information collection
    are current.
    """

    source_url = html.escape(calendar_source["source_url"], quote=True)
    agenda = calendar_source["agenda"]
    week = agenda["week"]
    schedule = calendar_source["pdf_schedule"]
    document = calendar_source["document"]
    document_url = html.escape(document["url"], quote=True)
    document_label = html.escape(document["label"])
    agenda_captured_at = html.escape(agenda["captured_at"].replace("T", " ").removesuffix("Z") + " UTC")

    week_days = []
    for day in week["days"]:
        is_selected = day == week["selected"]
        selected = ' aria-current="date"' if is_selected else ""
        class_name = "calendar-week__day calendar-week__day--selected" if is_selected else "calendar-week__day"
        week_days.append(
            f'          <li class="{class_name}" title="{html.escape(day, quote=True)}"{selected}>'
            f"{html.escape(_calendar_day_short_label(day))}</li>"
        )

    agenda_rows = []
    for event in agenda["events"]:
        agenda_rows.append(
            '          <li class="calendar-event">\n'
            f'            <time class="calendar-event__date" datetime="{html.escape(event["date"], quote=True)}">{html.escape(event["date_label"])}</time>\n'
            '            <div class="calendar-event__body">\n'
            f'              <h3>{html.escape(event["title"])}</h3>\n'
            f'              <p class="calendar-event__time">{html.escape(event["time"])} <span aria-hidden="true">·</span> {html.escape(event["duration"])}</p>\n'
            f'              <p class="calendar-event__details">{html.escape(event["location"])}</p>\n'
            "            </div>\n"
            f'            <a class="calendar-event__register" href="{source_url}" target="_blank" rel="noreferrer">REGISTER<span class="visually-hidden"> for {html.escape(event["title"])}</span></a>\n'
            "          </li>"
        )

    schedule_rows = []
    for event in schedule["events"]:
        schedule_rows.append(
            '            <li class="calendar-pdf-event">\n'
            f'              <time datetime="{html.escape(event["date"], quote=True)}">{html.escape(event["date_label"])}</time>\n'
            f'              <span>{html.escape(event["title"])}</span>\n'
            "            </li>"
        )
    support_lines = "\n".join(
        f"            <li>{html.escape(line)}</li>" for line in schedule["support"]
    )
    theme = (
        f'          <p class="calendar-pdf__theme">{html.escape(schedule["theme"])}</p>\n'
        if schedule["theme"]
        else ""
    )
    week_days_markup = "\n".join(week_days)
    agenda_rows_markup = "\n".join(agenda_rows)
    schedule_rows_markup = "\n".join(schedule_rows)
    return (
        '      <section class="calendar-agenda" aria-labelledby="calendar-agenda-heading">\n'
        '        <header class="calendar-agenda__header">\n'
        '          <p class="calendar-eyebrow">Class Signup</p>\n'
        '          <h2 id="calendar-agenda-heading">Upcoming classes</h2>\n'
        '          <p>Choose a class below to check current availability and register on the Digital Equity site.</p>\n'
        '        </header>\n'
        '        <div class="calendar-week" aria-label="Captured visible week">\n'
        f'          <p class="calendar-week__label">{html.escape(week["label"])}</p>\n'
        '          <ol class="calendar-week__days">\n'
        f"{week_days_markup}\n"
        '          </ol>\n'
        '        </div>\n'
        '        <ol class="calendar-events">\n'
        f"{agenda_rows_markup}\n"
        '        </ol>\n'
        f'        <p class="calendar-agenda__continue"><a href="{source_url}" target="_blank" rel="noreferrer">View all current classes on the Digital Equity site</a></p>\n'
        f'        <p class="calendar-agenda__note">Schedule captured {agenda_captured_at}; availability changes on the Digital Equity site.</p>\n'
        '      </section>\n'
        '      <section class="calendar-pdf" aria-labelledby="calendar-pdf-heading">\n'
        '        <header class="calendar-pdf__header">\n'
        '          <p class="calendar-eyebrow">Regular Class Schedule</p>\n'
        f'          <h2 id="calendar-pdf-heading">{html.escape(schedule["month"])}</h2>\n'
        f"{theme}"
        f'          <p class="calendar-pdf__location">{html.escape(schedule["location"]["name"])}<br>{html.escape(schedule["location"]["address"])} </p>\n'
        f'          <p class="calendar-pdf__hours">{html.escape(schedule["default_hours"])}</p>\n'
        f'          <p><a class="calendar-pdf__download" href="{document_url}" target="_blank" rel="noreferrer">{document_label} <span aria-hidden="true">(PDF)</span></a></p>\n'
        '        </header>\n'
        '        <ol class="calendar-pdf-events">\n'
        f"{schedule_rows_markup}\n"
        '        </ol>\n'
        '        <section class="calendar-support" aria-labelledby="calendar-support-heading">\n'
        '          <h3 id="calendar-support-heading">Tech Time &amp; support</h3>\n'
        '          <ul>\n'
        f"{support_lines}\n"
        '          </ul>\n'
        f'          <p>{html.escape(schedule["registration_note"])}</p>\n'
        '        </section>\n'
        '      </section>'
    )


def render_calendar_snapshot_source(
    snapshot_html: str,
    title: str,
    asset_base: str,
    routes: list[dict],
    calendar_source: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Build a coherent static calendar view around current official actions.

    Wix's agenda controls are live and change frequently. The historical
    capture remains provenance for stable location and class-hours text, while
    actual availability, registration, and the downloadable schedule remain
    outbound actions on the official public site.
    """

    nodes, footer_links = snapshot_semantic_document(snapshot_html)
    nodes = calendar_stable_nodes(nodes)
    if not nodes:
        return None
    outline = assign_snapshot_heading_ids(nodes, title, primary_heading=False)
    routes_by_path = {route["path"]: route for route in routes}
    stable_content = render_snapshot_nodes(
        nodes,
        asset_base,
        routes_by_path,
        snapshot_heading_base(nodes),
    )
    if not stable_content:
        return None
    footer = render_snapshot_footer_links(footer_links, nodes, asset_base, routes_by_path)
    content = (
        f'{render_current_calendar_view(calendar_source)}\n'
        f'      <section class="calendar-location-details">\n{stable_content}\n      </section>'
    )
    # The calendar has its own source-faithful hierarchy above; a generic
    # outline is more visual noise than help here.
    return "", content, footer


def is_faq_heading(text: str) -> bool:
    """Recognize the captured FAQ heading without relying on route-specific copy."""

    return text.casefold().strip().rstrip(":") == FAQ_HEADING


def is_faq_question(text: str) -> bool:
    """FAQ questions in the reviewed source are paired with their following answer."""

    return text.rstrip().endswith("?")


def render_source_fragments(page: dict) -> str:
    """Render source text, grouping consecutive FAQ question-and-answer pairs semantically."""

    fragments = source_fragments(page)
    heading_ids = {
        index: target
        for index, _, target in source_outline_from_fragments(fragments)
    }
    content: list[str] = []
    index = 0

    while index < len(fragments):
        text, is_heading = fragments[index]
        if is_heading and is_faq_heading(text):
            pairs: list[tuple[str, str]] = []
            pair_index = index + 1
            while pair_index + 1 < len(fragments):
                question, question_is_heading = fragments[pair_index]
                answer, answer_is_heading = fragments[pair_index + 1]
                if question_is_heading or answer_is_heading or not is_faq_question(question):
                    break
                pairs.append((question, answer))
                pair_index += 2

            if pairs:
                heading_id = heading_ids[index]
                content.append(
                    f'      <section class="source-faq" aria-labelledby="{heading_id}">'\
                    f'\n        <h2 id="{heading_id}">{html.escape(text)}</h2>'\
                    '\n        <dl class="source-faq__list">'
                )
                for question, answer in pairs:
                    content.append(
                        '          <div class="source-faq__item">'\
                        f'\n            <dt>{html.escape(question)}</dt>'\
                        f'\n            <dd>{html.escape(answer)}</dd>'\
                        '\n          </div>'
                    )
                content.append("        </dl>\n      </section>")
                index = pair_index
                continue

        if is_heading:
            heading_id = heading_ids[index]
            content.append(f'      <h2 id="{heading_id}">{html.escape(text)}</h2>')
        else:
            content.append(f"      <p>{html.escape(text)}</p>")
        index += 1

    return "\n".join(content)


def render_text_page(
    route: dict,
    asset_base: str,
    routes: list[dict] | None = None,
    snapshot_html: str | None = None,
    navigation_snapshot_html: str | None = None,
    calendar_source: dict[str, Any] | None = None,
) -> str:
    """Render a small, text-only projection of the approved public page."""

    page = route.get("page") or {}
    title = clean_source_fragment(page.get("title")) or route["path"].strip("/") or "Home"
    escaped_title = html.escape(title)
    escaped_source = html.escape(route["sourceUrl"], quote=True)
    escaped_page_id = html.escape(route["pageId"], quote=True)
    escaped_csp = html.escape(CSP, quote=True)
    escaped_css = html.escape(
        f"{asset_base}replica-shell.css?v={REPLICA_SHELL_CSS_VERSION}", quote=True
    )
    escaped_js = html.escape(
        f"{asset_base}replica-shell.js?v={REPLICA_SHELL_JS_VERSION}", quote=True
    )
    navigation = render_source_navigation(
        navigation_snapshot_html,
        asset_base,
        routes,
        route["path"],
    )

    if (
        route["path"] == CALENDAR_ROUTE_PATH
        and snapshot_html
        and routes
        and calendar_source
    ):
        snapshot_projection = render_calendar_snapshot_source(
            snapshot_html,
            title,
            asset_base,
            routes,
            calendar_source,
        )
    else:
        snapshot_projection = (
            render_snapshot_source(snapshot_html, title, asset_base, routes)
            if snapshot_html and routes
            else None
        )
    if snapshot_projection is not None:
        source_outline, source_content, source_footer_links = snapshot_projection
        if route["path"] == CALENDAR_ROUTE_PATH and snapshot_html:
            page_heading = (
                f"    <h1>{html.escape(calendar_snapshot_heading(snapshot_html, 'Calendar'))}</h1>"
            )
        else:
            page_heading = ""
    else:
        source_content = render_source_fragments(page) or "      <p>No source text is available.</p>"
        source_outline = render_source_outline(source_fragments(page))
        source_footer_links = ""
        page_heading = f"    <h1>{escaped_title}</h1>"

    lastmod = clean_source_fragment(page.get("lastmod"))
    updated = f" · site update {html.escape(lastmod)}" if lastmod else ""
    return f"""<!doctype html>
<html lang="en" {REPLICA_MARKER} data-fortune-text-view="true">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title} · Fortune source text</title>
  <meta name="robots" content="noindex,nofollow,noarchive">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="{escaped_csp}">
  <meta name="fortune-replica-source" content="{escaped_source}">
  <link rel="stylesheet" href="{escaped_css}">
</head>
<body>
  <a class="skip-link" href="#source-text">Skip to source text</a>
  <header class="site-header">
    <div class="site-header__inner">
      <a class="site-name" href="{html.escape(static_href(asset_base, '/'), quote=True)}">Digital Equity</a>
{navigation}
    </div>
  </header>
  <main id="source-text">
{page_heading}
{source_outline}
    <article class="source-document">
{source_content}
    </article>
    <footer class="source-footer">
{source_footer_links}
      <p class="source-kind">Public source snapshot</p>
      <p>Source: <a href="{escaped_source}" target="_blank" rel="noreferrer">Digital Equity public site</a>{updated}</p>
    </footer>
  </main>
  <script src="{escaped_js}" data-source-url="{escaped_source}" data-page-id="{escaped_page_id}"></script>
</body>
</html>
"""


def expected_files(routes: list[dict[str, str]]) -> set[pathlib.PurePosixPath]:
    expected = {pathlib.PurePosixPath(asset) for asset in SHARED_ASSETS}
    expected.add(pathlib.PurePosixPath(SIDECAR_OUTPUT))
    for route in routes:
        if route["path"] == "/":
            expected.add(pathlib.PurePosixPath("index.html"))
        else:
            expected.add(pathlib.PurePosixPath(route["path"].strip("/")) / "index.html")
    return expected


def validate_output(site_root: pathlib.Path, routes: list[dict[str, str]]) -> dict[str, int]:
    actual: set[pathlib.PurePosixPath] = set()
    for candidate in site_root.rglob("*"):
        if candidate.is_symlink():
            raise BuildError(f"Pages artifact contains a symbolic link: {candidate}")
        if candidate.is_file():
            actual.add(pathlib.PurePosixPath(candidate.relative_to(site_root).as_posix()))

    expected = expected_files(routes)
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unexpected:
        raise BuildError(
            "Pages artifact contains files outside the allowlist: "
            + ", ".join(str(path) for path in unexpected)
        )
    if missing:
        raise BuildError(
            "Pages artifact is missing expected files: "
            + ", ".join(str(path) for path in missing)
        )

    route_shells = [path for path in actual if path.name == "index.html"]
    if len(route_shells) != len(routes):
        raise BuildError(f"expected {len(routes)} replica routes but found {len(route_shells)}")
    for shell_path in route_shells:
        shell = (site_root / pathlib.Path(shell_path.as_posix())).read_text(encoding="utf-8")
        if REPLICA_MARKER not in shell:
            raise BuildError(f"replica marker is missing from {shell_path}")
        if 'data-fortune-text-view="true"' not in shell:
            raise BuildError(f"text-source marker is missing from {shell_path}")
        if shell.lower().count("<script") != 1 or "replica-shell.js" not in shell:
            raise BuildError(f"unexpected executable scripts in {shell_path}")
        if re.search(r"<\s*(?:img|picture|svg)\b|<\s*style\b", shell, re.IGNORECASE):
            raise BuildError(f"visual or inline-style markup found in {shell_path}")
        for forbidden in ("wix-viewer-model", "X-XSRF-TOKEN", "OLLAMA_API_KEY"):
            if forbidden.lower() in shell.lower():
                raise BuildError(f"private or runtime-only value found in {shell_path}: {forbidden}")

    return {
        "indexed_routes": len(routes),
        "replica_routes": len(route_shells),
        "shared_assets": len(SHARED_ASSETS),
        "allowlisted_root_files": len(SHARED_ASSETS) + 1 + (1 if routes else 0),
        "total_files": len(actual),
    }


def build(routes: list[dict[str, str]], snapshots: dict[str, dict]) -> dict[str, int]:
    required = (
        SIDECAR_TEMPLATE_PATH,
        *tuple(ROOT / asset for asset in SHARED_ASSETS),
    )
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise BuildError("missing public source files: " + ", ".join(sorted(missing)))

    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".pages-build-", dir=ROOT))
    try:
        root_route = next(route for route in routes if route["path"] == "/")
        navigation_snapshot_html = snapshots[root_route["sourceUrl"]]["html"]
        calendar_source = (
            load_calendar_source()
            if any(route["path"] == CALENDAR_ROUTE_PATH for route in routes)
            else None
        )
        for asset in SHARED_ASSETS:
            shutil.copyfile(ROOT / asset, temporary / asset)
        shutil.copyfile(SIDECAR_TEMPLATE_PATH, temporary / SIDECAR_OUTPUT)
        for route in routes:
            destination = route_destination(temporary, route["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            depth = 0 if route["path"] == "/" else len(route["path"].strip("/").split("/"))
            prefix = "../" * depth
            if route["sourceUrl"] not in snapshots:
                raise BuildError(f"missing reviewed snapshot for {route['sourceUrl']}")
            rendered = render_text_page(
                route,
                prefix,
                routes,
                snapshots[route["sourceUrl"]]["html"],
                navigation_snapshot_html,
                calendar_source,
            )
            destination.write_text(rendered, encoding="utf-8")
        counts = validate_output(temporary, routes)
        if OUTPUT_PATH.exists():
            if OUTPUT_PATH.is_symlink() or not OUTPUT_PATH.is_dir():
                raise BuildError(f"refusing to replace unsafe output path: {OUTPUT_PATH}")
            shutil.rmtree(OUTPUT_PATH)
        temporary.replace(OUTPUT_PATH)
        return counts
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-index",
        action="store_true",
        help="validate site-index.json without reading snapshots or writing output",
    )
    parser.add_argument(
        "--check-snapshots",
        action="store_true",
        help="validate the site index, manifest, and compressed snapshots without writing output",
    )
    args = parser.parse_args()
    try:
        routes = load_routes()
        if args.check_index:
            print(f"validated {INDEX_PATH.name}: {len(routes)} unique HTTPS routes")
            return 0
        snapshots = load_snapshots(routes)
        if args.check_snapshots:
            print(f"validated {MANIFEST_PATH.name}: {len(snapshots)} reviewed snapshots")
            return 0
        counts = build(routes, snapshots)
    except BuildError as error:
        parser.error(str(error))

    print(f"built {OUTPUT_PATH}")
    print(f"indexed routes: {counts['indexed_routes']}")
    print(f"replica routes: {counts['replica_routes']}")
    print(f"shared assets: {counts['shared_assets']}")
    print(f"allowlisted root files: {counts['allowlisted_root_files']}")
    print(f"total files: {counts['total_files']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
