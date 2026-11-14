from __future__ import annotations

import json
import logging
import urllib.parse
from difflib import SequenceMatcher
from typing import Any

from ..models import ModMeta, Project, TargetFile
from ..util import norm_key, slugify
from .http import HttpClient


BASE = "https://api.modrinth.com/v2"
PROJECT_URL = {
    "mod": "https://modrinth.com/mod/{}",
    "resourcepack": "https://modrinth.com/resourcepack/{}",
    "shader": "https://modrinth.com/shader/{}",
}
LOGGER = logging.getLogger("modmigrator.modrinth")

LOADER_ALIASES = {
    "fabric": "fabric",
    "quilt": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}


class ModrinthProvider:
    def __init__(self, client: HttpClient):
        self.client = client

    # --------------------------------------------------------------- projects
    def lookup_hashes(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        """Return sha1 -> Modrinth version object using the batch endpoint."""
        result: dict[str, dict[str, Any]] = {}
        unique = [h for h in dict.fromkeys(hashes) if h]
        for start in range(0, len(unique), 100):
            chunk = unique[start : start + 100]
            try:
                data = self.client.post_json(
                    f"{BASE}/version_files",
                    {"hashes": chunk, "algorithm": "sha1"},
                    ttl=24 * 3600,
                )
            except Exception as exc:
                LOGGER.warning("Modrinth 哈希查询失败：%s", exc)
                continue
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        result[str(key).lower()] = value
        return result

    def get_projects(self, ids: list[str]) -> dict[str, Project]:
        result: dict[str, Project] = {}
        unique = [i for i in dict.fromkeys(ids) if i]
        for start in range(0, len(unique), 50):
            chunk = unique[start : start + 50]
            params = urllib.parse.urlencode({"ids": json.dumps(chunk)})
            try:
                data = self.client.get_json(f"{BASE}/projects?{params}", ttl=12 * 3600)
            except Exception as exc:
                LOGGER.warning("Modrinth 项目查询失败：%s", exc)
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                project = self._project_from_api(item, "文件哈希", 1.0)
                result[project.project_id] = project
        return result

    def get_project(self, project_id: str) -> Project | None:
        try:
            data = self.client.get_json(f"{BASE}/project/{project_id}", ttl=12 * 3600)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return self._project_from_api(data, "项目 ID", 1.0)

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        try:
            data = self.client.get_json(
                f"{BASE}/version/{version_id}", ttl=12 * 3600
            )
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _project_from_api(
        self, data: dict[str, Any], method: str, confidence: float
    ) -> Project:
        slug = str(data.get("slug") or "")
        project_type = str(data.get("project_type") or "mod")
        return Project(
            platform="modrinth",
            project_id=str(data.get("id") or ""),
            slug=slug,
            title=str(data.get("title") or slug or data.get("id") or ""),
            url=PROJECT_URL.get(project_type, PROJECT_URL["mod"]).format(slug) if slug else "",
            description=str(data.get("description") or ""),
            icon_url=str(data.get("icon_url") or ""),
            match_method=method,
            confidence=confidence,
            extra={
                "downloads": data.get("downloads", 0),
                "loaders": data.get("loaders", []),
                "game_versions": data.get("game_versions", []),
                "project_type": project_type,
            },
        )

    # ----------------------------------------------------------------- search
    def search(
        self, query: str, limit: int = 8, project_type: str = "mod"
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": limit,
                "facets": json.dumps([[f"project_type:{project_type}"]]),
            }
        )
        try:
            data = self.client.get_json(f"{BASE}/search?{params}", ttl=6 * 3600)
        except Exception as exc:
            LOGGER.warning("Modrinth 搜索失败：%s", exc)
            return []
        hits = data.get("hits") if isinstance(data, dict) else None
        return [h for h in (hits or []) if isinstance(h, dict)]

    def find_project(self, meta: ModMeta, project_type: str = "mod") -> Project | None:
        queries = []
        if meta.name:
            queries.append(meta.name)
        if meta.mod_id and meta.mod_id != meta.name:
            queries.append(meta.mod_id.replace("-", " ").replace("_", " "))
        if meta.name and meta.name != meta.mod_id:
            queries.append(f"{meta.name} minecraft")
        queries = [q for q in dict.fromkeys(queries) if q and len(q.strip()) >= 2]

        best: tuple[float, Project] | None = None
        seen: set[str] = set()
        for query in queries[:3]:
            for hit in self.search(query, limit=8, project_type=project_type):
                project_id = str(hit.get("project_id") or "")
                if not project_id or project_id in seen:
                    continue
                seen.add(project_id)
                score = self._score(hit, meta)
                if score < 0.84:
                    continue
                project = self._project_from_api(
                    hit, "名称搜索", score
                )
                if best is None or score > best[0]:
                    best = (score, project)
            if best and best[0] >= 0.97:
                break
        return best[1] if best else None

    @staticmethod
    def _score(hit: dict[str, Any], meta: ModMeta) -> float:
        slug = norm_key(str(hit.get("slug") or ""))
        title = norm_key(str(hit.get("title") or ""))
        mod_id = norm_key(meta.mod_id)
        name = norm_key(meta.name)
        if not (slug or title):
            return 0.0
        if mod_id and slug == mod_id:
            return 0.99
        if name and title == name:
            return 0.98
        if name and title and (title.startswith(name) or name.startswith(title)) and min(len(title), len(name)) >= 5:
            return 0.94
        if mod_id and title and (title.startswith(mod_id) or mod_id.startswith(title)) and min(len(mod_id), len(title)) >= 5:
            return 0.9
        if mod_id and slug and (slug.startswith(mod_id) or mod_id.startswith(slug)) and min(len(slug), len(mod_id)) >= 5:
            return 0.88
        if name and title:
            ratio = SequenceMatcher(None, name, title).ratio()
            if ratio >= 0.9:
                return ratio
        return 0.0

    # ---------------------------------------------------------------- versions
    def target_file(
        self, project_id: str, mc_version: str, loader: str, project_type: str = "mod"
    ) -> TargetFile | None:
        params_data: dict[str, Any] = {"game_versions": json.dumps([mc_version])}
        if project_type == "mod":
            loaders = [loader]
            if loader == "quilt":
                loaders = ["quilt", "fabric"]
            params_data["loaders"] = json.dumps(loaders)
        params = urllib.parse.urlencode(params_data)
        try:
            data = self.client.get_json(
                f"{BASE}/project/{project_id}/version?{params}", ttl=3 * 3600
            )
        except Exception as exc:
            LOGGER.warning("Modrinth 版本查询失败（%s）：%s", project_id, exc)
            return None
        if not isinstance(data, list):
            return None

        candidates: list[dict[str, Any]] = []
        for version in data:
            if not isinstance(version, dict):
                continue
            game_versions = [str(v) for v in (version.get("game_versions") or [])]
            version_loaders = [str(v).lower() for v in (version.get("loaders") or [])]
            if mc_version not in game_versions:
                continue
            if project_type == "mod" and loader not in version_loaders and not (
                loader == "quilt" and "fabric" in version_loaders
            ):
                continue
            candidates.append(version)
        if not candidates:
            return None

        type_rank = {"release": 3, "beta": 2, "alpha": 1}

        def sort_key(version: dict[str, Any]):
            version_loaders = [str(v).lower() for v in (version.get("loaders") or [])]
            exact_loader = 1 if project_type == "mod" and loader in version_loaders else 0
            return (
                exact_loader,
                type_rank.get(str(version.get("version_type") or ""), 0),
                str(version.get("date_published") or ""),
            )

        chosen = max(candidates, key=sort_key)
        files = chosen.get("files")
        if not isinstance(files, list) or not files:
            return None
        primary = next(
            (f for f in files if isinstance(f, dict) and f.get("primary")),
            next((f for f in files if isinstance(f, dict) and f.get("url")), None),
        )
        if not primary:
            return None
        url = str(primary.get("url") or "")
        filename = str(primary.get("filename") or "")
        if not url or not filename:
            return None
        hashes = primary.get("hashes") if isinstance(primary.get("hashes"), dict) else {}
        notes = []
        if project_type != "mod":
            notes.append("资源包/光影仅按 Minecraft 版本匹配，不区分加载器")
        elif loader == "quilt" and "quilt" not in [
            str(v).lower() for v in (chosen.get("loaders") or [])
        ]:
            notes.append("该文件标记为 Fabric，Quilt 通常可兼容")
        return TargetFile(
            platform="modrinth",
            project_id=project_id,
            file_id=str(chosen.get("id") or ""),
            name=filename,
            url=url,
            version_label=str(chosen.get("version_number") or ""),
            mc_versions=[str(v) for v in (chosen.get("game_versions") or [])],
            loaders=[str(v) for v in (chosen.get("loaders") or [])],
            release_type=str(chosen.get("version_type") or ""),
            size=int(primary.get("size") or 0),
            published=str(chosen.get("date_published") or ""),
            sha1=str(hashes.get("sha1") or ""),
            dependencies=[
                d for d in (chosen.get("dependencies") or []) if isinstance(d, dict)
            ],
            notes=notes,
        )


def slug_for_project(title: str) -> str:
    return slugify(title)
