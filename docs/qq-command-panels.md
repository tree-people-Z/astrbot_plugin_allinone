# QQ 官方指令面板

面板分别针对 QQ 群聊（`group`）和私聊（`c2c`）创建，作用范围为该场景全部用户/群。本插件同步脚本从 v1.4.0 起只包含点歌与歌单；签到等入口由独立插件管理。现有综合面板继续有效，不必重新创建。

| 指令 | 描述 |
| --- | --- |
| `/我的歌单` | 查看收藏歌曲 |
| `/点歌` | 发送指令后补充歌名 |

面板的指令项填入输入框后由用户发送，插件按普通指令处理，因此 `command_enable` 必须开启。点歌应在发送前补充歌名，例如 `/点歌 稻香`。菜单入口不改变普通聊天格式，也不改变现有 Markdown 消息按钮。

实际 API 回读会移除指令名称开头的 `/`，并省略 `only_admin=false`。同步脚本使用不带斜杠的名称，并按有效语义比较配置，避免因这些格式变化重复更新。

## 同步脚本

脚本 `scripts/sync_qq_panels.py` 在 AstrBot 容器工作目录运行，读取现有 `data/cmd_config.json` 和插件配置，不打印 AppSecret 或访问令牌。

只预览：

```sh
python data/plugins/astrbot_plugin_allinone/scripts/sync_qq_panels.py
```

创建或更新：

```sh
python data/plugins/astrbot_plugin_allinone/scripts/sync_qq_panels.py --apply
```

如有多个 QQ 官方账号，必须通过 `--platform-id` 指定 AstrBot 配置中的机器人 ID。脚本只创建/更新 remark 为 `astrbot_plugin_allinone:group` 或 `astrbot_plugin_allinone:c2c` 的全局面板，不覆盖其他面板。再次运行会复用原面板；配置未变化时不提交更新。脚本识别 QQ 返回的空面板列表（可能省略 `records`）。

## 本机容器已创建的面板

2026-10-08 已在本机 `astrbot` 容器的 QQ 官方机器人“艾利欧”创建并回读验证：

- 群聊：`p_Y4eRvNYj7U90h4AGvG44RA`
- 私聊：`p_Y4eRvdYUrV1jh4AGvG44RA`

容器原有 `command_enable=true`，没有修改开关或重启服务。同步脚本另保存在数据目录，可直接执行：

```sh
docker exec astrbot python data/plugin_data/astrbot_plugin_allinone/sync_qq_panels.py --apply
```

线上面板已验证，QQ 客户端的展示和指令发送还需在对应群聊/私聊中检查。输入 `/` 查看面板；客户端显示仍未更新时，可重新进入聊天窗口检查。

接口参考：[创建指令面板](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html)、[查询面板列表](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.get.html)、[修改面板](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.put.html)、[接口鉴权](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/api-use.html)。
