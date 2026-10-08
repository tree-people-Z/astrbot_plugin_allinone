"""插件消息统一使用 Markdown，媒体仍以原生消息组件发送。"""

import re

from astrbot.api.event import AstrMessageEvent


def escape_markdown(value: object) -> str:
    """将昵称、角色名等外部文本作为一行展示，避免改变消息结构。"""
    text = " ".join(str(value).splitlines())
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def card(title: str, *blocks: str) -> str:
    return "\n\n".join([f"### {title}", *(block for block in blocks if block)])


def action_hint(config, natural: str, command: str = "") -> str:
    hint = f"说“{natural}”即可。"
    if command and config.bool("command_enable", False):
        hint += f" 也可发送 `{command}`。"
    return hint


def lyrics_card(name: str, lyric: str) -> str:
    # 歌词逐行引用，保留换行且不把歌词中的符号当作 Markdown 结构。
    body = "\n".join(f"> {escape_markdown(line)}" for line in lyric.splitlines())
    return card("📃 歌词", f"**{escape_markdown(name)}**", body or "暂时没有找到歌词。")


def format_markdown(text: str) -> str:
    """保留功能自己的排版；旧模块的普通文案使用统一结果框。"""
    text = str(text).strip()
    if text.startswith("### "):
        return text
    body = "  \n".join(escape_markdown(line) for line in text.splitlines())
    return card("📋 操作结果", body)


def markdown_result(event: AstrMessageEvent, text: str):
    return markdown_chain_result(event, event.plain_result(format_markdown(text)).chain)


def markdown_chain_result(event: AstrMessageEvent, chain: list):
    # 只设置新建的插件结果，不修改 event 的默认结果、LLM 输出或全局配置。
    result = event.chain_result(chain)
    # 兼容尚未提供 use_markdown() 的 AstrBot 版本。
    result.use_markdown_ = True
    result.use_t2i_ = False
    return result
