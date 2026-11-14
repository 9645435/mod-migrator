from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from modmigrator.dependencies import resolve_missing_dependencies
from modmigrator.matcher import _apply_existing, _TargetIndex, scan_instances, target_path_for
from modmigrator.migrate import download_plans
from modmigrator.models import Instance, ModMeta, ModPlan, Project, TargetFile
from modmigrator.packs import read_pack_metadata, read_packs_folder


def make_pack(path: Path, description: str = "测试资源包") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "pack.mcmeta",
            json.dumps(
                {
                    "pack": {
                        "pack_format": 46,
                        "description": description,
                        "supported_formats": [46, 65],
                    }
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("assets/minecraft/pack.png", b"png")
    return path


def instance_at(game: Path, version_dir: Path | None = None) -> Instance:
    selected = version_dir or game
    return Instance(
        path=selected,
        game_dir=game,
        mods_dir=selected / "mods",
        mc_version="26.3",
        loader="fabric",
        loader_version="0.19.5",
    )


class PackMetadataTests(unittest.TestCase):
    def test_read_resourcepack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = make_pack(Path(tmp) / "Stay True 1.20.1.zip")
            meta = read_pack_metadata(path, "resourcepack")
            self.assertEqual(meta.kind, "resourcepack")
            self.assertEqual(meta.kind_label, "资源包")
            self.assertFalse(meta.error)
            self.assertEqual(meta.name, "Stay True")
            self.assertEqual(meta.version, "1.20.1")
            self.assertEqual(meta.loader, "minecraft")
            self.assertTrue(meta.sha1)

    def test_zip_without_pack_mcmeta_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Stay True 1.20.1.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("assets/minecraft/pack.png", b"png")
            meta = read_pack_metadata(path, "resourcepack")
            self.assertFalse(meta.error)
            self.assertEqual(meta.kind, "resourcepack")
            self.assertEqual(meta.name, "Stay True")
            self.assertEqual(meta.version, "1.20.1")
            self.assertFalse(meta.extra["has_pack_mcmeta"])

    def test_reject_non_zip_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.zip"
            path.write_bytes(b"this is not a zip file")
            meta = read_pack_metadata(path, "shader")
            self.assertTrue(meta.error)
            self.assertEqual(meta.kind, "shader")

    def test_malformed_pack_mcmeta_is_still_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Broken Meta 1.0.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("pack.mcmeta", "{not valid json")
            meta = read_pack_metadata(path, "resourcepack")
            self.assertFalse(meta.error)
            self.assertEqual(meta.name, "Broken Meta")
            self.assertIn("pack_meta_error", meta.extra)

    def test_shader_without_pack_mcmeta_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BSL_v8.4.01.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("shaders/shader.fsh", "void main() {}")
            meta = read_pack_metadata(path, "shader")
            self.assertFalse(meta.error)
            self.assertEqual(meta.kind, "shader")
            self.assertEqual(meta.name, "BSL")
            self.assertEqual(meta.version, "v8.4.01")

    def test_pack_folders_are_version_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / ".minecraft"
            version = game / "versions" / "26.3-Fabric"
            make_pack(version / "resourcepacks" / "Pack 2.0.zip")
            make_pack(version / "shaderpacks" / "Shader 1.0.zip")
            inst = instance_at(game, version)
            self.assertEqual(inst.resourcepacks_dir, version / "resourcepacks")
            self.assertEqual(inst.shaderpacks_dir, version / "shaderpacks")
            self.assertEqual(inst.resourcepack_count, 1)
            self.assertEqual(inst.shader_count, 1)
            self.assertEqual(len(read_packs_folder(inst.resourcepacks_dir, "resourcepack")), 1)


class ScanValidationTests(unittest.TestCase):
    def test_invalid_pack_is_not_sent_to_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = instance_at(Path(tmp) / "source")
            target = instance_at(Path(tmp) / "target")
            bad = ModMeta(
                path=Path("broken.zip"),
                name="broken",
                kind="resourcepack",
                is_mod=False,
                error="不是有效的资源包：缺少 pack.mcmeta",
            )

            def packs(folder, kind):
                return [bad] if kind == "resourcepack" else []

            with mock.patch("modmigrator.matcher.read_mods_folder", return_value=[]), mock.patch(
                "modmigrator.matcher.read_packs_folder", side_effect=packs
            ), mock.patch("modmigrator.matcher.ModrinthProvider") as provider:
                plans = scan_instances(source, target)

            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].status, "unsupported")
            provider.return_value.find_project.assert_not_called()
            provider.return_value.target_file.assert_not_called()


