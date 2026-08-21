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


ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "site-index.json"
MANIFEST_PATH = ROOT / "replica-manifest.json"
SNAPSHOT_ROOT = ROOT / "replica-snapshots"
SIDECAR_TEMPLATE_PATH = ROOT / "index.html"
OUTPUT_PATH = ROOT / "_site"
ALLOWED_HOSTS = {"fortunedigitalequity.org", "www.fortunedigitalequity.org"}
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
REPLICA_SHELL_CSS_VERSION = "20260820-text-source-1"
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
NAVIGATION_LABELS = {
    "welcome!",
    "to the fortune society",
    "choose a service",
    "explore learning paths",
    "more about us",
    "find a workshop",
    "view calendar",
    "get a device",
    "internship",
    "get support",
    "explore tools",
    "contact us",
    "attend an open lab",
    "more",
    "explore more",
    "other resources",
    "volunteer",
    "special events",
    "donate",
    "tech fair",
    "program updates",
    "media kit",
    "hear from past participants",
    "still not sure where to start?",
}
PRIMARY_NAVIGATION = (
    ("Home", "/"),
    ("Workshops", "/workshops"),
    ("Calendar", "/calendar"),
    ("Devices", "/devices"),
    ("Support", "/support"),
    ("Contact", "/contact"),
)


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
        or any(pattern.search(text) for pattern in VISUAL_SCAFFOLD)
    ):
        return ""
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
        if key in NAVIGATION_LABELS:
            continue
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


def render_text_page(route: dict, asset_base: str) -> str:
    """Render the approved retrieval record as a small, text-only HTML page."""

    page = route.get("page") or {}
    title = clean_source_fragment(page.get("title")) or route["path"].strip("/") or "Home"
    authority = str(page.get("authority") or "excluded")
    is_answer_source = authority == "answer" and int(page.get("status") or 0) == 200
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
    navigation = "".join(
        f'<a href="{html.escape(static_href(asset_base, path), quote=True)}"'
        + (' aria-current="page"' if route["path"] == path else "")
        + f">{html.escape(label)}</a>"
        for label, path in PRIMARY_NAVIGATION
    )

    if is_answer_source:
        content = []
        for text, is_heading in source_fragments(page):
            tag = "h2" if is_heading else "p"
            content.append(f"      <{tag}>{html.escape(text)}</{tag}>")
        source_content = "\n".join(content) or "      <p>No source text is available.</p>"
        source_label = "Website Guide source text"
    else:
        source_content = (
            "      <p>This route is retained for navigation or reference. "
            "It is not used by Website Guide as a current answer source.</p>"
        )
        source_label = "Not a current answer source"

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
      <a class="site-name" href="{html.escape(static_href(asset_base, '/'), quote=True)}">Fortune Society Digital Equity</a>
      <nav aria-label="Source pages">{navigation}</nav>
    </div>
  </header>
  <main id="source-text">
    <p class="source-kind">{html.escape(source_label)}</p>
    <h1>{escaped_title}</h1>
    <article class="source-document">
{source_content}
    </article>
    <footer class="source-footer">
      <p>Source: <a href="{escaped_source}" target="_blank" rel="noreferrer">Fortune Society Digital Equity</a>{updated}</p>
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
            rendered = render_text_page(route, prefix)
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
