# JobRadar

JobRadar 是一套给 AI agent（Claude Code、Codex 等）用的求职框架，由两个 skill 组成：

- **jobradar（第一阶段）**：按“官方来源 → 结构化合集 → 其他平台”的顺序搜索大陆和国际岗位，
  按你写下的硬性条件做有证据的第一轮淘汰。证据不足的一律保留待核，不猜测。
- **potential-position（第二阶段）**：复核候选、按契合度评级，核实直达链接、截止日期和投递名额，
  维护飞书求职工作台，并记录你本人报告的投递状态。

所有投递都由你本人提交，agent 不会替你点提交、发消息或上传材料。

## 代码与个人实例分开

本仓库只有代码。你的硬性条件、职业画像、飞书配置、岗位编号和搜索结果放在另一个**实例目录**里，
运行时用 `--root` 指向它。

## 开始使用

需要 Python 3.10+。飞书工作台（第二阶段）需要已登录的 [`lark-cli`](https://github.com/larksuite/cli)；可选的 Jev 工具还需要 Node。

1. 按 [templates/instance/README.md](templates/instance/README.md) 建立实例，写好硬性条件。
2. 把两个 skill 链接进实例，然后在实例目录里启动你的 Agent。
3. 告诉 Agent 要搜索什么。使用说明见 [docs/WORKFLOW.md](docs/WORKFLOW.md)，搜索规则见 [docs/RADAR.md](docs/RADAR.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `system/skill/` | 两个 skill：说明（SKILL.md、references）和脚本 |
| `system/tools/browser.py` | 专用浏览器（CDP）的启动与检查 |
| `system/config/` | 代码自带的公共配置（Jev） |
| `system/tests/` | 测试 |
| `templates/instance/` | 示例实例 |
| `docs/` | 使用说明 |

## 测试

只需要 Python 3.10+，不依赖第三方包：

```bash
python3 system/skill/jobradar/scripts/test_radar.py
python3 system/skill/potential-position/scripts/test_position.py
python3 system/tests/test_initial_filter.py
python3 system/tests/test_feishu_source.py
```

## 许可证

[MIT](LICENSE)
