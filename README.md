# astrbot_plugin_allinone 聚合助手

一站式 AstrBot 插件：**每日签到 + 每日老婆 + QQ点歌 + QQ群管**，深度融合 LLM 函数调用。

- 来源灵感：Telegram 机器人 [@cvEvthBot](https://t.me/cvEvthBot) 的签到/每日老婆玩法，以及
  [Zhalslar](https://github.com/Zhalslar) 的 `astrbot_plugin_music` / `astrbot_plugin_qqadmin` 两款插件的交互设计。
- 许可证：[GPL-3.0](./LICENSE)。

## ✨ 功能

### 签到 & 每日老婆
| 指令 | 说明 |
| --- | --- |
| `/签到`（打卡） | 9 级吉凶运势 + 随机积分 + 连签加成，显示累计积分 |
| `/我的信息` | 积分 / 连签 / 总签到 / 今日老婆 |
| `/排行榜` | 全局积分 Top10，未上榜时在下方单独显示本人名次 |
| `/老婆`（今日老婆/每日老婆） | 每人每天全局一次，图 + 角色名/标签 |
| `/换老婆` | 重抽，消耗积分，每人每天全局限次 |

> 数据以用户为锚点：签到、积分、老婆与换老婆次数在私聊和各群聊之间**互通**，同一用户每天只能签到一次、抽老婆一次。

老婆图源 `waifu_source`：`manshuo`（默认，调用漫朔今日老婆接口）/ `local`（本地图片库）。

本地图片库目录结构为 `图源/角色文件夹/图片`（如 `图源/3_安和昴/27.webp`），`local_wife_paths` 指向 `图源` 根目录。角色名默认取图片所在文件夹名并去掉前导序号（`3_安和昴` → `安和昴`）；可开启 `local_wife_name_llm` 交由大模型从文件夹名提取角色名（失败回退去序号结果）。图片直接放在 `图源` 根目录时不显示名称。

## 🗣️ 自然语言驱动

本插件**以自然语言为主、指令为兜底**（指令默认关闭）：

- **统一使用 AstrBot 默认 Agent 管线**：默认 LLM 负责普通对话、主动回复和工具调用；本插件通过 `@filter.llm_tool` 提供签到、老婆、点歌、群管等能力。主动回复请在 AstrBot 的 `provider_ltm_settings.active_reply` 中配置。

## 🧩 工具架构（方便加功能）

```
消息 ──→ AstrBot 默认 Agent 管线
          ├─ 违禁词/刷屏/宵禁 → admin 模块
          └─ LLM 工具调用 → modules 层
                              ├→ checkin_wife（签到/老婆/积分）
                              ├→ music（点歌/歌词/歌单）
                              └→ admin（群管）
```

**加新功能三步**：modules/ 写方法 → 在 `main.py` 注册 `@filter.llm_tool` → 在能力提示中补充工具说明。

### QQ点歌
| 指令 | 说明 |
| --- | --- |
| `点歌 <歌名>` | 搜索并选歌，可结尾序号直接选定（如 `点歌 稻香 2`） |
| `查歌词 <歌名>` | 查询歌词 |
| `歌单添加 <歌名>` / `我的歌单` / `播放歌单 <序号>` / `删除歌单 <序号>` / `清空歌单` | 用户歌单 |

> 音源为 QQ 音乐（聚合接口 `https://music.txqq.pro/`，可在配置中替换）。配置 `music_card_api_key` 时，QQ OneBot 优先发送签名 Ark 卡片，避免部分 QQ 客户端对普通卡片显示“发送者版本过低”；没有 Key 或签名服务失败时再尝试普通卡片，之后按配置回退语音和文本链接。

### QQ群管（仅 QQ）
- 禁言/解禁/全禁、踢人/群拉黑、撤回、改名、群公告、群友信息
- 违禁词（自定义 + 内置）、刷屏检测、宵禁
- 进阶（配置开启）：头衔、上管/下管、改群名、改群头像
- 权限：超级管理员 > 群主 > 管理员 > 成员
- `/群管帮助` 查看指令列表

## 🤖 LLM 融合

- 注册函数工具（可在 WebUI「函数工具」中启用/停用）：
  `checkin`、`query_my_info`、`show_leaderboard`、`draw_daily_wife`、`change_daily_wife`、
  `search_music`、`play_music`、`ban_group_user`、`kick_group_user`、
  `recall_group_messages`、`rename_group`、`send_group_notice`
- 每轮对话注入一小段能力提示（动态上下文，不污染 system prompt 缓存），让模型明确可用能力
- 签到、资料、排行榜、歌单、歌词、群管结果及媒体内容由插件直接发送完整消息，只向模型返回发送状态

## ⚙️ 配置

WebUI 插件配置页可修改；关键项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `timezone` | `8` | 时区（自然日重置） |
| `fortune_tiers` | 内置 9 级 | 运势等级/权重/积分区间 |
| `streak_bonus_per_day` / `streak_bonus_cap` | `5` / `50` | 连签加成 |
| `waifu_source` | `manshuo` | 老婆图源 |
| `local_wife_paths` | `[]` | 本地图片库根目录（`图源/角色文件夹/图片`），`waifu_source=local` 时使用 |
| `local_wife_name_source` | `folder` | 名称来源：`folder`（角色文件夹名去序号）/`filename`/`none` |
| `local_wife_name_llm` | `false` | 用大模型从文件夹名提取角色名（失败回退去序号） |
| `local_wife_name_llm_timeout` | `20` | 大模型提取名称超时（秒） |
| `change_wife_cost` / `change_wife_limit` | `30` / `2` | 换老婆（每人每天全局） |
| `music_agg_base_url` | `https://music.txqq.pro/` | 点歌聚合接口 |
| `music_send_card` | `true` | QQ OneBot 上尝试普通卡片和签名卡片 |
| `music_card_api_key` | 空 | 签名 Ark 卡片服务的 API Key |
| `music_record_link` | `false` | 卡片失败后尝试语音发送 |
| `music_enable_lyrics` | `false` | 发送后附带歌词预览（仍可用 `/查歌词` 单独查询） |
| `admin_*` | 见配置 | 群管默认值/违禁词/刷屏/宵禁/进阶 |
| `llm_capability_hint` | `true` | 是否向默认 LLM 注入本插件工具能力提示 |

> AstrBot 官方主动回复由 AstrBot 配置中的 `provider_ltm_settings.active_reply` 管理，不再由插件维护独立的 chatter 缓存或决策器。

## 🗂 数据存储

`data/plugin_data/astrbot_plugin_allinone/allinone.db`（单 SQLite：用户积分、每日老婆、歌单、群配置、KV）。

> 积分、老婆、歌单均以用户（`sender_id`）为锚点，私聊与各群互通。升级到本版本时，会自动执行一次性迁移，把历史上按群存储的积分与老婆数据合并到用户级（迁移标记存于 KV 表）。

## 📌 已知限制

- 群管仅支持 QQ(aiocqhttp)，其他平台不注册这些 handler。
- 违禁词内置表可在 `modules/group_admin.py:BUILTIN_BADWORDS` 中扩充。
- 群管依赖 OneBot 协议端（napcat 已验证可用性较好的实现），其他协议端以实际为准。

## 📄 License

[GPL-3.0](./LICENSE)
