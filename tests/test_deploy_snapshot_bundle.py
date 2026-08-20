#!/usr/bin/env python3
"""Network-free contracts for the Railway raw snapshot bundle builder."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import pathlib
import shutil
import tarfile
import tempfile
import unittest


DEMO = pathlib.Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, DEMO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pack = load_script("pack_deploy_snapshots")
unpack = load_script("unpack_deploy_snapshots")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class DeploySnapshotBundleTests(unittest.TestCase):
    def write_fixture(self, root: pathlib.Path) -> tuple[dict, bytes]:
        snapshots = root / "replica-snapshots"
        snapshots.mkdir()
        routes = (
            (
                "page-home",
                "https://www.fortunedigitalequity.org/",
                "/",
                b"<!doctype html><html><body><main>Home</main></body></html>",
            ),
            (
                "page-contact",
                "https://www.fortunedigitalequity.org/contact",
                "/contact",
                b"<!doctype html><html><body><main>Contact</main></body></html>",
            ),
        )
        pages = []
        raw_by_name = {}
        for page_id, url, route_path, raw in routes:
            filename = f"{page_id}.html.gz"
            compressed = gzip.compress(raw, compresslevel=9, mtime=0)
            (snapshots / filename).write_bytes(compressed)
            pages.append(
                {
                    "id": page_id,
                    "url": url,
                    "final_url": url,
                    "path": route_path,
                    "file": f"replica-snapshots/{filename}",
                    "status": 200,
                    "source_bytes": len(raw),
                    "source_sha256": sha256(raw),
                    "snapshot_bytes": len(compressed),
                    "snapshot_sha256": sha256(compressed),
                }
            )
            raw_by_name[f"replica-snapshots/{page_id}.html"] = raw
        manifest = {"route_count": len(pages), "pages": pages}
        (root / "replica-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (root / "site-index.json").write_text(
            json.dumps(
                {
                    "unique_urls": len(pages),
                    "pages": [{"id": page["id"], "url": page["url"]} for page in pages],
                }
            ),
            encoding="utf-8",
        )
        return raw_by_name, (root / "replica-manifest.json").read_bytes()

    def build(self, root: pathlib.Path, output: pathlib.Path) -> int:
        return pack.create_bundle(
            manifest_path=root / "replica-manifest.json",
            index_path=root / "site-index.json",
            snapshot_root=root / "replica-snapshots",
            bundle_path=output,
        )

    def test_bundle_is_deterministic_manifest_bound_and_leaves_sources_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            expected_raw, manifest_before = self.write_fixture(root)
            snapshots_before = {
                item.name: item.read_bytes()
                for item in (root / "replica-snapshots").iterdir()
            }
            first = root / "first.tar.xz"
            second = root / "second.tar.xz"

            self.assertEqual(self.build(root, first), 2)
            self.assertEqual(self.build(root, second), 2)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                (root / "replica-manifest.json").read_bytes(), manifest_before
            )
            self.assertEqual(
                {
                    item.name: item.read_bytes()
                    for item in (root / "replica-snapshots").iterdir()
                },
                snapshots_before,
            )
            with tarfile.open(first, "r:xz") as archive:
                self.assertEqual(archive.getnames(), sorted(expected_raw))
                self.assertTrue(all(member.isfile() for member in archive.getmembers()))
                for name, raw in expected_raw.items():
                    source = archive.extractfile(name)
                    assert source is not None
                    self.assertEqual(source.read(), raw)

    def test_bundle_round_trips_through_railway_unpack_with_original_source_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = pathlib.Path(directory) / "source"
            source_root.mkdir()
            expected_raw, _ = self.write_fixture(source_root)
            source_bundle = source_root / "replica-snapshots.raw.tar.xz"
            self.build(source_root, source_bundle)

            railway_root = pathlib.Path(directory) / "railway"
            railway_root.mkdir()
            shutil.copy2(source_root / "replica-manifest.json", railway_root)
            shutil.copy2(source_bundle, railway_root / source_bundle.name)
            original_root = unpack.ROOT
            original_snapshots = unpack.SNAPSHOTS
            original_bundle = unpack.BUNDLE
            original_manifest = unpack.MANIFEST_PATH
            rebuilt_snapshots_current = False
            try:
                unpack.ROOT = railway_root
                unpack.SNAPSHOTS = railway_root / "replica-snapshots"
                unpack.BUNDLE = railway_root / "replica-snapshots.raw.tar.xz"
                unpack.MANIFEST_PATH = railway_root / "replica-manifest.json"
                unpack.main()
                rebuilt_manifest = json.loads(unpack.MANIFEST_PATH.read_text())
                rebuilt_snapshots_current = unpack.compressed_snapshots_are_current(
                    rebuilt_manifest
                )
            finally:
                unpack.ROOT = original_root
                unpack.SNAPSHOTS = original_snapshots
                unpack.BUNDLE = original_bundle
                unpack.MANIFEST_PATH = original_manifest

            manifest = json.loads((railway_root / "replica-manifest.json").read_text())
            self.assertEqual(manifest["route_count"], 2)
            for page in manifest["pages"]:
                snapshot = railway_root / page["file"]
                raw = gzip.decompress(snapshot.read_bytes())
                self.assertEqual(raw, expected_raw[page["file"].removesuffix(".gz")])
                self.assertEqual(len(raw), page["source_bytes"])
                self.assertEqual(sha256(raw), page["source_sha256"])
            self.assertTrue(rebuilt_snapshots_current)

    def test_bad_input_never_replaces_an_existing_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_fixture(root)
            output = root / "replica-snapshots.raw.tar.xz"
            output.write_bytes(b"previous reviewed bundle")
            snapshot = root / "replica-snapshots" / "page-home.html.gz"
            snapshot.write_bytes(snapshot.read_bytes() + b"tampered")

            with self.assertRaisesRegex(pack.BundleError, "compressed snapshot hash"):
                self.build(root, output)
            self.assertEqual(output.read_bytes(), b"previous reviewed bundle")

    def test_route_parity_mismatch_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self.write_fixture(root)
            index_path = root / "site-index.json"
            index = json.loads(index_path.read_text())
            index["pages"][0]["id"] = "different-page-id"
            index_path.write_text(json.dumps(index), encoding="utf-8")

            with self.assertRaisesRegex(pack.BundleError, "manifest and site index differ"):
                self.build(root, root / "replica-snapshots.raw.tar.xz")
            self.assertFalse((root / "replica-snapshots.raw.tar.xz").exists())


if __name__ == "__main__":
    unittest.main()
