#!/usr/bin/env python3
"""Restore the exact reviewed HTML from Railway's compact snapshot bundle."""

import gzip
import hashlib
import json
import pathlib
import shutil
import tarfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "replica-snapshots"
BUNDLE = ROOT / "replica-snapshots.raw.tar.xz"
MANIFEST_PATH = ROOT / "replica-manifest.json"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compressed_snapshots_are_current(manifest: dict, capture_root=None) -> bool:
    root = capture_root or ROOT
    for page in manifest["pages"]:
        snapshot = root / page["file"]
        try:
            value = snapshot.read_bytes()
        except OSError:
            return False
        if len(value) != page["snapshot_bytes"] or sha256(value) != page["snapshot_sha256"]:
            return False
    return True


def safe_extract_bundle(capture_root=None) -> None:
    root = (capture_root or ROOT).resolve()
    bundle = root / "replica-snapshots.raw.tar.xz" if capture_root else BUNDLE
    with tarfile.open(bundle, "r:xz") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            inside_snapshot_root = member.name == "replica-snapshots" or member.name.startswith(
                "replica-snapshots/"
            )
            if root not in target.parents or not inside_snapshot_root:
                raise RuntimeError("The Railway snapshot bundle contains an unsafe path.")
            if not (member.isdir() or member.isfile()):
                raise RuntimeError("The Railway snapshot bundle contains an unsupported entry.")
        archive.extractall(root)


def restore_capture(capture_root=None) -> None:
    root = capture_root or ROOT
    snapshots = root / "replica-snapshots" if capture_root else SNAPSHOTS
    bundle = root / "replica-snapshots.raw.tar.xz" if capture_root else BUNDLE
    manifest_path = root / "replica-manifest.json" if capture_root else MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if compressed_snapshots_are_current(manifest, root):
        return
    if not bundle.is_file():
        raise RuntimeError("The reviewed Railway snapshot bundle is missing.")

    shutil.rmtree(snapshots, ignore_errors=True)
    snapshots.mkdir(parents=True)
    safe_extract_bundle(capture_root)

    expected_raw = {
        pathlib.Path(page["file"]).name.removesuffix(".gz"): page
        for page in manifest["pages"]
    }
    actual_raw = {item.name for item in snapshots.glob("*.html")}
    if actual_raw != set(expected_raw):
        raise RuntimeError("The Railway snapshot bundle does not match the reviewed manifest.")

    for filename, page in expected_raw.items():
        raw_path = snapshots / filename
        raw = raw_path.read_bytes()
        if len(raw) != page["source_bytes"] or sha256(raw) != page["source_sha256"]:
            raise RuntimeError(f"The Railway snapshot bundle changed reviewed HTML: {filename}")
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        raw_path.with_suffix(f"{raw_path.suffix}.gz").write_bytes(compressed)
        raw_path.unlink()
        page["snapshot_bytes"] = len(compressed)
        page["snapshot_sha256"] = sha256(compressed)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not compressed_snapshots_are_current(manifest, root):
        raise RuntimeError("The restored Railway snapshots failed manifest verification.")
    print(f"restored {manifest['route_count']} verified replica snapshots")


def main() -> None:
    restore_capture()
    mobile_root = ROOT / "replica-mobile"
    if (mobile_root / "replica-manifest.json").is_file():
        restore_capture(mobile_root)


if __name__ == "__main__":
    main()
