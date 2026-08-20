#!/usr/bin/env python3
"""Build the reviewed Fortune replica and its isolated informational sidecar."""

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
REPLICA_SHELL_CSS_VERSION = "20260820-responsive-header-1"
REPLICA_SHELL_JS_VERSION = "20260820-responsive-header-1"
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
    "font-src data: https://static.parastorage.com https://static.wixstatic.com https://fonts.gstatic.com; "
    "frame-src 'self'; "
    "img-src 'self' data: blob: https://static.parastorage.com https://static.wixstatic.com "
    "https://siteassets.parastorage.com https://www-fortunedigitalequity-org.filesusr.com https://i.ytimg.com; "
    "media-src https://static.parastorage.com https://static.wixstatic.com; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://static.parastorage.com https://static.wixstatic.com https://fonts.googleapis.com; "
    "form-action 'none'"
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


def load_routes() -> list[dict[str, str]]:
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
        routes.append({"path": path, "sourceUrl": url, "pageId": page_id})

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


def render_snapshot(snapshot_html: str, route: dict[str, str], asset_base: str) -> str:
    snapshot_html = re.sub(
        r"(?<!:)//(?=(?:static\.parastorage\.com|static\.wixstatic\.com|siteassets\.parastorage\.com|www-fortunedigitalequity-org\.filesusr\.com|i\.ytimg\.com)/)",
        "https://",
        snapshot_html,
        flags=re.IGNORECASE,
    )
    lower = snapshot_html.lower()
    head_match = re.search(r"<head\b[^>]*>", snapshot_html, re.IGNORECASE)
    body_position = lower.rfind("</body>")
    if not head_match or body_position < 0 or body_position < head_match.end():
        raise BuildError(f"snapshot has invalid document boundaries: {route['sourceUrl']}")

    escaped_csp = html.escape(CSP, quote=True)
    escaped_source = html.escape(route["sourceUrl"], quote=True)
    escaped_page_id = html.escape(route["pageId"], quote=True)
    escaped_css = html.escape(
        f"{asset_base}replica-shell.css?v={REPLICA_SHELL_CSS_VERSION}", quote=True
    )
    escaped_js = html.escape(
        f"{asset_base}replica-shell.js?v={REPLICA_SHELL_JS_VERSION}",
        quote=True,
    )
    head_injection = (
        "\n  <!-- Fortune replica publication controls -->\n"
        "  <meta name=\"robots\" content=\"noindex,nofollow,noarchive\">\n"
        "  <meta name=\"referrer\" content=\"no-referrer\">\n"
        f"  <meta http-equiv=\"Content-Security-Policy\" content=\"{escaped_csp}\">\n"
        f"  <meta name=\"fortune-replica-source\" content=\"{escaped_source}\">\n"
        f"  <link rel=\"stylesheet\" href=\"{escaped_css}\">\n"
    )
    head_position = head_match.end()
    shell = snapshot_html[:head_position] + head_injection + snapshot_html[head_position:]
    body_position = shell.lower().rfind("</body>")
    body_injection = (
        "\n  <!-- Trusted sidecar and same-site navigation bridge -->\n"
        f"  <script src=\"{escaped_js}\" data-source-url=\"{escaped_source}\" "
        f"data-page-id=\"{escaped_page_id}\"></script>\n"
    )
    shell = shell[:body_position] + body_injection + shell[body_position:]
    shell = re.sub(
        r"<html\b",
        f"<html {REPLICA_MARKER}",
        shell,
        count=1,
        flags=re.IGNORECASE,
    )
    return shell


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
        if shell.lower().count("<script") != 1 or "replica-shell.js" not in shell:
            raise BuildError(f"unexpected executable scripts in {shell_path}")
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
            rendered = render_snapshot(
                snapshots[route["sourceUrl"]]["html"], route, prefix
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
