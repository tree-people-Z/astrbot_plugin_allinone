"""共享核心容器：配置 / 数据库 / 网络 / 图片渲染。"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .db import Database
from .net import Http
from .render import text_to_image
from .utils import today_str


class Core:
    def __init__(self, context=None, config: Config | None = None):
        self.context = context
        self.cfg = config if isinstance(config, Config) else Config({})
        self.data_dir = self._resolve_data_dir()
        self.db = Database(self.data_dir / "allinone.db")
        self.http = Http(self.cfg.float("request_timeout", 15), self.cfg.str("http_proxy"))

    def _resolve_data_dir(self) -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_allinone"
        except Exception:
            base = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_allinone"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def today(self) -> str:
        return today_str(self.cfg.float("timezone", 8))

    async def start(self):
        await self.db.connect()
        await self.http.start()

    async def stop(self):
        await self.db.close()
        await self.http.close()

    async def render_text(self, text: str) -> str | None:
        return text_to_image(text)
