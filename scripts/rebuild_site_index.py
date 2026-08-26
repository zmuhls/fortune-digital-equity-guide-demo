#!/usr/bin/env python3
"""Build an auditable retrieval index from the public Digital Equity sitemap.

The crawler keeps every sitemap URL in the index. Current operational pages
and active booking services may support participant answers. Old posts,
category archives, staging pages, and archived services remain visible in the
inventory but cannot become answer authority.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import gzip
from html import unescape
from html.parser import HTMLParser
import argparse
import hashlib
import json
import pathlib
import re
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


HERE = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = HERE / "site-index.json"
SNAPSHOT_MANIFEST = HERE / "replica-manifest.json"
SNAPSHOT_ROOT = HERE / "replica-snapshots"
ROOT_SITEMAP = "https://www.fortunedigitalequity.org/sitemap.xml"
BLOG_FEED = "https://www.fortunedigitalequity.org/blog-feed.xml"
ALLOWED_HOST = "www.fortunedigitalequity.org"
USER_AGENT = "FortuneDigitalEquityGuideIndex/1.0 (+public meeting prototype)"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
MIN_REQUEST_INTERVAL = 0.4
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "button", "dd", "div",
    "dl", "dt", "figcaption", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "label", "li", "main", "nav", "p",
    "section", "table", "td", "th", "tr",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
REPLICA_PRESENTATION_ATTRIBUTES = {
    "data-replica-embed-placeholder",
    "data-replica-static-preview-note",
    "data-replica-live-action",
}

EXCLUDED_PAGE_PATHS = {
    "/acp": "outdated program page",
    "/file-share": "member file area",
    "/groups": "member community area",
    "/home-new": "duplicate or staging home page",
    "/members": "member directory",
    "/pdf2-upload": "administrative upload page",
    "/test": "test page",
    "/test-calendy": "test page",
}
ARCHIVE_PAGE_PATHS = {
    "/techfair/techfair22", "/techfair/techfair23", "/techfair/techfair24",
    "/techfair/techfair25",
}
ADDITIONAL_PUBLIC_ROUTES = {
    "/news/page/2": "blog-categories",
    "/news/page/3": "blog-categories",
}


def fetch(url, timeout=40):
    global _LAST_REQUEST
    parsed = urllib.parse.urlsplit(url)
    request_url = urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/%:@"),
        parsed.query,
        "",
    ))
    request = urllib.request.Request(request_url, headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        with _RATE_LOCK:
            delay = MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST)
            if delay > 0:
                time.sleep(delay)
            _LAST_REQUEST = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), getattr(response, "status", 200)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 4:
                raise
            retry_after = error.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
            time.sleep(min(wait, 24))
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError):
            if attempt == 4:
                raise
            time.sleep(min(2 ** (attempt + 1), 16))
    raise RuntimeError("fetch retries exhausted")


def canonical_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname and parsed.hostname not in {ALLOWED_HOST, "fortunedigitalequity.org"}:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(("https", ALLOWED_HOST, path, "", ""))


def sitemap_entries():
    root_body, _ = fetch(ROOT_SITEMAP)
    root = ET.fromstring(root_body)
    rows = []
    for sitemap in root.findall("sm:sitemap", NS):
        location = sitemap.findtext("sm:loc", default="", namespaces=NS).strip()
        if not location:
            continue
        kind = pathlib.PurePosixPath(urllib.parse.urlsplit(location).path).name.replace("-sitemap.xml", "")
        body, _ = fetch(location)
        child = ET.fromstring(body)
        for item in child.findall("sm:url", NS):
            url = canonical_url(item.findtext("sm:loc", default="", namespaces=NS).strip())
            if urllib.parse.urlsplit(url).hostname != ALLOWED_HOST:
                continue
            rows.append({
                "url": url,
                "sitemap_kind": kind,
                "lastmod": item.findtext("sm:lastmod", default="", namespaces=NS).strip(),
            })

    feed_body, _ = fetch(BLOG_FEED)
    feed = ET.fromstring(feed_body)
    for item in feed.findall("./channel/item"):
        url = canonical_url(item.findtext("link", default="").strip())
        if urllib.parse.urlsplit(url).hostname != ALLOWED_HOST:
            continue
        rows.append({
            "url": url,
            "sitemap_kind": "blog-posts",
            "lastmod": item.findtext("pubDate", default="").strip(),
        })

    for path, kind in ADDITIONAL_PUBLIC_ROUTES.items():
        rows.append({
            "url": canonical_url(path),
            "sitemap_kind": kind,
            "lastmod": "",
        })

    deduplicated = {}
    for row in rows:
        existing = deduplicated.get(row["url"])
        if existing is None or existing["sitemap_kind"] == "blog-categories":
            deduplicated[row["url"]] = row
    return rows, list(deduplicated.values())


def normalize_text(value):
    value = unescape(value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


class PageExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.description = ""
        self.blocks = []
        self.headings = []
        self.links = set()
        self._in_title = False
        self._main_depth = 0
        self._skip_depth = 0
        self._skip_root_depths = set()
        self._heading_depth = 0
        self._buffer = []

    @staticmethod
    def _attrs(attrs):
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        values = self._attrs(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "meta" and values.get("name", "").lower() == "description":
            self.description = normalize_text(values.get("content"))

        if tag == "main" and (
            values.get("data-main-content", "").lower() == "true"
            or values.get("id") == "PAGES_CONTAINER"
        ):
            self._main_depth = 1
            return
        if not self._main_depth:
            return
        self._main_depth += 1
        if tag in SKIP_TAGS or any(name in values for name in REPLICA_PRESENTATION_ATTRIBUTES):
            self._skip_depth += 1
            self._skip_root_depths.add(self._main_depth)
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth += 1
        if tag == "a":
            href = values.get("href", "")
            if href:
                self.links.add(href)
        if tag == "img":
            alt = normalize_text(values.get("alt"))
            if alt:
                self._buffer.append(alt)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self._main_depth:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if not self._main_depth:
            return
        if self._main_depth in self._skip_root_depths:
            self._skip_depth -= 1
            self._skip_root_depths.remove(self._main_depth)
        elif not self._skip_depth:
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                text = self._flush()
                if text:
                    self.headings.append(text)
                self._heading_depth = max(0, self._heading_depth - 1)
            elif tag in BLOCK_TAGS:
                self._flush()
        self._main_depth -= 1

    def handle_data(self, data):
        text = normalize_text(data)
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._main_depth and not self._skip_depth:
            self._buffer.append(text)

    def _flush(self):
        text = normalize_text(" ".join(self._buffer))
        self._buffer = []
        if text:
            self.blocks.append(text)
        return text


def authority_for(row):
    path = urllib.parse.urlsplit(row["url"]).path.rstrip("/") or "/"
    kind = row["sitemap_kind"]
    if kind == "blog-posts":
        return "archive", "older news post; navigation only"
    if kind == "blog-categories" or path == "/news":
        return "navigation", "news index or category; navigation only"
    if kind == "profiles":
        return "excluded", "public author profile; excluded from participant retrieval"
    if path in EXCLUDED_PAGE_PATHS:
        return "excluded", EXCLUDED_PAGE_PATHS[path]
    if path in ARCHIVE_PAGE_PATHS:
        return "archive", "past Tech Fair page"
    if kind == "booking-services":
        slug = path.rsplit("/", 1)[-1]
        if "archive" in slug:
            return "archive", "service title is marked archive"
        if slug == "sample-class":
            return "excluded", "sample service page"
        if slug == "identity-theft-how-to-minimize-risk-1":
            return "excluded", "duplicate service page"
    return "answer", "current public operational page"


def page_id(row):
    path = urllib.parse.urlsplit(row["url"]).path.strip("/") or "home"
    prefix = {
        "pages": "page",
        "booking-services": "service",
        "blog-posts": "post",
        "blog-categories": "category",
        "profiles": "profile",
    }.get(row["sitemap_kind"], "page")
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    digest = hashlib.sha256(row["url"].encode()).hexdigest()[:8]
    return f"{prefix}-{slug[:72]}-{digest}"


def clean_blocks(blocks):
    output = []
    seen = set()
    generic = {
        "top of page", "bottom of page", "use tab to navigate through the menu items.",
    }
    for block in blocks:
        block = normalize_text(block)
        key = block.casefold()
        if len(block) < 2 or key in generic or key in seen:
            continue
        seen.add(key)
        output.append(block[:4000])
        if sum(len(item) for item in output) >= 60000:
            break
    return output


CALENDAR_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        start=1,
    )
}


def _plain_snapshot_text(fragment):
    value = re.sub(r"<[^>]+>", " ", str(fragment or ""))
    return normalize_text(unescape(value))


def calendar_events_from_snapshot(markup, captured_at=""):
    """Keep each rendered Wix agenda row as one dated source record."""

    try:
        captured = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        captured = date.today()
    day_sections = re.findall(
        r'<li\b[^>]*data-hook=["\']daily-agenda-day["\'][^>]*>(.*?)'
        r'(?=<li\b[^>]*data-hook=["\']daily-agenda-day["\']|'
        r'<p\b[^>]*data-replica-live-calendar-note|\Z)',
        str(markup or ""),
        flags=re.I | re.S,
    )
    events = []
    previous_date = None
    for section in day_sections:
        day_match = re.search(
            r'data-hook=["\']daily-agenda-day-date["\'][^>]*>(.*?)</span>',
            section,
            flags=re.I | re.S,
        )
        if not day_match:
            continue
        day_label = _plain_snapshot_text(day_match.group(1))
        date_match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", day_label)
        if not date_match or date_match.group(1).lower() not in CALENDAR_MONTHS:
            continue
        month = CALENDAR_MONTHS[date_match.group(1).lower()]
        day = int(date_match.group(2))
        year = previous_date.year if previous_date else captured.year
        try:
            event_date = date(year, month, day)
        except ValueError:
            continue
        if previous_date:
            while event_date < previous_date:
                year += 1
                event_date = date(year, month, day)
        else:
            while event_date < captured - timedelta(days=2):
                year += 1
                event_date = date(year, month, day)
        previous_date = event_date

        slot_sections = re.findall(
            r'<li\b[^>]*data-hook=["\']daily-agenda-slot["\'][^>]*>(.*?)</li>',
            section,
            flags=re.I | re.S,
        )
        for slot in slot_sections:
            parts = [
                _plain_snapshot_text(value)
                for value in re.findall(
                    r'<span\b[^>]*aria-hidden=["\']false["\'][^>]*>(.*?)</span>',
                    slot,
                    flags=re.I | re.S,
                )
            ]
            parts = [value for value in parts if value and value != day_label]
            if not parts:
                continue
            weekday = event_date.strftime("%A")
            label = f"{weekday}, {event_date.strftime('%B')} {event_date.day}, {event_date.year}: "
            label += " · ".join(parts)
            events.append({
                "date": event_date.isoformat(),
                "label": label,
            })
    return events


def internal_links(base_url, links):
    result = set()
    for raw in links:
        try:
            url = canonical_url(urllib.parse.urljoin(base_url, raw))
        except ValueError:
            continue
        if urllib.parse.urlsplit(url).hostname == ALLOWED_HOST and url != base_url:
            result.add(url)
    return sorted(result)


def reviewed_authority(row, previous=None):
    """Keep recorded source decisions; hold newly discovered URLs for review."""
    if previous:
        return (
            previous.get("authority", "excluded"),
            previous.get(
                "authority_reason",
                "existing source classification retained during content refresh",
            ),
        )
    proposed = authority_for(row)
    if proposed[0] != "answer":
        return proposed
    return "excluded", "new public URL pending Fortune staff source review"


def crawl_page(row, previous=None):
    record = dict(row)
    record["id"] = page_id(row)
    record["authority"], record["authority_reason"] = reviewed_authority(
        row, previous
    )
    path = urllib.parse.urlsplit(row["url"]).path
    record["volatile"] = any(token in path for token in (
        "/calendar", "/events", "/reserve", "/devices", "/opportunities", "/service-page/",
    ))
    try:
        body, status = fetch(row["url"])
        parser = PageExtractor()
        parser.feed(body.decode("utf-8", errors="replace"))
        blocks = clean_blocks(parser.blocks)
        content_characters = sum(len(block) for block in blocks)
        if record["authority"] == "answer" and content_characters < 80:
            record["authority"] = "excluded"
            record["authority_reason"] = "page returned too little public text for safe retrieval"
        record.update({
            "status": status,
            "title": normalize_text(" ".join(parser.title_parts)) or path.rsplit("/", 1)[-1].replace("-", " ").title(),
            "description": parser.description,
            "headings": clean_blocks(parser.headings)[:30],
            "blocks": blocks,
            "internal_links": internal_links(row["url"], parser.links),
            "content_characters": content_characters,
            "content_hash": hashlib.sha256("\n".join(blocks).encode()).hexdigest(),
            "source_owner": (previous or {}).get(
                "source_owner",
                "Fortune Society Digital Equity staff (confirmation pending)",
            ),
            "approval_state": (previous or {}).get(
                "approval_state", "pending Fortune staff review"
            ),
            "reviewed_on": (previous or {}).get("reviewed_on"),
        })
    except Exception as error:  # keep a failed URL visible in the audit inventory
        record.update({
            "status": 0,
            "title": path.rsplit("/", 1)[-1].replace("-", " ").title() or "Digital Equity home",
            "description": "",
            "headings": [],
            "blocks": [],
            "internal_links": [],
            "content_characters": 0,
            "content_hash": "",
            "source_owner": "Fortune Society Digital Equity staff (confirmation pending)",
            "approval_state": "crawl incomplete",
            "reviewed_on": None,
            "crawl_error": type(error).__name__,
        })
    return record


def write_index(pages, sitemap_entry_count, generated_from):
    pages.sort(key=lambda page: page["url"])
    counts = {}
    for page in pages:
        counts[page["authority"]] = counts.get(page["authority"], 0) + 1
    document = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "generated_from": generated_from,
        "root_sitemap": ROOT_SITEMAP,
        "sitemap_entries": sitemap_entry_count,
        "unique_urls": len(pages),
        "authority_counts": counts,
        "policy": {
            "answer": "May support a participant answer when retrieval finds it relevant.",
            "navigation": "May appear as a related destination but not as factual answer authority.",
            "archive": "Retained for provenance and labeled historical navigation only.",
            "excluded": "Retained in the audit inventory and unavailable to participant retrieval.",
        },
        "pages": pages,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    successful = sum(1 for page in pages if page["status"] == 200)
    print(f"wrote {OUTPUT} ({successful}/{len(pages)} pages fetched; authorities={counts})")


def _snapshot_document(manifest_path):
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read rendered snapshot manifest {manifest_path}: {error}") from error
    pages = document.get("pages")
    if not isinstance(pages, list) or not pages:
        raise RuntimeError("rendered snapshot manifest must contain a non-empty pages list")
    if document.get("route_count") != len(pages):
        raise RuntimeError("rendered snapshot manifest route_count does not match pages")
    return document


def _rendered_snapshot_content(page, manifest_page, snapshot_root, captured_at=""):
    """Return source fields extracted from one reviewed rendered snapshot.

    The raw Wix response can omit lazy-loaded and accordion text.  This path is
    deliberately separate from ``crawl_page``: it only accepts a manifest-bound
    snapshot captured by Firefox after the public page has rendered.
    """
    if manifest_page.get("url") != page.get("url") or manifest_page.get("id") != page.get("id"):
        raise RuntimeError(f"snapshot identity does not match index page: {page.get('url')}")
    if manifest_page.get("status") != 200 or manifest_page.get("final_url") != page.get("url"):
        raise RuntimeError(f"snapshot is not a canonical HTTP 200 page: {page.get('url')}")

    relative = pathlib.PurePosixPath(str(manifest_page.get("file") or ""))
    if relative.is_absolute() or ".." in relative.parts or relative.suffixes[-2:] != [".html", ".gz"]:
        raise RuntimeError(f"snapshot filename is unsafe: {relative}")
    snapshot_path = snapshot_root.parent / pathlib.Path(relative.as_posix())
    try:
        snapshot_path.relative_to(snapshot_root)
    except ValueError as error:
        raise RuntimeError(f"snapshot escapes snapshot root: {relative}") from error

    try:
        compressed = snapshot_path.read_bytes()
        expanded = gzip.decompress(compressed)
    except (OSError, gzip.BadGzipFile) as error:
        raise RuntimeError(f"cannot read rendered snapshot {relative}: {error}") from error
    if hashlib.sha256(compressed).hexdigest() != manifest_page.get("snapshot_sha256"):
        raise RuntimeError(f"compressed snapshot hash does not match manifest: {relative}")
    if hashlib.sha256(expanded).hexdigest() != manifest_page.get("source_sha256"):
        raise RuntimeError(f"rendered snapshot hash does not match manifest: {relative}")

    markup = expanded.decode("utf-8", errors="strict")
    parser = PageExtractor()
    parser.feed(markup)
    blocks = clean_blocks(parser.blocks)
    content_characters = sum(len(block) for block in blocks)
    if not blocks:
        raise RuntimeError(f"rendered snapshot has no public main-frame text: {page.get('url')}")
    result = {
        "title": normalize_text(" ".join(parser.title_parts)) or page.get("title", ""),
        "description": parser.description,
        "headings": clean_blocks(parser.headings)[:30],
        "blocks": blocks,
        "internal_links": internal_links(page["url"], parser.links),
        "content_characters": content_characters,
        "content_hash": hashlib.sha256("\n".join(blocks).encode()).hexdigest(),
        "rendered_snapshot": {
            "file": str(relative),
            "source_sha256": manifest_page["source_sha256"],
            "site_revision": manifest_page["site_revision"],
        },
    }
    if urllib.parse.urlsplit(page.get("url", "")).path.rstrip("/") == "/calendar":
        result["source_captured_at"] = captured_at or None
        result["calendar_events"] = calendar_events_from_snapshot(
            markup,
            captured_at or page.get("lastmod", ""),
        )
    return result


def rendered_snapshot_pages(index_document, manifest_document, snapshot_root):
    """Refresh page text from manifest-bound rendered snapshots without network I/O."""
    pages = index_document.get("pages")
    if not isinstance(pages, list) or not pages:
        raise RuntimeError("site index must contain a non-empty pages list")
    manifest_pages = manifest_document.get("pages")
    if not isinstance(manifest_pages, list):
        raise RuntimeError("rendered snapshot manifest must contain pages")
    by_url = {item.get("url"): item for item in manifest_pages if isinstance(item, dict)}
    expected_urls = {page.get("url") for page in pages if isinstance(page, dict)}
    if expected_urls != set(by_url):
        missing = sorted(expected_urls - set(by_url))
        extra = sorted(set(by_url) - expected_urls)
        raise RuntimeError(
            "site index and rendered snapshot manifest differ; "
            f"missing={missing[:3]!r}, extra={extra[:3]!r}"
        )

    refreshed = []
    revisions = set()
    for page in pages:
        if not isinstance(page, dict) or not page.get("url"):
            raise RuntimeError("site index contains an invalid page record")
        manifest_page = by_url[page["url"]]
        content = _rendered_snapshot_content(
            page,
            manifest_page,
            snapshot_root,
            manifest_document.get("captured_at", ""),
        )
        revisions.add(content["rendered_snapshot"]["site_revision"])
        record = dict(page)
        record.update(content)
        record.pop("crawl_error", None)
        refreshed.append(record)
    if len(revisions) != 1:
        raise RuntimeError(f"rendered snapshot refresh spans multiple Wix revisions: {sorted(revisions)}")
    return refreshed


def cached_inventory_pages(path):
    inventory = json.loads(path.read_text(encoding="utf-8"))
    kind_map = {
        "page": "pages",
        "booking": "booking-services",
        "blog_post": "blog-posts",
        "blog_category": "blog-categories",
    }
    pages = []
    for cached in inventory.get("records", []):
        row = {
            "url": canonical_url(cached["url"]),
            "sitemap_kind": kind_map.get(cached.get("sitemap_kind"), cached.get("sitemap_kind", "pages")),
            "lastmod": "",
        }
        recommendation = cached.get("recommendation")
        if recommendation == "authoritative":
            authority, reason = "answer", cached.get("recommendation_reason", "current public operational page")
        elif recommendation == "context_only":
            authority = "navigation" if row["sitemap_kind"] == "blog-categories" else "archive"
            reason = cached.get("recommendation_reason", "historical or index context only")
        else:
            authority, reason = "excluded", cached.get("recommendation_reason", "excluded from participant retrieval")

        visible = normalize_text(cached.get("visible_text"))
        marker = "Use tab to navigate through the menu items."
        if marker in visible:
            visible = visible.split(marker, 1)[1].strip()
        for footer_marker in ("Contact Us Volunteer Donate Media Kit", "©2024 by Fortune Society Digital Equity Program"):
            if footer_marker in visible:
                visible = visible.split(footer_marker, 1)[0].strip()
        title = normalize_text(cached.get("title"))
        if visible.startswith(title):
            visible = visible[len(title):].strip()
        blocks = clean_blocks(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", visible))
        status = cached.get("status")
        status = status if isinstance(status, int) else 206
        path_value = urllib.parse.urlsplit(row["url"]).path
        pages.append({
            **row,
            "id": page_id(row),
            "authority": authority,
            "authority_reason": reason,
            "volatile": any(token in path_value for token in (
                "/calendar", "/events", "/reserve", "/devices", "/opportunities", "/service-page/",
            )),
            "status": status,
            "title": title,
            "description": "",
            "headings": [title] if title else [],
            "blocks": blocks,
            "internal_links": sorted({canonical_url(url) for url in cached.get("internal_links", []) if canonical_url(url)}),
            "content_characters": sum(len(block) for block in blocks),
            "content_hash": hashlib.sha256("\n".join(blocks).encode()).hexdigest(),
            "source_owner": "Fortune Society Digital Equity staff (confirmation pending)",
            "approval_state": "pending Fortune staff review" if status == 200 else "crawl incomplete",
            "reviewed_on": None,
            **({"crawl_error": "partial_fetch"} if status != 200 else {}),
        })
    return inventory, pages


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-inventory", type=pathlib.Path, help="Convert an already completed public crawl without making new requests.")
    source.add_argument(
        "--from-rendered-snapshots",
        action="store_true",
        help="Refresh text from the reviewed Firefox-rendered snapshot manifest without making network requests.",
    )
    parser.add_argument(
        "--snapshot-manifest",
        type=pathlib.Path,
        default=SNAPSHOT_MANIFEST,
        help="Manifest paired with --from-rendered-snapshots (default: replica-manifest.json).",
    )
    args = parser.parse_args()
    if args.from_inventory:
        inventory, pages = cached_inventory_pages(args.from_inventory)
        write_index(
            pages,
            inventory.get("unique_url_count", len(pages)) + 1,
            inventory.get("generated_from", [ROOT_SITEMAP]),
        )
        return

    if args.from_rendered_snapshots:
        try:
            index_document = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"cannot read current site index for rendered refresh: {error}")
        try:
            manifest_document = _snapshot_document(args.snapshot_manifest)
            pages = rendered_snapshot_pages(index_document, manifest_document, SNAPSHOT_ROOT)
        except RuntimeError as error:
            parser.error(str(error))
        generated_from = list(index_document.get("generated_from", []))
        rendered_source = f"rendered Firefox snapshots ({args.snapshot_manifest.name})"
        if rendered_source not in generated_from:
            generated_from.append(rendered_source)
        write_index(
            pages,
            index_document.get("sitemap_entries", len(pages)),
            generated_from,
        )
        return

    previous_pages = {}
    if OUTPUT.is_file():
        try:
            previous_document = json.loads(OUTPUT.read_text(encoding="utf-8"))
            previous_pages = {
                page["url"]: page
                for page in previous_document.get("pages", [])
                if isinstance(page, dict) and page.get("url")
            }
        except (OSError, json.JSONDecodeError, TypeError):
            previous_pages = {}

    all_rows, unique_rows = sitemap_entries()
    pages = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(crawl_page, row, previous_pages.get(row["url"])): row
            for row in unique_rows
        }
        completed = 0
        for future in as_completed(futures):
            pages.append(future.result())
            completed += 1
            if completed % 20 == 0 or completed == len(unique_rows):
                print(f"crawled {completed}/{len(unique_rows)}", file=sys.stderr)

    write_index(pages, len(all_rows), [ROOT_SITEMAP, BLOG_FEED])


if __name__ == "__main__":
    main()
