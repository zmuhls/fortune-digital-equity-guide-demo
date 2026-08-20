#!/usr/bin/env python3
"""Build Railway's raw snapshot bundle from reviewed compressed captures.

The tracked ``replica-snapshots/`` directory is intentionally omitted from the
Railway upload.  This command verifies every reviewed ``.html.gz`` capture
against ``replica-manifest.json`` and writes the corresponding raw HTML into a
deterministic tar.xz bundle.  ``unpack_deploy_snapshots.py`` can then recreate
the checked captures during Railway's build without contacting the public site.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import pathlib
import stat
import tarfile
import tempfile
import urllib.parse
from collections.abc import Iterable


ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "site-index.json"
MANIFEST_PATH = ROOT / "replica-manifest.json"
SNAPSHOT_ROOT = ROOT / "replica-snapshots"
BUNDLE_PATH = ROOT / "replica-snapshots.raw.tar.xz"
SNAPSHOT_DIRECTORY = "replica-snapshots"
FORTUNE_HOSTS = {"fortunedigitalequity.org", "www.fortunedigitalequity.org"}


class BundleError(RuntimeError):
    """Raised when a snapshot bundle is not safe to publish."""


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_document(path: pathlib.Path, label: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError(f"cannot read {label}: {error}") from error
    if not isinstance(document, dict):
        raise BundleError(f"{label} must contain a JSON object")
    return document


def _required_text(page: dict, key: str, position: int) -> str:
    value = page.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BundleError(f"manifest page {position} is missing {key}")
    return value.strip()


def _required_size(page: dict, key: str, position: int) -> int:
    value = page.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BundleError(f"manifest page {position} has an invalid {key}")
    return value


def _required_sha256(page: dict, key: str, position: int) -> str:
    value = page.get(key)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value.lower())
    ):
        raise BundleError(f"manifest page {position} has an invalid {key}")
    return value.lower()


def _route_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in FORTUNE_HOSTS
        or parsed.query
        or parsed.fragment
    ):
        raise BundleError(f"manifest contains an unsafe public route: {url!r}")
    normalized = parsed.path.rstrip("/") or "/"
    parts = pathlib.PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts[1:]):
        raise BundleError(f"manifest contains an unsafe public route: {url!r}")
    return normalized


def _snapshot_file(value: str, position: int) -> pathlib.PurePosixPath:
    pure = pathlib.PurePosixPath(value)
    if (
        pure.is_absolute()
        or len(pure.parts) != 2
        or pure.parts[0] != SNAPSHOT_DIRECTORY
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.suffixes != [".html", ".gz"]
    ):
        raise BundleError(f"manifest page {position} has an unsafe snapshot file")
    return pure


def _raw_member_name(snapshot_file: pathlib.PurePosixPath) -> str:
    return snapshot_file.with_suffix("").as_posix()


def load_manifest(manifest_path: pathlib.Path = MANIFEST_PATH) -> list[dict]:
    """Load and validate the complete manifest identity and source contract."""

    manifest = _read_document(manifest_path, "replica-manifest.json")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise BundleError("replica-manifest.json must contain a non-empty pages list")
    if manifest.get("route_count") != len(pages):
        raise BundleError("replica manifest route_count does not match its pages list")

    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_paths: set[str] = set()
    seen_files: set[str] = set()
    checked: list[dict] = []
    for position, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise BundleError(f"manifest page {position} is not an object")
        page_id = _required_text(page, "id", position)
        url = _required_text(page, "url", position)
        path = _required_text(page, "path", position)
        snapshot_file = _snapshot_file(_required_text(page, "file", position), position)
        if page.get("status") != 200 or page.get("final_url") != url:
            raise BundleError(f"manifest page {position} is not a canonical HTTP 200 capture")
        if _route_path(url) != path:
            raise BundleError(f"manifest page {position} path does not match its URL")
        if snapshot_file.name != f"{page_id}.html.gz":
            raise BundleError(f"manifest page {position} snapshot name does not match its id")
        for label, seen, value in (
            ("id", seen_ids, page_id),
            ("URL", seen_urls, url),
            ("path", seen_paths, path),
            ("snapshot file", seen_files, snapshot_file.as_posix()),
        ):
            if value in seen:
                raise BundleError(f"replica manifest has a duplicate {label}: {value}")
            seen.add(value)
        checked.append(
            {
                "id": page_id,
                "url": url,
                "path": path,
                "file": snapshot_file,
                "source_bytes": _required_size(page, "source_bytes", position),
                "source_sha256": _required_sha256(page, "source_sha256", position),
                "snapshot_bytes": _required_size(page, "snapshot_bytes", position),
                "snapshot_sha256": _required_sha256(page, "snapshot_sha256", position),
            }
        )
    return sorted(checked, key=lambda page: page["file"].as_posix())


def verify_route_parity(pages: Iterable[dict], index_path: pathlib.Path = INDEX_PATH) -> None:
    """Ensure the bundle will agree with the exact routes Railway will publish."""

    index = _read_document(index_path, "site-index.json")
    index_pages = index.get("pages")
    if not isinstance(index_pages, list) or not index_pages:
        raise BundleError("site-index.json must contain a non-empty pages list")
    if index.get("unique_urls") != len(index_pages):
        raise BundleError("site-index.json unique_urls does not match its pages list")

    expected: dict[str, tuple[str, str]] = {}
    for position, page in enumerate(index_pages, start=1):
        if not isinstance(page, dict):
            raise BundleError(f"site index page {position} is not an object")
        url = _required_text(page, "url", position)
        page_id = _required_text(page, "id", position)
        path = _route_path(url)
        if url in expected:
            raise BundleError(f"site-index.json has a duplicate URL: {url}")
        expected[url] = (page_id, path)

    actual = {page["url"]: (page["id"], page["path"]) for page in pages}
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        mismatched = sorted(
            url for url in set(expected) & set(actual) if expected[url] != actual[url]
        )
        raise BundleError(
            "replica manifest and site index differ; "
            f"missing={missing[:3]!r}, extra={extra[:3]!r}, mismatched={mismatched[:3]!r}"
        )


def _checked_snapshot_path(snapshot_root: pathlib.Path, page: dict) -> pathlib.Path:
    root = snapshot_root.resolve()
    path = (root / page["file"].name).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise BundleError(f"snapshot path escaped its root: {page['file']}") from error
    return path


def verify_snapshot_set(pages: Iterable[dict], snapshot_root: pathlib.Path = SNAPSHOT_ROOT) -> None:
    """Reject missing, extra, nested, or linked inputs before packaging."""

    if not snapshot_root.is_dir():
        raise BundleError(f"reviewed snapshot directory is missing: {snapshot_root}")
    expected = {page["file"].as_posix() for page in pages}
    actual: set[str] = set()
    for item in snapshot_root.rglob("*"):
        relative = item.relative_to(snapshot_root).as_posix()
        if item.is_symlink():
            raise BundleError(f"reviewed snapshot directory contains a symlink: {relative}")
        if item.is_dir():
            raise BundleError(f"reviewed snapshot directory contains a nested directory: {relative}")
        if not item.is_file():
            raise BundleError(f"reviewed snapshot directory contains an unsupported entry: {relative}")
        actual.add(f"{SNAPSHOT_DIRECTORY}/{relative}")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise BundleError(
            "reviewed snapshot files and manifest differ; "
            f"missing={missing[:3]!r}, extra={extra[:3]!r}"
        )


def expanded_snapshot(page: dict, snapshot_root: pathlib.Path = SNAPSHOT_ROOT) -> bytes:
    """Read one gzip capture and verify both compressed and expanded identities."""

    snapshot_path = _checked_snapshot_path(snapshot_root, page)
    try:
        mode = os.lstat(snapshot_path).st_mode
        if not stat.S_ISREG(mode):
            raise BundleError(f"snapshot is not a regular file: {page['file']}")
        compressed = snapshot_path.read_bytes()
    except OSError as error:
        raise BundleError(f"cannot read reviewed snapshot: {page['file']}: {error}") from error
    if (
        len(compressed) != page["snapshot_bytes"]
        or sha256(compressed) != page["snapshot_sha256"]
    ):
        raise BundleError(f"compressed snapshot hash or size does not match manifest: {page['file']}")
    try:
        expanded = gzip.decompress(compressed)
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise BundleError(f"cannot expand reviewed snapshot: {page['file']}: {error}") from error
    if (
        len(expanded) != page["source_bytes"]
        or sha256(expanded) != page["source_sha256"]
    ):
        raise BundleError(f"expanded snapshot hash or size does not match manifest: {page['file']}")
    return expanded


def verify_bundle(bundle_path: pathlib.Path, pages: Iterable[dict]) -> None:
    """Verify a raw bundle independently before it replaces the current one."""

    expected = {_raw_member_name(page["file"]): page for page in pages}
    seen: set[str] = set()
    try:
        with tarfile.open(bundle_path, "r:xz") as archive:
            for member in archive.getmembers():
                pure = pathlib.PurePosixPath(member.name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or member.name not in expected
                    or not member.isfile()
                ):
                    raise BundleError("snapshot bundle contains an unsafe or unexpected entry")
                if member.name in seen:
                    raise BundleError(f"snapshot bundle contains a duplicate entry: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise BundleError(f"cannot read snapshot bundle entry: {member.name}")
                raw = source.read()
                page = expected[member.name]
                if len(raw) != page["source_bytes"] or sha256(raw) != page["source_sha256"]:
                    raise BundleError(f"snapshot bundle changed reviewed HTML: {member.name}")
                seen.add(member.name)
    except (OSError, tarfile.TarError) as error:
        raise BundleError(f"cannot verify snapshot bundle: {error}") from error
    if seen != set(expected):
        missing = sorted(set(expected) - seen)
        raise BundleError(f"snapshot bundle is missing reviewed HTML: {missing[:3]!r}")


def _fsync_directory(path: pathlib.Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def create_bundle(
    *,
    manifest_path: pathlib.Path = MANIFEST_PATH,
    index_path: pathlib.Path = INDEX_PATH,
    snapshot_root: pathlib.Path = SNAPSHOT_ROOT,
    bundle_path: pathlib.Path = BUNDLE_PATH,
) -> int:
    """Create an atomically published, deterministic bundle and return its route count."""

    pages = load_manifest(manifest_path)
    verify_route_parity(pages, index_path)
    verify_snapshot_set(pages, snapshot_root)
    output = pathlib.Path(os.path.abspath(bundle_path))
    if not output.parent.is_dir():
        raise BundleError(f"bundle output directory is missing: {output.parent}")
    if output.exists() and not stat.S_ISREG(os.lstat(output).st_mode):
        raise BundleError("bundle output must be a regular file")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = pathlib.Path(temporary_name)
    os.close(descriptor)
    try:
        with tarfile.open(
            temporary,
            "w:xz",
            format=tarfile.USTAR_FORMAT,
            preset=9,
        ) as archive:
            for page in pages:
                raw = expanded_snapshot(page, snapshot_root)
                member = tarfile.TarInfo(_raw_member_name(page["file"]))
                member.size = len(raw)
                member.mode = 0o644
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                archive.addfile(member, io.BytesIO(raw))
        os.chmod(temporary, 0o644)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        verify_bundle(temporary, pages)
        os.replace(temporary, output)
        _fsync_directory(output.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return len(pages)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package reviewed rendered snapshots for Railway without recapturing the site."
    )
    parser.add_argument("--manifest", type=pathlib.Path, default=MANIFEST_PATH)
    parser.add_argument("--index", type=pathlib.Path, default=INDEX_PATH)
    parser.add_argument("--snapshot-root", type=pathlib.Path, default=SNAPSHOT_ROOT)
    parser.add_argument("--output", type=pathlib.Path, default=BUNDLE_PATH)
    options = parser.parse_args()
    count = create_bundle(
        manifest_path=options.manifest,
        index_path=options.index,
        snapshot_root=options.snapshot_root,
        bundle_path=options.output,
    )
    print(f"packed {count} verified replica snapshots into {options.output}")


if __name__ == "__main__":
    main()
