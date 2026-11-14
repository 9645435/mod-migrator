from __future__ import annotations

import logging
import time
import urllib.parse
from difflib import SequenceMatcher
from typing import Any

from ..models import ModMeta, Project, TargetFile
from ..util import norm_key, slugify
from .http import HttpClient, HttpError, quote_url_path


OFFICIAL_BASE = "https://api.curseforge.com/v1"
CFWIDGET_BASE = "https://api.cfwidget.com"
PROJECT_URL = {
    "mod": "https://www.curseforge.com/minecraft/mc-mods/{}",
    "resourcepack": "https://www.curseforge.com/minecraft/texture-packs/{}",
    "shader": "https://www.curseforge.com/minecraft/shaders/{}",
}
CFWIDGET_PATH = {
    "mod": "mc-mods",
    "resourcepack": "texture-packs",
    "shader": "shaders",
}
GAME_ID = 432
CLASS_ID = {"mod": 6, "resourcepack": 12, "shader": 6552}
CLASS_ID_MODS = 6

MOD_LOADER_TYPE = {
    "forge": 1,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}
LOADER_TAG = {
    "forge": "Forge",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "neoforge": "NeoForge",
}
ALL_LOADER_TAGS = set(LOADER_TAG.values())
RELEASE_RANK = {1: 3, 2: 2, 3: 1, "release": 3, "beta": 2, "alpha": 1}
LOGGER = logging.getLogger("modmigrator.curseforge")


