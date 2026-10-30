# JobRadar 实例模板

JobRadar 把**代码**和**你的个人实例**分开：

- 代码目录（本仓库）：skill、脚本、测试、公共来源配置。升级时直接替换，不含任何个人数据。
- 实例目录（你自己建）：你的硬性条件、职业画像、飞书工作台配置、永久编号、搜索结果。
  运行脚本时用 `--root <实例目录>` 指向它。

本目录是一个可以直接复制使用的示例实例，所有内容都是虚构的示例。

## 目录结构

| 路径 | 内容 | 谁来写 |
|---|---|---|
| `Profile/hard-conditions.md` | 求职硬性条件原文 | 你 |
| `Profile/career-profile.md` | 职业画像，第二阶段评级时使用 | 你 |
| `system/config/preferences.json` | 硬条件文档的位置、复核过的哈希、`hard_rules`（把硬条件写成机器规则），以及 `postdoc_prep_days`（博士后申请至少需要的准备天数，0 表示不设） | 你，或在你授权下由 agent 维护 |
| `system/config/feishu-workbench.json` | 飞书工作台的 `base_token` 和各表 ID | 你 |
| `system/config/feishu-aggregate.json` | 可选：你想用作二级来源的飞书岗位合集（只读） | 你 |
| `system/state/` | 永久编号登记、关注清单、运行锁 | 脚本自动生成 |
| `outputs/` | 每批搜索结果 | 脚本自动生成 |
| `system/validation/` | 测试和诊断运行 | 脚本自动生成 |

## 建立你的实例

1. 复制模板：`cp -R <JobRadar代码目录>/templates/instance ~/JobSearch`（目录名随意）。
2. 把 `Profile/` 里的两个示例文件改成你自己的内容。
3. 按你的硬性条件修改 `system/config/preferences.json` 里的 `hard_rules`。各节都可以删除；
   删除的一节就不再作为淘汰条件。字段说明见代码目录的
   `system/skill/jobradar/scripts/initial_filter.py` 开头。
4. 确认规则与文档一致后，写入文档的新哈希：
   `shasum -a 256 Profile/hard-conditions.md`，把结果填进 `hard_constraint_sha256`，
   并更新 `hard_constraint_reviewed_at`。
5. 用到飞书工作台时，填写 `system/config/feishu-workbench.json`。占位符没有替换之前，
   第二阶段会拒绝访问飞书。
6. 如果你有想用的飞书岗位合集，填写 `system/config/feishu-aggregate.json`：`base_token`、`table_id`、
   `view_id` 可以从合集链接里取，`fields` 和 `search_fields` 填字段 ID（可用
   `lark-cli base +field-list` 查询，具体参数以 `--help` 为准）。不用的话保留占位符即可，这个来源会被跳过。
7. 把两个 skill 链接进实例（Codex、OpenCode 用 `.agents/skills`，Claude Code 用 `.claude/skills`）：
   ```bash
   cd ~/JobSearch
   mkdir -p .agents/skills .claude/skills
   for s in jobradar potential-position; do
     ln -s <JobRadar代码目录的绝对路径>/system/skill/$s .agents/skills/$s
     ln -s <JobRadar代码目录的绝对路径>/system/skill/$s .claude/skills/$s
   done
   ```
8. 检查：`python3 <代码目录>/system/skill/jobradar/scripts/radar.py --root ~/JobSearch doctor`，
   输出里的 `rules.ready` 应为 `true`。

## 硬条件文档改了怎么办

文档一旦改动，哈希就对不上，`screen` 会拒绝运行。这是有意的设计：先按新文档更新
`hard_rules`，再写入新的哈希。只改哈希、不复核规则，等于绕过这道检查。
