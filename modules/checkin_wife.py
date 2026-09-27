"""签到 + 每日老婆模块。"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.core import Core
from ..core.utils import group_id_of, sender_id_of, sender_name_of

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

    def scope_id(self, event: AstrMessageEvent) -> str:
        if str(self.core.cfg.get("leaderboard_scope", "global")).lower() == "group":
            return group_id_of(event)
        return ""

    # ---------- 签到 ----------

    async def checkin(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        scope_id = self.scope_id(event)
        name = sender_name_of(event)
        today = self.core.today()

        row = await self.db.get_user(sender_id, scope_id)
        if row and row[2] == today:
            return (
                f"📅 今天已经签到过啦~\n当前累计积分：{row[0]}\n连续签到：{row[1]} 天\n明天再来吧！"
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

        await self.db.apply_checkin(sender_id, scope_id, name, gain, streak, today)
        total = (row[0] if row else 0) + gain

        mood = "🎉" if "吉" in tier["name"] else "😢"
        lines = [f"📅 今日运势：{tier['name']} {mood}"]
        if bonus > 0:
            lines.append(f"获得积分：{base}（连续签到 {streak} 天，+{bonus}）")
        else:
            lines.append(f"获得积分：{base}")
        lines.append(f"当前累计积分：{total}")
        lines.append(f"连续签到：{streak} 天")
        return "\n".join(lines)

    async def my_info(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        scope_id = self.scope_id(event)
        group_id = group_id_of(event)
        user = await self.db.get_user(sender_id, scope_id)
        wife = await self.db.get_wife(sender_id, group_id, self.core.today())

        lines = [
            f"👤 {sender_name_of(event)} 的信息",
            f"累计积分：{user[0] if user else 0}",
            f"连续签到：{user[1] if user else 0} 天",
            f"总签到：{user[3] if user else 0} 次",
        ]
        lines.append(f"今日老婆：{wife[0]}" if wife else "今日老婆：还没抽，发送 /老婆 试试~")
        return "\n".join(lines)

    async def leaderboard(self, event: AstrMessageEvent):
        rows = await self.db.leaderboard(self.scope_id(event), 10)
        if not rows:
            return "暂无排行数据，快去发送 /签到 吧~"
        scope_label = "本群" if self.scope_id(event) else "全局"
        lines = [f"🏆 {scope_label}积分排行榜 Top10"]
        for index, (sender_id, sender_name, points) in enumerate(rows):
            prefix = MEDALS[index] if index < 3 else f"{index + 1}."
            lines.append(f"{prefix} {sender_name or sender_id} — {points}")
        return "\n".join(lines)

    # ---------- 老婆图源 ----------

    def _manshuo_headers(self) -> dict:
        headers = {}
        key = self.core.cfg.str("manshuo_api_key")
        if key:
            headers["X-API-Key"] = key
        return headers

    async def _draw_kitsu(self) -> dict | None:
        headers = {"Accept": "application/vnd.api+json"}
        base = "https://kitsu.io/api/edge/characters"
        for offset in (random.randint(0, 90000), random.randint(0, 1000)):
            data = await self.http.get_json(
                f"{base}?page%5Blimit%5D=1&page%5Boffset%5D={offset}", headers=headers
            )
            items = (data.get("data") if isinstance(data, dict) else None) or []
            if not items:
                continue
            attrs = items[0].get("attributes") or {}
            image = (attrs.get("image") or {}).get("original")
            if not image:
                continue
            names = attrs.get("names") or {}
            name = str(attrs.get("canonicalName") or names.get("en") or "")
            return {"name": name, "label": "角色", "image": image, "desc": "", "source": "Kitsu"}
        return None

    async def _draw_anilist(self) -> dict | None:
        page = random.randint(1, 150)
        payload = {
            "query": "query($p:Int){Page(page:$p,perPage:1){characters(sort:FAVOURITES_DESC)"
            "{name{full native}image{large}description}}}",
            "variables": {"p": page},
        }
        data = await self.http.post_json("https://graphql.anilist.co", data=payload)
        chars = (((data or {}).get("data") or {}).get("Page") or {}).get("characters") or []
        if not chars:
            return None
        char = chars[0]
        image = (char.get("image") or {}).get("large")
        if not image:
            return None
        names = char.get("name") or {}
        if names.get("native") and names.get("full"):
            name = f"{names['full']}（{names['native']}）"
        else:
            name = str(names.get("full") or names.get("native") or "")
        desc = char.get("description") or ""
        for token in (
            "<br>",
            "<br/>",
            "<i>",
            "</i>",
            "<em>",
            "</em>",
            "__",
            "**",
            "~~",
            "~!",
            "!~",
        ):
            desc = desc.replace(token, " ")
        from ..core.utils import truncate

        return {
            "name": name,
            "label": "角色",
            "image": image,
            "desc": truncate(desc),
            "source": "AniList",
        }

    async def _draw_manshuo(self) -> dict | None:
        base = self.core.cfg.str("manshuo_base_url", "https://web.manshuo.ink").rstrip("/")
        for offset in (random.randint(0, 16000), 0):
            data = await self.http.get_json(
                f"{base}/api/img/today_wife/list?limit=1&offset={offset}",
                headers=self._manshuo_headers(),
            )
            items = (((data or {}).get("data") or {}).get("items")) or []
            if not items:
                continue
            item = items[0]
            image = (
                item.get("full_url") or item.get("download_url") or item.get("thumbnail_full_url")
            )
            if not image:
                continue
            tags = [str(t) for t in (item.get("tags") or []) if t]
            name = "、".join(tags[:5]) if tags else str(item.get("display_name") or "")
            return {
                "name": name,
                "label": "标签",
                "image": image,
                "desc": "来自漫朔图库",
                "source": "漫朔",
            }
        return None

    async def _draw_manshuo_trace(self) -> dict | None:
        result = await self._draw_manshuo()
        if not result:
            return None
        trace_base = self.core.cfg.str("trace_moe_base_url", "https://api.trace.moe/search")
        data = await self.http.get_json(
            f"{trace_base}?anilistInfo=1&url={quote(result['image'], safe='')}"
        )
        results = (data or {}).get("result") or []
        if results:
            anime = results[0].get("anilist") or {}
            title = anime.get("title") or {}
            title_name = (
                title.get("chinese")
                or title.get("romaji")
                or title.get("native")
                or title.get("english")
            )
            if title_name:
                extra = f"出自《{title_name}》"
                episode = results[0].get("episode")
                if episode:
                    extra += f" 第{episode}集"
                result["desc"] = extra
                result["source"] = "漫朔 + trace.moe"
        return result

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

    async def _draw_local(self) -> dict | None:
        files = self._local_wife_files()
        if not files:
            logger.warning(f"{SIGN} 本地图源没有找到可用图片")
            return None
        image = random.choice(files)
        use_filename = self.core.cfg.bool("local_wife_name_from_filename", True)
        return {
            "name": image.stem if use_filename else "",
            "label": "角色",
            "image": str(image.resolve()),
            "desc": "来自本地图片库",
            "source": "本地图库",
            "local": True,
        }

    async def draw_wife(self) -> dict | None:
        source = self.core.cfg.str("waifu_source", "manshuo").lower()
        try:
            if source == "anilist":
                return await self._draw_anilist()
            if source == "manshuo":
                return await self._draw_manshuo()
            if source == "manshuo_trace":
                return await self._draw_manshuo_trace()
            if source == "local":
                return await self._draw_local()
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
        if sender_name and change_count == 0:
            lines = [f"🎴 今天，你的老婆是{name}".rstrip()]
        elif prefix.startswith("🔄"):
            lines = [f"{prefix}：今天，你的老婆是{name}".rstrip()]
        else:
            lines = [f"今天，你的老婆是{name}".rstrip()]
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
        group_id = group_id_of(event)
        today = self.core.today()
        existing = await self.db.get_wife(sender_id, group_id, today)
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
        result = await self.draw_wife()
        if not result:
            return None
        await self.db.set_wife(
            sender_id,
            group_id,
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
        scope_id = self.scope_id(event)
        group_id = group_id_of(event)
        today = self.core.today()
        cost = self.core.cfg.int("change_wife_cost", 30)
        limit = self.core.cfg.int("change_wife_limit", 2)

        existing = await self.db.get_wife(sender_id, group_id, today)
        if not existing:
            return "你还没有今天的老婆，先发送 /老婆 抽一个吧~"

        change_count = int(existing[4])
        if limit > 0 and change_count >= limit:
            return f"今日换老婆次数已用完（上限 {limit} 次），明天再来吧~"

        user = await self.db.get_user(sender_id, scope_id)
        points = user[0] if user else 0
        if cost > 0 and points < cost:
            return f"积分不足，换老婆需要 {cost} 积分，你当前只有 {points} 积分。"

        result = await self.draw_wife()
        if not result:
            return "老婆召唤失败，请稍后再试~"

        if cost > 0:
            await self.db.add_points(sender_id, scope_id, -cost, sender_name_of(event))
        new_count = change_count + 1
        await self.db.set_wife(
            sender_id,
            group_id,
            today,
            result.get("name", ""),
            result.get("image", ""),
            result.get("source", ""),
            json.dumps(result, ensure_ascii=False),
            new_count,
        )
        prefix = f"🔄 换老婆成功（消耗 {cost} 积分）" if cost > 0 else "🔄 换老婆成功"
        return self.render_wife(result, prefix=prefix, change_count=new_count)
