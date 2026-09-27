"""配置读取封装。"""

from __future__ import annotations

from typing import Any


class Config:
    def __init__(self, raw: dict | None = None):
        self.raw: dict = raw if isinstance(raw, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        try:
            value = self.raw.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        try:
            return bool(value)
        except Exception:
            return default

    def int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def str(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        return str(value) if value is not None else default

    def list(self, key: str, default: list | None = None) -> list:
        value = self.get(key, default if default is not None else [])
        return value if isinstance(value, list) else []
