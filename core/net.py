"""aiohttp 网络封装。"""

from __future__ import annotations

import asyncio
import json

import aiohttp
from astrbot.api import logger


class Http:
    def __init__(self, timeout: float = 15.0, proxy: str = ""):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._proxy = (proxy or "").strip()
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    def _kwargs(self, headers: dict | None) -> dict:
        kwargs: dict = {}
        if headers:
            kwargs["headers"] = headers
        if self._proxy:
            kwargs["proxy"] = self._proxy
        return kwargs

    async def _do(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        if self._session is None:
            await self.start()
        assert self._session is not None
        headers = kwargs.pop("headers", None)
        kwargs.update(self._kwargs(headers))
        return await self._session.request(method, url, **kwargs)

    async def get_text(self, url: str, headers: dict | None = None) -> str:
        try:
            async with await self._do("GET", url, headers=headers) as resp:
                if resp.status >= 400:
                    logger.warning(f"[allinone] GET {url} -> HTTP {resp.status}")
                    return ""
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"[allinone] GET 失败 {url}: {exc}")
            return ""

    async def get_json(self, url: str, headers: dict | None = None):
        text = await self.get_text(url, headers=headers)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    async def post_json(
        self,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
    ):
        try:
            async with await self._do("POST", url, data=data, headers=headers) as resp:
                if resp.status >= 400:
                    logger.warning(f"[allinone] POST {url} -> HTTP {resp.status}")
                    return {}
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"[allinone] POST 失败 {url}: {exc}")
            return {}

    async def download(self, url: str, dest_path: str, headers: dict | None = None) -> bool:
        try:
            async with await self._do("GET", url, headers=headers) as resp:
                if resp.status >= 400:
                    return False
                with open(dest_path, "wb") as file_obj:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        file_obj.write(chunk)
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.warning(f"[allinone] 下载失败 {url}: {exc}")
            return False
