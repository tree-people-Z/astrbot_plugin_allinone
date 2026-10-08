"""在 AstrBot 容器中创建/更新本插件的 QQ 官方指令面板。

默认只预览；传入 --apply 才会修改线上面板。密钥仅从现有配置读取，不输出。
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.bot.qq.com"
OWNER = "astrbot_plugin_allinone"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def panel_items(config: dict) -> list[dict]:
    entries = [
        ("music_enable", "我的歌单", "查看收藏歌曲"),
        ("music_enable", "点歌", "发送指令后补充歌名"),
    ]
    return [
        {"type": "command", "name": name, "desc": desc, "only_admin": False}
        for switch, name, desc in entries
        if config.get(switch, True)
    ]


def normalize_items(items: list[dict]) -> list[dict]:
    # QQ 会移除名称前的 /，并省略 only_admin=false；按有效语义比较。
    return [
        {
            "name": item.get("name", "").lstrip("/"),
            "desc": item.get("desc", ""),
            "type": item.get("type", "command"),
            "only_admin": bool(item.get("only_admin", False)),
        }
        for item in items
    ]


def request(method: str, path: str, payload: dict | None = None, token: str = ""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"QQBot {token}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API_BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            code = json.loads(exc.read()).get("code", "unknown")
        except (ValueError, AttributeError):
            code = "unknown"
        # 不输出请求对象、认证头或完整响应，以免泄漏凭据。
        raise RuntimeError(
            f"{method} {path.split('?')[0]}: HTTP {exc.code}, API code {code}"
        ) from None
    except urllib.error.URLError:
        raise RuntimeError(f"{method} {path.split('?')[0]}: network unavailable") from None


def list_panels(scope: str, token: str) -> list[dict]:
    records, cursor, seen = [], "", set()
    while True:
        query = urllib.parse.urlencode({"scope": scope, "limit": 50, "cursor": cursor})
        page = request("GET", f"/v2/panels?{query}", token=token)
        if not isinstance(page, dict) or ("records" not in page and page.get("is_end") is not True):
            raise RuntimeError(f"Invalid panel list response for {scope}")
        # QQ 的空列表响应可能仅返回 is_end=true，省略 records。
        records.extend(page.get("records", []))
        cursor = page.get("next_cursor", "")
        if page.get("is_end") or not cursor:
            return records
        if cursor in seen:
            raise RuntimeError("Repeated panel pagination cursor")
        seen.add(cursor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("data/cmd_config.json"))
    parser.add_argument(
        "--plugin-config",
        type=Path,
        default=Path("data/config/astrbot_plugin_allinone_config.json"),
    )
    parser.add_argument("--platform-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config)
    platforms = [
        p
        for p in config.get("platform", [])
        if p.get("enable")
        and p.get("type") in {"qq_official", "qq_official_webhook"}
        and (args.platform_id is None or p.get("id") == args.platform_id)
    ]
    if len(platforms) != 1:
        raise RuntimeError("Select exactly one enabled QQ official bot with --platform-id")
    platform = platforms[0]
    plugin_config = load_json(args.plugin_config)
    if not plugin_config.get("command_enable", False):
        raise RuntimeError("Enable command_enable in the plugin config before syncing panels")
    items = panel_items(plugin_config)
    if not items:
        raise RuntimeError("No enabled plugin commands")
    if not args.apply:
        print(
            json.dumps(
                {"platform": platform["id"], "scopes": ["group", "c2c"], "items": items},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    auth = request(
        "POST",
        "/app/getAppAccessToken",
        {"appId": str(platform["appid"]), "clientSecret": platform["secret"]},
    )
    token = auth.get("access_token")
    if not token:
        raise RuntimeError("QQ authentication did not return an access token")
    plans = []
    # 先读取两个场景，再修改；只处理本脚本专属 remark，不覆盖其他面板。
    for scope in ("group", "c2c"):
        remark = f"{OWNER}:{scope}"
        matches = [
            p
            for p in list_panels(scope, token)
            if p.get("target_type") == "all" and p.get("panel", {}).get("remark") == remark
        ]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple managed panels for {scope}; inspect them before syncing")
        plans.append((scope, matches[0] if matches else None, {"items": items, "remark": remark}))
    for scope, existing, panel in plans:
        if existing:
            panel_id = existing["panel_id"]
            current = existing.get("panel", {})
            if normalize_items(current.get("items", [])) == normalize_items(items):
                status = "unchanged"
            else:
                request(
                    "PUT",
                    f"/v2/panels/{urllib.parse.quote(panel_id, safe='')}",
                    {"panel": panel},
                    token,
                )
                status = "updated"
        else:
            result = request(
                "POST", "/v2/panels", {"scope": scope, "target_type": "all", "panel": panel}, token
            )
            panel_id = result.get("panel_id")
            if not panel_id:
                raise RuntimeError(f"QQ did not return a panel_id for {scope}")
            status = "created"
        print(
            json.dumps({"scope": scope, "panel_id": panel_id, "status": status}, ensure_ascii=False)
        )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
