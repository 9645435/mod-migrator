from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..util import cache_dir


USER_AGENT = "ModMigrator/1.0.0 (Minecraft Java mod migration desktop app)"
LOGGER = logging.getLogger("modmigrator.http")


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, url: str = "", body: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body


class DownloadError(Exception):
    pass


class CancelledError(Exception):
    pass


ProgressCallback = Callable[[int, int], None]


class HttpClient:
    def __init__(self, cache_ttl: int = 6 * 3600, timeout: float = 30.0):
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self.cache_root = cache_dir()
        self._memory: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ cache
    def _cache_key(self, method: str, url: str, body: bytes | None) -> str:
        digest = hashlib.sha256()
        digest.update(method.encode())
        digest.update(b"\0")
        digest.update(url.encode())
        digest.update(b"\0")
        if body:
            digest.update(body)
        return digest.hexdigest()

    def _read_cache(self, key: str, ttl: int) -> Any | None:
        now = time.time()
        memory = self._memory.get(key)
        if memory and memory[0] > now:
            return memory[1]
        path = self.cache_root / f"{key}.json"
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if float(payload.get("expires", 0)) > now:
                data = payload.get("data")
                self._memory[key] = (float(payload["expires"]), data)
                return data
        except (OSError, ValueError):
            pass
        return None

    def _write_cache(self, key: str, data: Any, ttl: int) -> None:
        expires = time.time() + ttl
        self._memory[key] = (expires, data)
        path = self.cache_root / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump({"expires": expires, "data": data}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def clear_cache(self) -> int:
        self._memory.clear()
        removed = 0
        for path in self.cache_root.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    # ------------------------------------------------------------------ JSON
    def get_json(
        self,
        url: str,
        ttl: int | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
        allow_202: bool = False,
    ) -> Any:
        return self.request_json(
            "GET", url, None, ttl=ttl, headers=headers, retries=retries, allow_202=allow_202
        )

    def post_json(
        self,
        url: str,
        payload: Any,
        ttl: int | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.request_json(
            "POST", url, body, ttl=ttl, headers=headers, retries=retries
        )

    def request_json(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        ttl: int | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
        allow_202: bool = False,
    ) -> Any:
        ttl = self.cache_ttl if ttl is None else ttl
        key = self._cache_key(method, url, body)
        if ttl > 0:
            cached = self._read_cache(key, ttl)
            if cached is not None:
                return cached

        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if headers:
            request_headers.update(headers)
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")

        last_error: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                req = urllib.request.Request(
                    url, data=body, headers=request_headers, method=method
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    data = json.loads(raw.decode("utf-8"))
                if ttl > 0:
                    self._write_cache(key, data, ttl)
                return data
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    raw = exc.read()
                    if exc.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    body_text = raw.decode("utf-8", "replace")[:1000]
                except Exception:
                    body_text = ""
                if status == 202 and allow_202:
                    try:
                        return json.loads(body_text) if body_text else {}
                    except ValueError:
                        return {}
                if status in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                    delay = self._retry_delay(exc, attempt)
                    LOGGER.warning("HTTP %s，%0.1f 秒后重试：%s", status, delay, url)
                    time.sleep(delay)
                    last_error = HttpError(
                        self._friendly_status(status), status, url, body_text
                    )
                    continue
                raise HttpError(self._friendly_status(status), status, url, body_text)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    delay = 1.0 * (attempt + 1) + random.random() * 0.4
                    LOGGER.warning("网络错误，%0.1f 秒后重试：%s（%s）", delay, url, exc)
                    time.sleep(delay)
                    continue
                raise HttpError(f"网络连接失败：{exc}", None, url) from exc
            except ValueError as exc:
                raise HttpError("服务器返回了无法解析的数据", None, url, str(exc)) from exc
        raise HttpError(f"请求失败：{last_error}", None, url)

    @staticmethod
    def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                return min(30.0, max(0.5, float(header)))
            except ValueError:
                pass
        return min(15.0, 1.5 * (2**attempt) + random.random() * 0.5)

    @staticmethod
    def _friendly_status(status: int) -> str:
        messages = {
            400: "请求参数有误",
            401: "API Key 无效或缺失",
            403: "访问被拒绝（可能未配置 CurseForge API Key）",
            404: "未找到请求的资源",
            405: "请求方式不被允许",
            429: "请求过于频繁，请稍后再试",
            500: "服务器内部错误",
            502: "服务器网关错误",
            503: "服务器暂时不可用",
            504: "服务器响应超时",
        }
        return messages.get(status, f"HTTP 请求失败（{status}）")


def download_file(
    url: str,
    destination: Path,
    expected_size: int = 0,
    sha1: str = "",
    progress: ProgressCallback | None = None,
    cancel_event=None,
    timeout: float = 90.0,
) -> int:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    digest = hashlib.sha1()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if expected_size and length and length != expected_size:
                raise DownloadError(
                    f"服务器文件大小异常（{length} 字节，预期 {expected_size} 字节）"
                )
            with temp.open("wb") as fh:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError("下载已取消")
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    total += len(chunk)
                    if progress:
                        progress(total, length or expected_size)
        if expected_size and total != expected_size:
            raise DownloadError(f"文件大小不匹配（{total} 字节，预期 {expected_size} 字节）")
        if sha1 and digest.hexdigest().lower() != sha1.lower():
            raise DownloadError("文件校验失败（SHA1 不匹配）")
        os.replace(temp, destination)
        return total
    except urllib.error.HTTPError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        if exc.code == 403:
            raise DownloadError("下载被拒绝（403）。该文件可能未开放第三方下载。") from exc
        if exc.code == 404:
            raise DownloadError("下载地址不存在（404），文件可能已被作者删除。") from exc
        raise DownloadError(f"下载失败：HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, CancelledError):
            raise
        raise DownloadError(f"下载失败：{exc}") from exc
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def quote_url_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")
