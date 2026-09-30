"""签到 + 每日老婆模块。"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.core import Core
from ..core.utils import member_names, sender_id_of, sender_name_of

DEFAULT_TIERS = [
    {"name": "大凶", "weight": 3, "min_points": 1, "max_points": 5},
    {"name": "凶", "weight": 7, "min_points": 6, "max_points": 15},
    {"name": "小凶", "weight": 10, "min_points": 16, "max_points": 25},
    {"name": "末吉", "weight": 15, "min_points": 26, "max_points": 40},
    {"name": "小吉", "weight": 20, "min_points": 41, "max_points": 55},
    {"name": "中吉", "weight": 20, "min_points": 56, "max_points": 70},
    {"name": "吉", "weight": 15, "min_points": 71, "max_points": 85},
    {"name": "大吉", "weight": 8, "min_points": 86, "max_points": 99},
    {"name": "超大吉", "weight": 2, "min_points": 100, "max_points": 100},
]

MEDALS = ["🥇", "🥈", "🥉"]
SIGN = "[allinone]"


class CheckinWifeModule:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        self.http = core.http

    # ---------- 运势 ----------

    def tiers(self) -> list[dict]:
        raw = self.core.cfg.get("fortune_tiers", [])
        tiers: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    tiers.append(
                        {
                            "name": str(item.get("name") or "小吉"),
                            "weight": max(0, int(item.get("weight", 10))),
                            "min_points": int(item.get("min_points", 1)),
                            "max_points": int(item.get("max_points", 10)),
                        }
                    )
                except (TypeError, ValueError):
                    continue
        if not tiers:
            tiers = [dict(t) for t in DEFAULT_TIERS]
        for tier in tiers:
            if tier["min_points"] > tier["max_points"]:
                tier["min_points"], tier["max_points"] = tier["max_points"], tier["min_points"]
        return tiers

    def roll_tier(self) -> dict:
        tiers = self.tiers()
        total = sum(t["weight"] for t in tiers)
        if total <= 0:
            return random.choice(tiers)
        target = random.uniform(0, total)
        upto = 0.0
        for tier in tiers:
            upto += tier["weight"]
            if target <= upto:
                return tier
        return tiers[-1]

    # ---------- 签到 ----------

    async def checkin(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        name = sender_name_of(event)
        today = self.core.today()

        row = await self.db.get_user(sender_id, "")
        if row and row[2] == today:
            return (
                f"📅 {name}，今天已经签到过啦~\n当前累计积分：{row[0]}\n连续签到：{row[1]} 天\n明天再来吧！"
            )

        streak = 1
        if row and row[2]:
            try:
                last_date = datetime.strptime(row[2], "%Y-%m-%d").date()
                today_date = datetime.strptime(today, "%Y-%m-%d").date()
                if (today_date - last_date).days == 1:
                    streak = int(row[1]) + 1
            except ValueError:
                streak = 1

        tier = self.roll_tier()
        base = random.randint(int(tier["min_points"]), int(tier["max_points"]))
        per_day = self.core.cfg.int("streak_bonus_per_day", 5)
        cap = self.core.cfg.int("streak_bonus_cap", 50)
        bonus = min(streak * per_day, cap) if per_day > 0 else 0
        gain = base + bonus

        await self.db.apply_checkin(sender_id, "", name, gain, streak, today)
        total = (row[0] if row else 0) + gain

        mood = "🎉" if "吉" in tier["name"] else "😢"
        lines = [f"📅 {name} 今日运势：{tier['name']} {mood}"]
        if bonus > 0:
            lines.append(f"获得积分：{base}（连续签到 {streak} 天，+{bonus}）")
        else:
            lines.append(f"获得积分：{base}")
        lines.append(f"当前累计积分：{total}")
        lines.append(f"连续签到：{streak} 天")
        return "\n".join(lines)

    async def my_info(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        user = await self.db.get_user(sender_id, "")
        wife = await self.db.get_wife(sender_id, "", self.core.today())

        lines = [
            f"👤 {sender_name_of(event)} 的信息",
            f"累计积分：{user[0] if user else 0}",
            f"连续签到：{user[1] if user else 0} 天",
            f"总签到：{user[3] if user else 0} 次",
        ]
        lines.append(f"今日老婆：{wife[0]}" if wife else "今日老婆：还没抽，发送 /老婆 试试~")
        return "\n".join(lines)

    async def leaderboard(self, event: AstrMessageEvent):
        rows = await self.db.leaderboard("", 10)
        if not rows:
            return "暂无排行数据，快去发送 /签到 吧~"
        missing = [sid for sid, name, _ in rows if not name]
        names = await member_names(event, missing)
        lines = ["🏆 全局积分排行榜 Top10"]
        for index, (sender_id, sender_name, points) in enumerate(rows):
            prefix = MEDALS[index] if index < 3 else f"{index + 1}."
            lines.append(f"{prefix} {sender_name or names.get(sender_id) or sender_id} — {points}")

        me = sender_id_of(event)
        if all(str(sender_id) != str(me) for sender_id, _, _ in rows):
            rank = await self.db.user_rank("", me)
            if rank:
                lines.append("──────────")
                lines.append(f"第 {rank[0]} 名 {sender_name_of(event)} — {rank[1]}")
        return "\n".join(lines)

    # ---------- 老婆图源 ----------

    async def _draw_manshuo(self) -> dict | None:
        image_dir = self.core.data_dir / "wife_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image = image_dir / f"{uuid4().hex}.jpg"
        if not await self.http.download("https://web.manshuo.ink/api/img/today_wife", str(image)):
            image.unlink(missing_ok=True)
            return None
        return {
            "name": "",
            "image": str(image),
            "source": "漫朔",
            "local": True,
        }

    def _local_wife_files(self) -> list[Path]:
        """Expand configured local files/directories into supported image files."""
        extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        files: list[Path] = []
        for raw in self.core.cfg.list("local_wife_paths", []):
            path = Path(str(raw)).expanduser()
            if path.is_file() and path.suffix.lower() in extensions:
                files.append(path)
            elif path.is_dir():
                files.extend(
                    item
                    for item in path.rglob("*")
                    if item.is_file() and item.suffix.lower() in extensions
                )
        return files

    def _configured_roots(self) -> list[Path]:
        roots: list[Path] = []
        for raw in self.core.cfg.list("local_wife_paths", []):
            path = Path(str(raw)).expanduser()
            if path.is_dir():
                try:
                    roots.append(path.resolve())
                except OSError:
                    continue
        return roots

    @staticmethod
    def _strip_index(name: str) -> str:
        """去掉文件夹名的前导序号，如 3_安和昴 -> 安和昴 或 209-绪山真寻 -> 绪山真寻。"""
        return re.sub(r"^\s*\d+\s*[-_.、\s]*", "", str(name or "")).strip()

    @staticmethod
    def _clean_name(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        line = text.splitlines()[0].strip()
        line = line.strip("「」『』\"'“”‘’").strip()
        line = re.sub(r"^(角色名|名称|人物名)[:：\s]*", "", line).strip()
        return line[:20]

    async def _llm_extract_name(self, event: AstrMessageEvent, raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        cache_key = f"wife_name:{raw}"
        try:
            cached = await self.db.kv_get(cache_key)
        except Exception:
            cached = None
        if cached:
            return str(cached)

        context = getattr(self.core, "context", None)
        if context is None:
            return ""
        umo = getattr(event, "unified_msg_origin", None)
        provider = None
        try:
            get_async = getattr(context, "get_using_provider_async", None)
            if callable(get_async):
                provider = await get_async(umo=umo)
            else:
                provider = context.get_using_provider(umo=umo)
        except Exception as exc:
            logger.warning(f"{SIGN} 获取大模型服务失败: {exc}")
            provider = None
        if provider is None:
            return ""

        system_prompt = (
            "你是命名助手。用户会给出一个本地图片文件夹名，请从中提取角色（人物）名称。"
            "只输出角色名本身，不要序号、数字、作品名、括号、标点或任何解释。"
        )
        timeout = max(1, self.core.cfg.int("local_wife_name_llm_timeout", 20))
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=raw, system_prompt=system_prompt),
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning(f"{SIGN} 大模型提取角色名失败: {exc}")
            return ""
        name = self._clean_name(getattr(resp, "completion_text", "") or "")
        if not name:
            return ""
        try:
            await self.db.kv_set(cache_key, name)
        except Exception:
            pass
        return name

    async def _name_from_folder(self, event: AstrMessageEvent, image: Path) -> str:
        try:
            parent = image.parent.resolve()
        except OSError:
            parent = image.parent
        if parent in self._configured_roots():
            return ""
        raw = image.parent.name
        fallback = self._strip_index(raw)
        if self.core.cfg.bool("local_wife_name_llm", False):
            extracted = await self._llm_extract_name(event, raw)
            if extracted:
                return extracted
        return fallback

    async def _draw_local(self, event: AstrMessageEvent) -> dict | None:
        files = self._local_wife_files()
        if not files:
            logger.warning(f"{SIGN} 本地图源没有找到可用图片")
            return None
        image = random.choice(files)
        source = self.core.cfg.str("local_wife_name_source", "folder").lower()
        if source == "filename":
            name = image.stem
        elif source == "none":
            name = ""
        else:
            name = await self._name_from_folder(event, image)
        return {
            "name": name,
            "image": str(image.resolve()),
            "source": "本地图库",
            "local": True,
        }

    async def draw_wife(self, event: AstrMessageEvent) -> dict | None:
        source = self.core.cfg.str("waifu_source", "manshuo").lower()
        try:
            if source == "local":
                return await self._draw_local(event)
            return await self._draw_manshuo()
        except Exception as exc:
            logger.warning(f"{SIGN} 抽取老婆失败: {exc}")
            return None

    def render_wife(
        self,
        result: dict,
        sender_name: str | None = None,
        prefix: str = "🎴 今日老婆",
        change_count: int | None = None,
    ) -> list:
        cost = self.core.cfg.int("change_wife_cost", 30)
        limit = self.core.cfg.int("change_wife_limit", 2)
        name = str(result.get("name") or "").strip()
        owner = f"{sender_name}，" if sender_name else ""
        if sender_name and change_count == 0:
            lines = [f"🎴 {owner}今天，你的老婆是{name}".rstrip()]
        elif prefix.startswith("🔄"):
            lines = [f"{prefix}：{owner}今天，你的老婆是{name}".rstrip()]
        else:
            lines = [f"{owner}今天，你的老婆是{name}".rstrip()]
        if change_count is not None:
            if limit > 0:
                lines.append(f"今日已换 {change_count}/{limit} 次")
            lines.append(
                f"发送 /换老婆 重抽（消耗 {cost} 积分）"
                if cost > 0
                else "发送 /换老婆 重抽（免费）"
            )
        chain = []
        if result.get("image"):
            image = str(result["image"])
            if result.get("local") or Path(image).is_file():
                chain.append(Comp.Image.fromFileSystem(image))
            else:
                chain.append(Comp.Image.fromURL(image))
        chain.append(Comp.Plain("\n".join(lines)))
        return chain

    async def wife(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        today = self.core.today()
        existing = await self.db.get_wife(sender_id, "", today)
        if existing:
            chain = []
            if existing[1]:
                image = str(existing[1])
                if Path(image).is_file():
                    chain.append(Comp.Image.fromFileSystem(image))
                else:
                    chain.append(Comp.Image.fromURL(image))
            chain.append(
                Comp.Plain(
                    f"💞 {sender_name_of(event)} 你今天的老婆已经抽过啦~\n"
                    f"今天，你的老婆是{existing[0] or ''}\n"
                    "发送 /换老婆 可以重抽"
                ),
            )
            return chain
        result = await self.draw_wife(event)
        if not result:
            return None
        await self.db.set_wife(
            sender_id,
            "",
            today,
            result.get("name", ""),
            result.get("image", ""),
            result.get("source", ""),
            json.dumps(result, ensure_ascii=False),
            0,
        )
        return self.render_wife(result, sender_name=sender_name_of(event), change_count=0)

    async def change_wife(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        name = sender_name_of(event)
        today = self.core.today()
        cost = self.core.cfg.int("change_wife_cost", 30)
        limit = self.core.cfg.int("change_wife_limit", 2)

        existing = await self.db.get_wife(sender_id, "", today)
        if not existing:
            return f"{name}，你还没有今天的老婆，先发送 /老婆 抽一个吧~"

        change_count = int(existing[4])
        if limit > 0 and change_count >= limit:
            return f"{name}，今日换老婆次数已用完（上限 {limit} 次），明天再来吧~"

        user = await self.db.get_user(sender_id, "")
        points = user[0] if user else 0
        if cost > 0 and points < cost:
            return f"{name}，积分不足，换老婆需要 {cost} 积分，你当前只有 {points} 积分。"

        result = await self.draw_wife(event)
        if not result:
            return f"{name}，老婆召唤失败，请稍后再试~"

        if cost > 0:
            await self.db.add_points(sender_id, "", -cost, name)
        new_count = change_count + 1
        await self.db.set_wife(
            sender_id,
            "",
            today,
            result.get("name", ""),
            result.get("image", ""),
            result.get("source", ""),
            json.dumps(result, ensure_ascii=False),
            new_count,
        )
        prefix = f"🔄 换老婆成功（消耗 {cost} 积分）" if cost > 0 else "🔄 换老婆成功"
        return self.render_wife(result, sender_name=name, prefix=prefix, change_count=new_count)
