# astrbot_plugin_allinone 聚合助手

一站式 AstrBot 插件：**每日签到 + 每日老婆 + QQ点歌 + 戳一戳 + QQ群管**，深度融合 LLM 函数调用。

- 来源灵感：Telegram 机器人 [@cvEvthBot](https://t.me/cvEvthBot) 的签到/每日老婆玩法，以及
  [Zhalslar](https://github.com/Zhalslar) 的 `astrbot_plugin_music` / `astrbot_plugin_pokepro` / `astrbot_plugin_qqadmin` 三款插件的交互设计。
- 许可证：[GPL-3.0](./LICENSE)。

## ✨ 功能

### 签到 & 每日老婆
| 指令 | 说明 |
| --- | --- |
| `/签到`（打卡） | 9 级吉凶运势 + 随机积分 + 连签加成，显示累计积分 |
| `/我的信息` | 积分 / 连签 / 总签到 / 今日老婆 |
| `/排行榜` | 积分 Top10（全局或分群可配） |
| `/老婆`（今日老婆/每日老婆） | 每日一次，图 + 角色名/标签 |
| `/换老婆` | 重抽，消耗积分，每日限次 |

老婆图源 `waifu_source`：`manshuo`（默认，漫朔图库高清图 + AI 标签）/ `manshuo_trace`（漫朔图 + trace.moe 反查出处）/ `anilist` / `kitsu`。

## 🗣️ 自然语言驱动（不@ 也能触发）

本插件**以自然语言为主、指令为兜底**（指令默认关闭）：

- **LLM 函数工具**：注册 20+ 函数工具，正常 @机器人 对话时，模型可自主调度签到、点歌、戳人、群管等能力。
- **上下文主动感知插话**（`chatter`）：即使 AstrBot 的主动回复/唤醒关闭，插件也会监听全群消息——

  1. 记住每群最近 14 条对话；
  2. 按模式与冷却/概率决定是否决策（`keyword` 模式命中「签到/老婆/点歌…」才决策，`llm` 模式每条都决策）；
  3. LLM 返回 `{reply, intent, args}` JSON 决策，**调度器（dispatcher）按意图路由到 modules 层**，把结果发回群里；
  4. 群级冷却默认 60 秒、插话概率 0.7，避免刷屏；LLM 不可用时自动退回本地正则规则。

  > 不 @ 也能触发的原理：只要插件注册了消息监听器，AstrBot 会把每条群消息激活给它；@ 只约束 AstrBot 内置 LLM 管线，不影响插件自主感知与调度。

## 🧩 调度器架构（方便加功能）

```
消息 ──→ on_qq_event / on_any_event（监听）
          ├─ poke 事件 → poke 模块
          ├─ 违禁词/刷屏/宵禁 → admin 模块
          └─ chatter 上下文感知 ──→ LLM 决策 JSON ──→ dispatcher.dispatch(intent, args)
                                                        │
                                                        ├→ checkin_wife 模块（签到/老婆/积分）
                                                        ├→ music 模块（点歌/歌词/歌单）
                                                        ├→ poke 模块（戳人）
                                                        └→ admin 模块（群管）
```

**加新功能三步**：modules/ 写方法（返回 str 或消息链）→ `core/dispatcher.py` 的 `INTENT`（`do_xxx` 方法）加映射 → 在 `PROMPT_INTENTS`/`PATTERN_INTENTS` 补意图说明。

### QQ点歌
| 指令 | 说明 |
| --- | --- |
| `点歌 <歌名>` | 搜索并选歌，可结尾序号直接选定（如 `点歌 稻香 2`） |
| `查歌词 <歌名>` | 查询歌词 |
| `歌单添加 <歌名>` / `我的歌单` / `播放歌单 <序号>` / `删除歌单 <序号>` / `清空歌单` | 用户歌单 |

> 音源为QQ音乐（聚合接口 `https://music.txqq.pro/`，可在配置中替换）。发送默认文本链接，`music_record_link` 开启后附带语音。

### 戳一戳（仅 QQ)
- 被戳按权重随机触发：反戳 / QQ表情 / 表情包 / LLM回复 / 禁言 / 触发命令
- `戳 @某人 [次数]`、`戳我`（回复 `戳` + @）、命中关键词自动回戳
- 冷却与概率可配

### QQ群管（仅 QQ）
- 禁言/解禁/全禁、踢人/群拉黑、撤回、改名、群公告、群友信息
- 违禁词（自定义 + 内置）、刷屏检测、宵禁
- 进阶（配置开启）：头衔、上管/下管、改群名、改群头像
- 权限：超级管理员 > 群主 > 管理员 > 成员
- `/群管帮助` 查看指令列表

## 🤖 LLM 融合

- 注册函数工具（可在 WebUI「函数工具」中启用/停用）：
  `checkin`、`query_my_info`、`show_leaderboard`、`draw_daily_wife`、`change_daily_wife`、
  `search_music`、`play_music`、`poke_user`、`ban_group_user`、`kick_group_user`、
  `recall_group_messages`、`rename_group`、`send_group_notice`
- 每轮对话注入一小段能力提示（动态上下文，不污染 system prompt 缓存），让模型明确可用能力
- 媒体类工具（点歌/老婆）内部主动发送音频与图片，只返回简要结果给模型

## ⚙️ 配置

WebUI 插件配置页可修改；关键项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `timezone` | `8` | 时区（自然日重置） |
| `fortune_tiers` | 内置 9 级 | 运势等级/权重/积分区间 |
| `streak_bonus_per_day` / `streak_bonus_cap` | `5` / `50` | 连签加成 |
| `waifu_source` | `manshuo_trace` | 老婆图源 |
| `manshuo_api_key` | 空 | 漫朔 API Key（密文） |
| `change_wife_cost` / `change_wife_limit` | `30` / `2` | 换老婆 |
| `leaderboard_scope` | `global` | 榜单范围 |
| `music_agg_base_url` | `https://music.txqq.pro/` | 点歌聚合接口 |
| `music_record_link` | `false` | 附带语音发送 |
| `poke_*` | 见配置 | 戳一戳权重/冷却/池 |
| `admin_*` | 见配置 | 群管默认值/违禁词/刷屏/宵禁/进阶 |
| `llm_enable` / `llm_capability_hint` | `true` / `true` | LLM 融合开关 |

## 🗂 数据存储

`data/plugin_data/astrbot_plugin_allinone/allinone.db`（单 SQLite：用户积分、每日老婆、歌单、群配置、KV）。

## 📌 已知限制

- 戳一戳与群管仅支持 QQ(aiocqhttp)，其他平台不注册这些 handler。
- 违禁词内置表可在 `modules/group_admin.py:BUILTIN_BADWORDS` 中扩充。
- 戳一戳/群管依赖 OneBot 协议端（napcat 已验证可用性较好的实现），其他协议端以实际为准。

## 📄 License

[GPL-3.0](./LICENSE)
