"""无需 AstrBot 实例，验证 QQ payload、按钮归属、重放和业务路由。"""

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

    async def test_playlist_markdown_and_chat_isolation(self):
        text = messages.card("🎵 我的歌单")
        await self.buttons.send(self.event, messages.markdown_result(self.event, text))
        payload = self.event.bot.api.post_group_message.call_args.kwargs
        self.assertEqual(payload["markdown"], {"content": text})
        self.assertFalse(self.event.default_result.use_markdown_)
        self.assertIn(
            "/aio_action ",
            payload["keyboard"]["content"]["rows"][0]["buttons"][0]["action"]["data"],
        )

    async def test_wife_actions_are_no_longer_owned(self):
        self.assertEqual(self.buttons.actions_for(messages.card("今日老婆")), [])
        self.assertFalse(hasattr(Plugin, "cmd_checkin"))
        self.assertFalse(hasattr(Plugin, "tool_draw_wife"))

    async def test_playlist_button_route_and_replay(self):
        plugin = object.__new__(Plugin)
        plugin.core, plugin.buttons = self.core, self.buttons
        plugin.music = types.SimpleNamespace(
            show_playlist=AsyncMock(return_value=messages.card("🎵 我的歌单"))
        )
        _, tokens = self.buttons.keyboard(self.event, [ButtonAction("我的歌单", "playlist")])
        await plugin.cmd_button_action(self.event, tokens[0])
        await plugin.cmd_button_action(self.event, tokens[0])
        plugin.music.show_playlist.assert_awaited_once()
        self.assertTrue(self.event.stopped)

    async def test_other_owner_cannot_consume(self):
        _, tokens = self.buttons.keyboard(self.event, [ButtonAction("我的歌单", "playlist")])
        self.assertIsNone(self.buttons.consume(Event(owner="other"), tokens[0])[0])
        self.assertIsNotNone(self.buttons.consume(self.event, tokens[0])[0])

    async def test_failed_keyboard_falls_back(self):
        self.event.bot.api.post_group_message.side_effect = RuntimeError("not allowed")
        text = messages.card("🎵 我的歌单")
        await self.buttons.send(self.event, messages.markdown_result(self.event, text))
        self.assertEqual(self.event.sent[0].chain[0].text, text)
        self.assertEqual(self.buttons.tickets, {})