class CurseForgeProvider:
    def __init__(self, client: HttpClient, api_key: str = ""):
        self.client = client
        self.api_key = (api_key or "").strip()
        self.official_disabled = False

    @property
    def has_key(self) -> bool:
        return bool(self.api_key) and not self.official_disabled

    # ---------------------------------------------------------------- projects
    def find_project(
        self,
        meta: ModMeta,
        modrinth_project: Project | None = None,
        project_type: str = "mod",
    ) -> Project | None:
        best: tuple[float, Project] | None = None

        if self.has_key:
            for query in self._queries(meta):
                for hit in self._official_search(query, project_type):
                    score = self._score_project(
                        str(hit.get("name") or ""),
                        str(hit.get("slug") or ""),
                        meta,
                    )
                    if score < 0.84:
                        continue
                    project = self._official_project(hit, "官方搜索", score, project_type)
                    if best is None or score > best[0]:
                        best = (score, project)
                if best and best[0] >= 0.97:
                    break

        if best and best[0] >= 0.97:
            return best[1]

        for slug in self._candidate_slugs(meta, modrinth_project):
            data = self._cfwidget_lookup(slug, project_type)
            if not data:
                continue
            title = str(data.get("title") or "")
            slug_value = self._slug_from_url(str((data.get("urls") or {}).get("curseforge") or "")) or slug
            score = self._score_project(title, slug_value, meta)
            if score < 0.84:
                continue
            page_url = str((data.get("urls") or {}).get("curseforge") or "")
            project = Project(
                platform="curseforge",
                project_id=str(data.get("id") or ""),
                slug=slug_value,
                title=title or slug_value,
                url=page_url or PROJECT_URL.get(project_type, PROJECT_URL["mod"]).format(slug_value),
                description=str(data.get("summary") or ""),
                icon_url=str(data.get("thumbnail") or ""),
                match_method="名称匹配",
                confidence=score,
                extra={
                    "downloads": (data.get("downloads") or {}).get("total", 0),
                    "project_type": project_type,
                },
            )
            if best is None or score > best[0]:
                best = (score, project)
            if score >= 0.97:
                break
        return best[1] if best else None

    def _queries(self, meta: ModMeta) -> list[str]:
        queries = [meta.name, meta.mod_id.replace("-", " ").replace("_", " ")]
        return [q for q in dict.fromkeys(queries) if q and len(q.strip()) >= 2][:3]

    def _candidate_slugs(
        self, meta: ModMeta, modrinth_project: Project | None
    ) -> list[str]:
        candidates = [
            meta.mod_id,
            meta.mod_id.replace("_", "-"),
            modrinth_project.slug if modrinth_project else "",
            slugify(meta.name),
            slugify(meta.name).replace("-mod", ""),
        ]
        cf_id = str(meta.extra.get("curseforge_id") or "")
        if cf_id:
            candidates.insert(0, cf_id)
        result = []
        for item in candidates:
            item = (item or "").strip()
            if item and item not in result and len(item) >= 2:
                result.append(item)
        return result[:6]

    def _official_search(
        self, query: str, project_type: str = "mod"
    ) -> list[dict[str, Any]]:
        if not self.has_key:
            return []
        params = urllib.parse.urlencode(
            {
                "gameId": GAME_ID,
                "classId": CLASS_ID.get(project_type, CLASS_ID_MODS),
                "searchFilter": query,
                "pageSize": 10,
                "sortField": 2,
                "sortOrder": "desc",
            }
        )
        try:
            data = self.client.get_json(
                f"{OFFICIAL_BASE}/mods/search?{params}",
                ttl=6 * 3600,
                headers={"x-api-key": self.api_key},
            )
        except HttpError as exc:
            if exc.status in (401, 403):
                LOGGER.warning("CurseForge API Key 无效，改用公共数据源")
                self.official_disabled = True
                return []
            LOGGER.warning("CurseForge 官方搜索失败：%s", exc)
            return []
        except Exception as exc:
            LOGGER.warning("CurseForge 官方搜索失败：%s", exc)
            return []
        items = data.get("data") if isinstance(data, dict) else None
        return [x for x in (items or []) if isinstance(x, dict)]

    def _official_project(
        self,
        data: dict[str, Any],
        method: str,
        confidence: float,
        project_type: str = "mod",
    ) -> Project:
        slug = str(data.get("slug") or "")
        links = data.get("links") if isinstance(data.get("links"), dict) else {}
        logo = data.get("logo") if isinstance(data.get("logo"), dict) else {}
        page_url = str(links.get("websiteUrl") or "")
        return Project(
            platform="curseforge",
            project_id=str(data.get("id") or ""),
            slug=slug,
            title=str(data.get("name") or slug),
            url=page_url
            or PROJECT_URL.get(project_type, PROJECT_URL["mod"]).format(slug),
            description=str(data.get("summary") or ""),
            icon_url=str(logo.get("thumbnailUrl") or ""),
            match_method=method,
            confidence=confidence,
            extra={
                "downloads": data.get("downloadCount", 0),
                "project_type": project_type,
            },
        )

    def _cfwidget_lookup(
        self, slug_or_id: str, project_type: str = "mod"
    ) -> dict[str, Any] | None:
        path = quote_url_path(slug_or_id)
        section = CFWIDGET_PATH.get(project_type, "mc-mods")
        url = f"{CFWIDGET_BASE}/minecraft/{section}/{path}"
        for attempt in range(3):
            try:
                data = self.client.get_json(
                    url, ttl=12 * 3600, retries=2, allow_202=True
                )
            except HttpError as exc:
                if exc.status == 404:
                    return None
                if exc.status == 202 and attempt < 2:
                    time.sleep(2.0)
                    continue
                LOGGER.warning("CurseForge 公共数据查询失败（%s）：%s", slug_or_id, exc)
                return None
            except Exception as exc:
                LOGGER.warning("CurseForge 公共数据查询失败（%s）：%s", slug_or_id, exc)
                return None
            if isinstance(data, dict) and data.get("id") and data.get("title"):
                return data
            if attempt < 2:
                time.sleep(2.0)
        return None

    @staticmethod
    def _slug_from_url(url: str) -> str:
        path = urllib.parse.urlparse(url).path.rstrip("/")
        return path.split("/")[-1] if path else ""

    @staticmethod
    def _score_project(title: str, slug: str, meta: ModMeta) -> float:
        title_key = norm_key(title)
        slug_key = norm_key(slug)
        mod_id = norm_key(meta.mod_id)
        name = norm_key(meta.name)
        if not (title_key or slug_key):
            return 0.0
        if mod_id and slug_key == mod_id:
            return 0.99
        if name and title_key == name:
            return 0.98
        if name and title_key and (title_key.startswith(name) or name.startswith(title_key)) and min(len(name), len(title_key)) >= 5:
            return 0.94
        if mod_id and title_key and (title_key.startswith(mod_id) or mod_id.startswith(title_key)) and min(len(mod_id), len(title_key)) >= 5:
            return 0.91
        if name and title_key:
            ratio = SequenceMatcher(None, name, title_key).ratio()
            if ratio >= 0.9:
                return ratio
        return 0.0

    # ---------------------------------------------------------------- versions
    def target_file(
        self,
        project_id: str,
        mc_version: str,
        loader: str,
        project_type: str = "mod",
    ) -> TargetFile | None:
        if self.has_key:
            result = self._official_target_file(
                project_id, mc_version, loader, project_type
            )
            if result:
                return result
        return self._cfwidget_target_file(
            project_id, mc_version, loader, project_type
        )

    def _official_target_file(
        self,
        project_id: str,
        mc_version: str,
        loader: str,
        project_type: str = "mod",
    ) -> TargetFile | None:
        if not self.has_key:
            return None
        if project_type != "mod":
            loader_types: list[int | None] = [None]
        else:
            loader_types = [MOD_LOADER_TYPE.get(loader, 0)]
            if loader == "quilt":
                loader_types.append(MOD_LOADER_TYPE["fabric"])
        for loader_type in loader_types:
            if project_type == "mod" and not loader_type:
                continue
            files: list[dict[str, Any]] = []
            for index in range(0, 4):
                params_data: dict[str, Any] = {
                    "gameVersion": mc_version,
                    "pageSize": 50,
                    "index": index * 50,
                }
                if loader_type:
                    params_data["modLoaderType"] = loader_type
                params = urllib.parse.urlencode(params_data)
                try:
                    data = self.client.get_json(
                        f"{OFFICIAL_BASE}/mods/{project_id}/files?{params}",
                        ttl=3 * 3600,
                        headers={"x-api-key": self.api_key},
                    )
                except HttpError as exc:
                    if exc.status in (401, 403):
                        self.official_disabled = True
                    LOGGER.warning("CurseForge 文件查询失败：%s", exc)
                    break
                except Exception as exc:
                    LOGGER.warning("CurseForge 文件查询失败：%s", exc)
                    break
                items = data.get("data") if isinstance(data, dict) else None
                if not items:
                    break
                files.extend(x for x in items if isinstance(x, dict))
                pagination = data.get("pagination") if isinstance(data, dict) else {}
                total = int((pagination or {}).get("totalCount") or 0)
                if len(files) >= total or len(items) < 50:
                    break
            chosen = self._pick_official(files, mc_version, loader, project_type)
            if chosen:
                return chosen
        return None

    def _pick_official(
        self,
        files: list[dict[str, Any]],
        mc_version: str,
        loader: str,
        project_type: str = "mod",
    ) -> TargetFile | None:
        candidates: list[dict[str, Any]] = []
        for item in files:
            game_versions = [str(v) for v in (item.get("gameVersions") or [])]
            if mc_version not in game_versions:
                continue
            if project_type == "mod" and not self._loader_ok(game_versions, loader):
                continue
            candidates.append(item)
        if not candidates:
            return None

        def sort_key(item: dict[str, Any]):
            game_versions = [str(v) for v in (item.get("gameVersions") or [])]
            loader_exact = (
                1
                if project_type == "mod" and LOADER_TAG.get(loader) in game_versions
                else 0
            )
            return (
                loader_exact,
                RELEASE_RANK.get(item.get("releaseType"), 0),
                str(item.get("fileDate") or ""),
            )

        chosen = max(candidates, key=sort_key)
        filename = str(chosen.get("fileName") or "")
        direct = str(chosen.get("downloadUrl") or "")
        url = direct or self.cdn_url(str(chosen.get("id") or ""), filename)
        hashes = chosen.get("hashes") if isinstance(chosen.get("hashes"), list) else []
        sha1 = ""
        for item in hashes:
            if isinstance(item, dict) and int(item.get("algo") or 0) == 1:
                sha1 = str(item.get("value") or "")
                break
        deps = []
        for dep in chosen.get("dependencies") or []:
            if isinstance(dep, dict):
                deps.append(dep)
        notes = []
        if not direct:
            notes.append("官方 API 未提供下载地址，已使用 CDN 直链")
        if project_type != "mod":
            notes.append("资源包/光影仅按 Minecraft 版本匹配")
        return TargetFile(
            platform="curseforge",
            project_id=project_id,
            file_id=str(chosen.get("id") or ""),
            name=filename,
            url=url,
            version_label=str(chosen.get("displayName") or filename),
            mc_versions=[str(v) for v in (chosen.get("gameVersions") or [])],
            loaders=(
                []
                if project_type != "mod"
                else [str(v) for v in (chosen.get("gameVersions") or [])]
            ),
            release_type=self._release_label(chosen.get("releaseType")),
            size=int(chosen.get("fileLength") or 0),
            published=str(chosen.get("fileDate") or ""),
            sha1=sha1,
            dependencies=deps,
            notes=notes,
        )

    def _cfwidget_target_file(
        self,
        project_id: str,
        mc_version: str,
        loader: str,
        project_type: str = "mod",
    ) -> TargetFile | None:
        data = self._cfwidget_lookup(str(project_id), project_type)
        if not data:
            return None
        files = data.get("files")
        if not isinstance(files, list):
            return None
        candidates: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            versions = [str(v) for v in (item.get("versions") or [])]
            if mc_version not in versions:
                continue
            if project_type == "mod" and not self._loader_ok(versions, loader):
                continue
            candidates.append(item)
        if not candidates:
            return None

        def sort_key(item: dict[str, Any]):
            versions = [str(v) for v in (item.get("versions") or [])]
            loader_exact = (
                1
                if project_type == "mod" and LOADER_TAG.get(loader) in versions
                else 0
            )
            return (
                loader_exact,
                RELEASE_RANK.get(str(item.get("type") or "").lower(), 0),
                str(item.get("uploaded_at") or ""),
            )

        chosen = max(candidates, key=sort_key)
        file_id = str(chosen.get("id") or "")
        filename = str(chosen.get("name") or "")
        if not file_id or not filename:
            return None
        notes = ["来自 CurseForge 公共数据源"]
        if project_type != "mod":
            notes.append("资源包/光影仅按 Minecraft 版本匹配")
        return TargetFile(
            platform="curseforge",
            project_id=project_id,
            file_id=file_id,
            name=filename,
            url=self.cdn_url(file_id, filename),
            version_label=str(chosen.get("display") or filename),
            mc_versions=[str(v) for v in (chosen.get("versions") or [])],
            loaders=(
                []
                if project_type != "mod"
                else [str(v) for v in (chosen.get("versions") or [])]
            ),
            release_type=str(chosen.get("type") or ""),
            size=int(chosen.get("filesize") or 0),
            published=str(chosen.get("uploaded_at") or ""),
            notes=notes,
        )

    @staticmethod
    def _loader_ok(labels: list[str], loader: str) -> bool:
        if loader not in LOADER_TAG:
            return False
        known = set(labels) & ALL_LOADER_TAGS
        if not known:
            return True
        target = LOADER_TAG.get(loader)
        if loader == "quilt" and "Fabric" in known:
            return True
        return bool(target and target in known)

    @staticmethod
    def _release_label(value: Any) -> str:
        return {1: "release", 2: "beta", 3: "alpha"}.get(value, str(value or ""))

    @staticmethod
    def cdn_url(file_id: str, filename: str) -> str:
        digits = "".join(ch for ch in str(file_id) if ch.isdigit())
        if not digits:
            return ""
        if len(digits) <= 3:
            prefix, suffix = "0", digits.zfill(3)
        else:
            prefix, suffix = digits[:-3], digits[-3:]
        return (
            "https://mediafilez.forgecdn.net/files/"
            f"{prefix}/{suffix}/{quote_url_path(filename)}"
        )


def _looks_like_loader_label(value: str) -> bool:
    return value in ALL_LOADER_TAGS
