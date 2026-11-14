from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modmigrator.mcinstance import (
    COMMON_MC_VERSIONS,
    _extract_mc_version,
    detect_instance,
    list_local_versions,
)


def write_version(mc_dir: Path, name: str, data: dict) -> Path:
    version_dir = mc_dir / "versions" / name
    version_dir.mkdir(parents=True)
    (version_dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return version_dir


class McVersionExtractionTests(unittest.TestCase):
    def test_modern_release_ids(self) -> None:
        self.assertEqual(_extract_mc_version("26.2"), "26.2")
        self.assertEqual(_extract_mc_version("26.3"), "26.3")
        self.assertEqual(_extract_mc_version("26.1.2"), "26.1.2")

    def test_launcher_loader_build(self) -> None:
        self.assertEqual(_extract_mc_version("26.3-Fabric 0.19.5"), "26.3")
        self.assertEqual(_extract_mc_version("26.3-forge-58.0.1"), "26.3")

    def test_snapshot_belongs_to_release(self) -> None:
        self.assertEqual(_extract_mc_version("26.3-snapshot-7"), "26.3")
        self.assertEqual(_extract_mc_version("26.4-snapshot-8"), "26.4")

    def test_old_versions_are_unchanged(self) -> None:
        self.assertEqual(_extract_mc_version("1.21.1"), "1.21.1")
        self.assertEqual(_extract_mc_version("1.21.11-pre1"), "1.21.11-pre1")
        self.assertEqual(_extract_mc_version("26w30a"), "")

    def test_common_versions_include_26x(self) -> None:
        for version in ("26.1", "26.1.1", "26.1.2", "26.2", "26.3"):
            self.assertIn(version, COMMON_MC_VERSIONS)


class InstanceDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.mc = Path(self.temp.name) / ".minecraft"
        (self.mc / "mods").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_latest_release_wins_over_snapshot(self) -> None:
        write_version(self.mc, "26.2", {"id": "26.2", "type": "release"})
        write_version(self.mc, "26.3", {"id": "26.3", "type": "release"})
        write_version(self.mc, "26.3-Fabric 0.19.5", {
            "id": "26.3-Fabric 0.19.5",
            "type": "release",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
        })
        write_version(self.mc, "26.3-snapshot-7", {"id": "26.3-snapshot-7", "type": "snapshot"})
        write_version(self.mc, "26.4-snapshot-2", {"id": "26.4-snapshot-2", "type": "snapshot"})
        (self.mc / "launcher_profiles.json").write_text(json.dumps({
            "profiles": {
                "a": {"lastVersionId": "latest-snapshot", "lastUsed": "1970-01-01T00:00:00.000Z"},
                "b": {"lastVersionId": "latest-release", "lastUsed": "2026-08-12T03:29:11.229Z"},
            }
        }), encoding="utf-8")

        instance = detect_instance(self.mc)
        self.assertEqual(instance.mc_version, "26.3")
        self.assertEqual(instance.version_id, "26.3")
        self.assertEqual(instance.loader, "vanilla")
        self.assertIn("latest-release", instance.notes[0])

    def test_fabric_build_folder_is_mc_26_3(self) -> None:
        version_dir = write_version(self.mc, "26.3-Fabric 0.19.5", {
            "id": "26.3-Fabric 0.19.5",
            "type": "release",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
        })
        instance = detect_instance(version_dir)
        self.assertEqual(instance.mc_version, "26.3")
        self.assertEqual(instance.loader, "fabric")
        self.assertEqual(instance.loader_version, "0.19.5")

    def test_explicit_26_2_directory(self) -> None:
        version_dir = write_version(self.mc, "26.2", {"id": "26.2", "type": "release"})
        instance = detect_instance(version_dir)
        self.assertEqual(instance.mc_version, "26.2")
        self.assertEqual(instance.version_id, "26.2")

    def test_snapshot_maps_to_release(self) -> None:
        version_dir = write_version(self.mc, "26.3-snapshot-7", {
            "id": "26.3-snapshot-7",
            "type": "snapshot",
        })
        instance = detect_instance(version_dir)
        self.assertEqual(instance.mc_version, "26.3")

    def test_list_local_versions(self) -> None:
        write_version(self.mc, "26.2", {"id": "26.2", "type": "release"})
        write_version(self.mc, "26.3", {"id": "26.3", "type": "release"})
        self.assertEqual(list_local_versions(self.mc), ["26.2", "26.3"])

    def test_explicit_version_path_is_preserved(self) -> None:
        version_dir = write_version(self.mc, "26.2-Fabric 0.20.1", {
            "id": "26.2-Fabric 0.20.1",
            "type": "release",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.20.1"}],
        })
        mods = version_dir / "mods"
        mods.mkdir()
        (mods / "one.jar").write_bytes(b"")
        instance = detect_instance(version_dir)
        self.assertEqual(instance.path, version_dir)
        self.assertEqual(instance.game_dir, self.mc)
        self.assertEqual(instance.mods_dir, mods)
        self.assertEqual(instance.mod_count, 1)

    def test_root_prefers_version_isolated_mods(self) -> None:
        write_version(self.mc, "26.3", {"id": "26.3", "type": "release"})
        fabric = write_version(self.mc, "26.3-Fabric 0.19.5", {
            "id": "26.3-Fabric 0.19.5",
            "type": "release",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
        })
        mods = fabric / "mods"
        mods.mkdir()
        (mods / "one.jar").write_bytes(b"")
        (mods / "two.jar").write_bytes(b"")
        (self.mc / "launcher_profiles.json").write_text(json.dumps({
            "profiles": {
                "b": {"lastVersionId": "latest-release", "lastUsed": "2026-08-12T03:29:11.229Z"},
            }
        }), encoding="utf-8")
        instance = detect_instance(self.mc)
        self.assertEqual(instance.version_id, "26.3-Fabric 0.19.5")
        self.assertEqual(instance.loader, "fabric")
        self.assertEqual(instance.mods_dir, mods)
        self.assertEqual(instance.mod_count, 2)
        self.assertIn("已选择带 2 个模组", instance.notes[0])

    def test_global_mods_folder_stays_authoritative(self) -> None:
        write_version(self.mc, "26.3", {"id": "26.3", "type": "release"})
        fabric = write_version(self.mc, "26.3-Fabric 0.19.5", {
            "id": "26.3-Fabric 0.19.5",
            "type": "release",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
        })
        (fabric / "mods").mkdir()
        (fabric / "mods" / "isolated.jar").write_bytes(b"")
        for name in ("a.jar", "b.jar", "c.jar"):
            (self.mc / "mods" / name).write_bytes(b"")
        instance = detect_instance(self.mc)
        self.assertEqual(instance.mods_dir, self.mc / "mods")
        self.assertEqual(instance.mod_count, 3)


if __name__ == "__main__":
    unittest.main()
