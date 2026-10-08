"""无需 AstrBot 实例，验证 QQ payload、按钮归属、重放和业务路由。"""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "allinone_button_tests"


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    pass


class Filter:
    PlatformAdapterType = types.SimpleNamespace(AIOCQHTTP=1)
    EventMessageType = types.SimpleNamespace(ALL=1)

    def __getattr__(self, name):
        return lambda *args, **kwargs: lambda function: function


def load_modules():
    stubs = {}
    for name in (
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.star",
        "astrbot.api.message_components",
        PACKAGE,
        f"{PACKAGE}.core",
        f"{PACKAGE}.modules",
        f"{PACKAGE}.core.core",
    ):
        module = types.ModuleType(name)
        module.__path__ = []
        stubs[name] = module
    stubs["astrbot.api"].logger = types.SimpleNamespace(warning=lambda *args: None)
    stubs["astrbot.api"].AstrBotConfig = object
    stubs["astrbot.api.event"].AstrMessageEvent = object
    stubs["astrbot.api.event"].filter = Filter()
    stubs["astrbot.api.star"].Star = object
    stubs["astrbot.api.star"].Context = object
    stubs["astrbot.api.message_components"].Plain = Plain
    stubs["astrbot.api.message_components"].Image = Image
    stubs[f"{PACKAGE}.core.core"].Core = object
    loaded = {}
    with patch.dict(sys.modules, stubs):
        for path in (
            "core/config.py",
            "core/utils.py",
            "core/messages.py",
            "core/buttons.py",
            "modules/checkin_wife.py",
            "modules/group_admin.py",
            "modules/music.py",
            "main.py",
        ):
            name = f"{PACKAGE}." + path[:-3].replace("/", ".")
            spec = importlib.util.spec_from_file_location(name, ROOT / path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[path] = module
    return loaded


MODULES = load_modules()
Config = MODULES["core/config.py"].Config
QQButtons = MODULES["core/buttons.py"].QQButtons
ButtonAction = MODULES["core/buttons.py"].ButtonAction
messages = MODULES["core/messages.py"]
Plugin = MODULES["main.py"].AllInOnePlugin


class Event:
    def __init__(self, platform="qq_official", owner="user1", origin="group1", c2c=False):
        self.platform = platform
        self.owner = owner
        self.unified_msg_origin = origin
        raw = (
            types.SimpleNamespace(author=types.SimpleNamespace(user_openid=owner))
            if c2c
            else types.SimpleNamespace(group_openid=origin)
        )
        self.message_obj = types.SimpleNamespace(raw_message=raw, message_id="message1")
        self.bot = types.SimpleNamespace(
            api=types.SimpleNamespace(post_group_message=AsyncMock(return_value={"id": "sent1"}))
        )
        self.post_c2c_message = AsyncMock(return_value={"id": "sent1"})
        self.sent = []
        self.default_result = types.SimpleNamespace(use_markdown_=False)
        self.stopped = False

    def get_platform_name(self):
        return self.platform

    def get_sender_id(self):
        return self.owner

    def plain_result(self, text):
        return self.chain_result([Plain(text)])

    def chain_result(self, chain):
        return types.SimpleNamespace(chain=chain, use_markdown_=None, use_t2i_=None)

    def stop_event(self):
        self.stopped = True

    async def send(self, result):
        self.sent.append(result)


class QQButtonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = types.SimpleNamespace(cfg=Config({}), today=lambda: "2026-10-08")
        self.buttons = QQButtons(self.core)
        self.event = Event()

    async def test_group_payload_and_chat_isolation(self):
        text = messages.card("📅 签到成功 · 大吉", "**本次获得：+1800 积分**")
        await self.buttons.send(self.event, messages.markdown_result(self.event, text))
        payload = self.event.bot.api.post_group_message.call_args.kwargs
        self.assertEqual(payload["group_openid"], "group1")
        self.assertEqual(payload["markdown"], {"content": text})
        self.assertEqual(payload["msg_type"], 2)
        self.assertEqual(payload["msg_id"], "message1")
        rows = payload["keyboard"]["content"]["rows"]
        self.assertEqual(
            [b["render_data"]["label"] for r in rows for b in r["buttons"]],
            ["查看排行榜", "我的资料", "抽老婆"],
        )
        self.assertFalse(self.event.default_result.use_markdown_)
        self.assertEqual(self.event.sent, [])

    async def test_webhook_c2c(self):
        event = Event(platform="qq_official_webhook", c2c=True)
        await self.buttons.send(
            event, messages.markdown_result(event, messages.card("👤 我的资料"))
        )
        event.post_c2c_message.assert_awaited_once()
        self.assertEqual(event.post_c2c_message.call_args.kwargs["openid"], "user1")
        event.bot.api.post_group_message.assert_not_awaited()

    async def test_unsupported_and_disabled_use_original_result(self):
        for platform, config in (("aiocqhttp", {}), ("qq_official", {"qq_buttons_enable": False})):
            event = Event(platform=platform)
            self.core.cfg = Config(config)
            result = messages.markdown_result(event, messages.card("📅 今天已签到"))
            await self.buttons.send(event, result)
            self.assertIs(event.sent[0], result)
            event.bot.api.post_group_message.assert_not_awaited()
        self.assertEqual(self.buttons.tickets, {})

    async def test_media_and_failed_keyboard_do_not_duplicate_image(self):
        image = Image()
        text = messages.card("💖 今日老婆", "**角色**")
        result = messages.markdown_chain_result(self.event, [image, Plain(text)])
        self.event.bot.api.post_group_message.side_effect = RuntimeError("not allowed")
        await self.buttons.send(self.event, result)
        self.assertEqual(len(self.event.sent), 2)
        self.assertEqual(self.event.sent[0].chain, [image])
        self.assertEqual(self.event.sent[1].chain[0].text, text)
        self.assertEqual(self.buttons.tickets, {})

    async def test_empty_api_response_falls_back(self):
        self.event.bot.api.post_group_message.return_value = None
        text = messages.card("🏆 积分排行榜")
        await self.buttons.send(self.event, messages.markdown_result(self.event, text))
        self.assertEqual(self.event.sent[0].chain[0].text, text)
        self.assertEqual(self.buttons.tickets, {})

    async def test_owner_origin_and_replay(self):
        _, tokens = self.buttons.keyboard(self.event, [ButtonAction("换老婆", "change_wife")])
        token = tokens[0]
        self.assertIsNone(self.buttons.consume(Event(owner="other"), token)[0])
        self.assertIsNone(self.buttons.consume(Event(origin="other"), token)[0])
        self.assertIn(token, self.buttons.tickets)
        self.assertEqual(self.buttons.consume(self.event, token)[0].action, "change_wife")
        self.assertIsNone(self.buttons.consume(self.event, token)[0])

    async def test_expiry_date_and_unknown_token(self):
        with patch.object(MODULES["core/buttons.py"].time, "time", return_value=100):
            _, tokens = self.buttons.keyboard(self.event, [ButtonAction("签到", "checkin")])
        with patch.object(MODULES["core/buttons.py"].time, "time", return_value=701):
            self.assertIsNone(self.buttons.consume(self.event, tokens[0])[0])
        _, tokens = self.buttons.keyboard(self.event, [ButtonAction("签到", "checkin")])
        self.core.today = lambda: "2026-10-09"
        self.assertIsNone(self.buttons.consume(self.event, tokens[0])[0])
        self.assertIsNone(self.buttons.consume(self.event, "forged")[0])

    async def test_action_availability_and_cost(self):
        actions = self.buttons.actions_for(messages.card("💖 今日老婆"))
        self.assertEqual(actions[0].label, "换老婆 · 60 积分")
        actions = self.buttons.actions_for(messages.card("💖 今日老婆", "次数已用完"))
        self.assertNotIn("change_wife", [a.action for a in actions])
        self.assertEqual(
            self.buttons.actions_for(messages.card("💖 还未抽取今日老婆"))[0].action, "wife"
        )
        self.core.cfg = Config({"wife_enable": False})
        self.assertNotIn(
            "wife", [a.action for a in self.buttons.actions_for(messages.card("📅 今天已签到"))]
        )

    async def test_callback_works_with_commands_disabled_and_only_charges_once(self):
        plugin = object.__new__(Plugin)
        plugin.core, plugin.buttons = self.core, self.buttons
        plugin._button_action_lock = asyncio.Lock()
        plugin.checkin_wife = types.SimpleNamespace(
            change_wife=AsyncMock(return_value=messages.card("🔄 换老婆成功", "本次消耗：60 积分"))
        )
        _, tokens = self.buttons.keyboard(
            self.event, [ButtonAction("换老婆", "change_wife", {"cost": 60})]
        )
        await asyncio.gather(*(plugin.cmd_button_action(self.event, tokens[0]) for _ in range(2)))
        plugin.checkin_wife.change_wife.assert_awaited_once()
        self.assertTrue(self.event.stopped)
        self.assertFalse(plugin.commands_on())

    async def test_price_change_does_not_charge_from_stale_button(self):
        plugin = object.__new__(Plugin)
        plugin.core, plugin.buttons = self.core, self.buttons
        plugin._button_action_lock = asyncio.Lock()
        plugin.checkin_wife = types.SimpleNamespace(change_wife=AsyncMock())
        _, tokens = self.buttons.keyboard(
            self.event, [ButtonAction("换老婆 · 60 积分", "change_wife", {"cost": 60})]
        )
        self.core.cfg = Config({"change_wife_cost": 100})
        await plugin.cmd_button_action(self.event, tokens[0])
        plugin.checkin_wife.change_wife.assert_not_awaited()
        payload = self.event.bot.api.post_group_message.call_args.kwargs
        self.assertIn("费用已更新", payload["markdown"]["content"])


if __name__ == "__main__":
    unittest.main()