class CopyMigrationTests(unittest.TestCase):
    def test_copy_without_target_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_game = root / "source"
            target_game = root / "target"
            pack = make_pack(source_game / "resourcepacks" / "Stay True 1.20.1.zip")
            source = instance_at(source_game)
            target = instance_at(target_game)
            meta = read_pack_metadata(pack, "resourcepack")
            plan = ModPlan(meta=meta, status="copy", reason="no target")

            result = download_plans([plan], target)

            self.assertEqual(result[0].status, "copied")
            destination = target_game / "resourcepacks" / pack.name
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_bytes(), pack.read_bytes())
            self.assertEqual(target_path_for(target, plan), destination)

    def test_copy_skips_existing_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = make_pack(root / "source" / "resourcepacks" / "Pack 1.0.zip")
            target = instance_at(root / "target")
            meta = read_pack_metadata(pack, "resourcepack")
            plan = ModPlan(meta=meta, status="copy")

            download_plans([plan], target)
            again = ModPlan(meta=meta, status="copy")
            download_plans([again], target)

            self.assertEqual(again.status, "already")
            self.assertIn("同名文件", again.reason)

    def test_apply_existing_matches_pack_name(self) -> None:
        meta = ModMeta(
            path=Path("target/Stay True 1.20.1.zip"),
            name="Stay True",
            mod_id="stay-true",
        )
        index = _TargetIndex.build([meta])
        plan = ModPlan(
            meta=ModMeta(path=Path("source/Stay True 1.18.zip"), name="Stay True", mod_id="stay-true"),
            status="copy",
        )
        _apply_existing(plan, index)
        self.assertEqual(plan.status, "already")
        self.assertEqual(plan.existing, "Stay True 1.20.1.zip")


class DependencyResolutionTests(unittest.TestCase):
    def test_transitive_required_dependencies_are_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = instance_at(Path(tmp) / "target")
            target.mods_dir.mkdir(parents=True)

            dependencies = {
                "P1": [{"project_id": "P2", "dependency_type": "required"}],
                "P2": [],
            }

            class FakeModrinth:
                def __init__(self, _client) -> None:
                    pass

                def get_project(self, project_id: str) -> Project:
                    return Project(
                        platform="modrinth",
                        project_id=project_id,
                        slug=project_id.lower(),
                        title=f"Project {project_id}",
                    )

                def target_file(self, project_id, mc_version, loader, project_type="mod"):
                    return TargetFile(
                        platform="modrinth",
                        project_id=project_id,
                        file_id=f"file-{project_id}",
                        name=f"{project_id}.jar",
                        url=f"https://example.invalid/{project_id}.jar",
                        version_label="1.0.0",
                        mc_versions=[mc_version],
                        loaders=[loader],
                        size=10,
                        dependencies=dependencies[project_id],
                    )

                def get_version(self, _version_id):
                    return None

            downloaded: list[ModPlan] = []

            def fake_download(plans, _target, **_kwargs):
                for plan in plans:
                    plan.status = "downloaded"
                downloaded.extend(plans)
                return plans

            with mock.patch("modmigrator.dependencies.ModrinthProvider", FakeModrinth), mock.patch(
                "modmigrator.dependencies._installed_and_required",
                return_value=(set(), [{"project_id": "P1", "file_name": "parent.jar"}]),
            ), mock.patch("modmigrator.dependencies.download_plans", side_effect=fake_download):
                plans = resolve_missing_dependencies(target)

            self.assertEqual([p.project.project_id for p in plans], ["P1", "P2"])
            self.assertTrue(all(p.status == "downloaded" for p in plans))
            self.assertEqual(len(downloaded), 2)
            self.assertIn("自动补全缺少的前置", plans[0].reason)

    def test_missing_dependency_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = instance_at(Path(tmp) / "target")
            target.mods_dir.mkdir(parents=True)

            class FakeModrinth:
                def __init__(self, _client) -> None:
                    pass

                def get_project(self, _project_id):
                    return None

            with mock.patch("modmigrator.dependencies.ModrinthProvider", FakeModrinth), mock.patch(
                "modmigrator.dependencies._installed_and_required",
                return_value=(set(), [{"project_id": "MISSING"}]),
            ), mock.patch("modmigrator.dependencies.download_plans") as download:
                plans = resolve_missing_dependencies(target)

            self.assertEqual(plans[0].status, "dependency_missing")
            self.assertIn("无法获取前置项目资料", plans[0].reason)
            download.assert_called_once_with([], target, overwrite=False, cancel_event=None)


if __name__ == "__main__":
    unittest.main()
