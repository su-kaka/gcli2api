from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from log import log

_MEDIA_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _safe_media_key(media_key: str) -> Optional[str]:
    if not media_key or not isinstance(media_key, str):
        return None
    if "/" in media_key or "\\" in media_key or ".." in media_key:
        return None
    if not _MEDIA_KEY_RE.match(media_key):
        return None
    return media_key


def _now_ts() -> int:
    return int(time.time())


@dataclass(frozen=True)
class SignedMediaUrl:
    url: str
    expires: int
    sig: str


class AnthropicMediaStore:
    """
    Anthropic 图片临时存储与签名 URL 工具类。

    目标：
    - 把下游 inlineData 的 base64 图片落地为短期可访问文件
    - 生成/校验短期签名 URL（HMAC-SHA256）
    - 定期清理过期文件与控制目录规模
    """

    def __init__(
        self,
        *,
        media_dir: str,
        ttl_seconds: int,
        signing_secret: str,
        max_bytes: int,
        max_files: int,
        cleanup_interval_seconds: int,
    ):
        self.media_dir = Path(media_dir)
        self.ttl_seconds = max(30, int(ttl_seconds))
        self.signing_secret = str(signing_secret or "").encode("utf-8")
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.max_files = max(10, int(max_files))
        self.cleanup_interval_seconds = max(5, int(cleanup_interval_seconds))

        self._last_cleanup_ts = 0

    def save_inline_data(self, *, mime_type: str, base64_data: str) -> str:
        """
        保存 base64 图片为本地文件并返回 media_key。
        """
        mt = str(mime_type or "").strip().lower()
        ext = _EXT_BY_MIME.get(mt, "png")
        media_key = f"{uuid.uuid4().hex}.{ext}"

        self.media_dir.mkdir(parents=True, exist_ok=True)

        try:
            raw = base64.b64decode(base64_data or "", validate=False)
        except Exception:
            raise ValueError("图片 base64 解码失败")

        if not raw:
            raise ValueError("图片数据为空")

        if len(raw) > self.max_bytes:
            raise ValueError(f"图片过大：{len(raw)} bytes，超过上限 {self.max_bytes} bytes")

        path = self._resolve_media_path(media_key)
        if path is None:
            raise ValueError("生成的 media_key 不安全")

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(raw)
        os.replace(tmp_path, path)
        os.utime(path, (time.time(), time.time()))

        self.maybe_cleanup()
        return media_key

    def build_signed_url(self, *, base_url: str, media_key: str, ttl_seconds: Optional[int] = None) -> SignedMediaUrl:
        """
        生成短期签名 URL（不包含任何敏感信息）。
        """
        key = _safe_media_key(media_key)
        if not key:
            raise ValueError("media_key 不合法")

        ttl = self.ttl_seconds if ttl_seconds is None else max(30, int(ttl_seconds))
        expires = _now_ts() + ttl
        sig = self.sign(media_key=key, expires=expires)

        base = str(base_url or "").rstrip("/")
        url = f"{base}/antigravity/v1/media/{key}?expires={expires}&sig={sig}"
        return SignedMediaUrl(url=url, expires=expires, sig=sig)

    def sign(self, *, media_key: str, expires: int) -> str:
        payload = f"{media_key}|{int(expires)}".encode("utf-8")
        return hmac.new(self.signing_secret, payload, hashlib.sha256).hexdigest()

    def validate(self, *, media_key: str, expires: int, sig: str) -> bool:
        key = _safe_media_key(media_key)
        if not key:
            return False
        try:
            exp = int(expires)
        except Exception:
            return False
        if exp < _now_ts():
            return False
        expected = self.sign(media_key=key, expires=exp)
        return hmac.compare_digest(str(sig or ""), expected)

    def get_media_file(self, *, media_key: str) -> Optional[Tuple[Path, str]]:
        """
        获取媒体文件路径与 Content-Type。
        """
        key = _safe_media_key(media_key)
        if not key:
            return None

        path = self._resolve_media_path(key)
        if path is None or not path.exists() or not path.is_file():
            return None

        ext = path.suffix.lstrip(".").lower()
        content_type = _MIME_BY_EXT.get(ext, "application/octet-stream")
        return path, content_type

    def maybe_cleanup(self) -> None:
        """
        按最小间隔触发清理，避免每次保存都全量扫描目录。
        """
        now = _now_ts()
        if now - self._last_cleanup_ts < self.cleanup_interval_seconds:
            return
        self._last_cleanup_ts = now
        try:
            self.cleanup()
        except Exception as e:
            log.debug(f"[ANTHROPIC][MEDIA] 清理失败: {e}")

    def cleanup(self) -> None:
        """
        清理过期文件，并在必要时裁剪到 max_files。
        """
        if not self.media_dir.exists():
            return

        now = time.time()
        expire_before = now - float(self.ttl_seconds)

        files = []
        for entry in os.scandir(self.media_dir):
            if not entry.is_file():
                continue
            name = entry.name
            if not _safe_media_key(name):
                continue
            try:
                st = entry.stat()
            except FileNotFoundError:
                continue
            # 先按 mtime 清理过期
            if st.st_mtime < expire_before:
                try:
                    os.remove(entry.path)
                except FileNotFoundError:
                    pass
                continue
            files.append((st.st_mtime, entry.path))

        if len(files) <= self.max_files:
            return

        files.sort(key=lambda x: x[0])  # 最旧在前
        to_remove = files[: max(0, len(files) - self.max_files)]
        for _, path in to_remove:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _resolve_media_path(self, media_key: str) -> Optional[Path]:
        key = _safe_media_key(media_key)
        if not key:
            return None
        path = (self.media_dir / key).resolve()
        base = self.media_dir.resolve()
        try:
            path.relative_to(base)
        except Exception:
            return None
        return path


_store: Optional[AnthropicMediaStore] = None
_store_key: Optional[tuple] = None


async def get_anthropic_media_store() -> AnthropicMediaStore:
    """
    获取全局媒体存储实例（按配置变化自动重建）。
    """
    global _store, _store_key

    from config import (
        get_anthropic_media_cleanup_interval_seconds,
        get_anthropic_media_dir,
        get_anthropic_media_max_bytes,
        get_anthropic_media_max_files,
        get_anthropic_media_signing_secret,
        get_anthropic_media_ttl_seconds,
    )

    media_dir = await get_anthropic_media_dir()
    ttl_seconds = await get_anthropic_media_ttl_seconds()
    signing_secret = await get_anthropic_media_signing_secret()
    max_bytes = await get_anthropic_media_max_bytes()
    max_files = await get_anthropic_media_max_files()
    cleanup_interval_seconds = await get_anthropic_media_cleanup_interval_seconds()

    key = (media_dir, ttl_seconds, signing_secret, max_bytes, max_files, cleanup_interval_seconds)
    if _store is None or _store_key != key:
        _store = AnthropicMediaStore(
            media_dir=media_dir,
            ttl_seconds=ttl_seconds,
            signing_secret=signing_secret,
            max_bytes=max_bytes,
            max_files=max_files,
            cleanup_interval_seconds=cleanup_interval_seconds,
        )
        _store_key = key

    return _store

