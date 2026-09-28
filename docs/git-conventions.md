# Git 协作规范 Git Conventions

## 一、总体流程

采用 **GitHub Flow**：主干（`main`）始终可交付，所有改动通过短生命周期的功能分支 + Pull Request 合入。流程定义见 [GitHub Flow 官方文档](https://docs.github.com/zh/get-started/using-github/github-flow)。

```text
main（随时可交付）
  └─ 开功能分支 → 小步提交 → push → 开 PR → 一人 Approve → Squash merge → 删分支
                                                              └─ 里程碑结束打 tag
```

不使用 `develop` / `release` 等多层分支模型：本项目只有一个交付主线，多一层分支只会让每个改动合并两次。

## 二、仓库与权限

| 项目 | 约定 |
| --- | --- |
| 可见性 | 私有仓库，仅邀请项目成员 |
| 主干保护 | 通过 Ruleset 保护默认分支 `main` |
| 合并要求 | 必须通过 PR，且至少一位非作者的成员 Approve |
| 合并方式 | 统一使用 **Squash merge**，保持主干历史线性、一个 PR 对应一条提交；平台侧只保留 Squash 这一种合并方式 |
| 管理员 | 陈易负责成员邀请、权限分配、分支保护与敏感信息管理 |

### 当前 Ruleset 配置

| 设置项 | 当前值 | 说明 |
| --- | --- | --- |
| Ruleset Name | `protect-main` | |
| Enforcement status | Active | 规则已生效 |
| Bypass list | 空 | 任何人都不能绕过规则，包括管理员 |
| Target branches | Default（默认分支） | 当前即 `main` |
| Restrict deletions | 已启用 | 禁止删除主干 |
| Block force pushes | 已启用 | 禁止强推覆盖历史 |
| Require a pull request before merging | 已启用 | 必须开 PR 才能合入主干 |
| Require status checks to pass | 未启用 | 本项目不配置 CI，没有状态检查可指定 |
| Require linear history | 未启用 | 使用 Squash merge 已能保持线性历史，无需重复设置 |
| Require signed commits / deployments / code scanning | 未启用 | 本项目不需要 |

`Require a pull request before merging` 的 **Show additional settings** 当前配置：

| 设置项 | 当前值 | 说明 |
| --- | --- | --- |
| Required approvals | 1 | 至少一位成员审核通过才能合并 |
| Dismiss stale pull request approvals when new commits are pushed | 未启用 | 建议启用：本项目没有 CI，审核是唯一的把关环节，审核意见应当对应到具体的那一次提交 |
| Require review from specific teams / Code Owners | 未启用 | 5 人小组不需要 |
| Require approval of the most recent reviewable push | 未启用 | 仅在多人向同一个 PR 推送时才需要 |
| Require conversation resolution before merging | 未启用 | 建议启用，保证 PR 中的讨论都已处理 |
| Require an additional approval for unattributed Copilot pull requests | 未启用 | 不使用 Copilot 自动开 PR，无需启用 |

**Allowed merge methods 已设为仅 Squash**，与"统一 Squash merge"的规定一致：合并方式由平台强制，不依赖成员自觉。PR 页面上只会出现 Squash and merge 一个选项，因此 **PR 标题就是主干上的提交信息**，必须遵循约定式提交格式（见第六节）。

## 三、分支命名

命名遵循社区规范 [Conventional Branch](https://conventionalbranch.org/zh/)。下表是本项目用到的类型，规范的完整类型清单见原文。

格式：`<type>/<issue-id>-<简短描述>`，全小写、连字符分隔、不使用中文与空格、不超过 50 字符。

| 前缀 | 用途 | 示例 |
| --- | --- | --- |
| `feat/` | 新功能 | `feat/12-memory-extraction` |
| `fix/` | 修复缺陷 | `fix/34-json-encoding` |
| `docs/` | 文档与 slides | `docs/7-milestone1` |
| `refactor/` | 重构，不改变行为 | `refactor/20-retrieval-api` |
| `test/` | 补充测试 | `test/22-recall-eval` |
| `chore/` | 配置、依赖、脚本 | `chore/5-ruff-setup` |
| `exp/` | 探索性实验，可能不合并 | `exp/18-graph-memory` |

约定：

- 一人一分支，一个分支只做一件事；分支寿命控制在 1-3 天。
- 不使用个人名或含糊命名（`zhangsan-dev`、`new`、`test2`）。
- 建议在 GitHub Issues 中为每个行动项建一条 issue，分支名带上编号，PR 中用 `Closes #编号` 关联。

## 四、提交流程

```bash
git switch main
git pull                               # 先同步主干
git switch -c feat/12-memory-extraction

# 干活，随时提交
git add -A
git commit -m "feat(memory): 抽取人物与时间字段"
git push -u origin feat/12-memory-extraction

# 到 GitHub 开 PR，等待一位成员 Approve 后 Squash merge
```

推送前先与主干对齐，避免最后一次性撞上大冲突：

```bash
git fetch origin
git rebase origin/main        # 在自己的分支上进行
```

## 五、提交信息规范（约定式提交）

遵循 [Conventional Commits 1.0.0（中文版）](https://www.conventionalcommits.org/zh-hans/v1.0.0/)。

格式：`<type>(<scope>): <描述>`，其中 `scope` 可省略。

| 类型 | 含义 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档、注释、slides |
| `test` | 测试相关 |
| `refactor` | 重构，不改变外部行为 |
| `chore` | 构建、依赖、配置、脚本 |
| `perf` | 性能优化 |
| `style` | 格式调整，不影响逻辑 |

`scope` 建议使用模块名：`memory`、`index`、`reasoning`、`web`、`eval`、`docs`、`process`、`ci`。

示例：

```text
feat(memory): 实现 LoCoMo 对话的结构化抽取
fix(eval): 修正 Recall@K 在无命中时的除零错误
docs(milestone1): 补全团队分工与会议机制
chore(ci): 增加 ruff 格式检查
```

规则：

- 描述用一句话说清"做了什么"，不加句号，中英文统一（本项目使用中文）。
- 一次提交只做一件事；"小而多"优于"大而少"。
- **每人每周都要有有意义的提交**，不接受截止前一次性全量提交——提交历史是课程评分项之一。
- 破坏性变更（例如修改他人依赖的接口）在类型或 scope 后加 `!`，并在正文或 footer 中写明 `BREAKING CHANGE: 说明`。

## 六、Pull Request 规范

**标题**同样使用约定式提交格式，因为 Squash merge 后它会成为主干上的提交信息：

```text
feat(reasoning): 增加记忆状态关系判定
```

**描述**按模板填写：

```markdown
## 改了什么
## 为什么改（Closes #编号）
## 怎么验证的（命令、结果、截图）
## 影响范围（是否影响他人模块的接口）
## 自检
- [ ] 本地跑过 lint 与测试
- [ ] 未提交密钥、数据或大文件
- [ ] 接口改动已通知消费方
```

其他要求：

| 项目 | 约定 |
| --- | --- |
| 规模 | 控制在 400 行以内，超过则拆分 |
| 状态 | 尚未完成但希望先获得反馈时，开 Draft PR |
| 审核 | 至少一位非作者 Approve；reviewer 尽量当天反馈 |
| 评论 | 处理完成的评论点 Resolve conversation，不留悬空讨论 |
| 标签 | 按类型添加 `feat` / `fix` / `docs` |
| 合并后 | 删除已合并分支 |

由于本项目不配置 CI，审核时没有自动化的绿灯可依赖，因此额外约定：

- 作者必须在**怎么验证的**一栏写出实际执行的命令与结果（例如 `ruff check .`、`pytest -q` 的输出）。
- reviewer 有权要求作者补充验证证据；合并前 reviewer 至少本地拉取该分支跑一次核心检查。

## 七、冲突处理

1. 在自己的分支上把 `main` 的最新内容合并或 rebase 进来，解决冲突后再推送。
2. 不要为了图快把没解决完的分支合进 `main`。
3. 冲突多来自两人同时修改同一文件，因此需遵守文件归属约定（见 `team-roles.md` 第六节）。

## 八、版本标记 Tag

每个里程碑交付后立即打 tag，便于回溯每个提交给老师的版本：

```bash
git tag -a v0.1-milestone1 -m "Milestone 1 Team Workflow"
git push origin v0.1-milestone1
```

命名约定：`v0.1-milestone1`、`v0.2-proposal`、`v0.3-srs`、`v0.4-midterm`、`v1.0-final`。

## 九、禁止事项

- 直接向 `main` 推送（分支保护会拦截）。
- 对共享分支执行 `git push -f`。
- 提交 API key、`.env`、数据集、模型输出的大文件。
- 使用 `--no-verify` 跳过本地检查。
- 截止前一次性提交所有人的全部工作。

## 十、常见问题

| 情况 | 处理方式 |
| --- | --- |
| 提交信息写错、尚未推送 | `git commit --amend` 修改后重新提交 |
| 提交信息写错、已推送 | 在 PR 中用正确标题 Squash merge，主干历史仍保持正确 |
| 提交后发现漏了文件 | 未推送用 `git add <文件>` + `git commit --amend`；已推送则追加一次提交 |
| 忘记拉取最新主干 | `git fetch origin && git rebase origin/main` 后再推送 |
| 误把密钥提交了 | 立即到平台撤销并重新生成密钥；仅删除文件无法从历史中移除 |
| 分支已合并但仍需继续修改 | 从最新 `main` 重新开分支，不复用已合并的分支 |

## 十一、自动化检查

本项目不配置 CI，检查全部在本地完成。由 `pre-commit` 在提交前执行 ruff 格式化与检查，并拦截大文件与私钥：

```bash
pip install pre-commit
pre-commit install
```

由于没有服务端兜底，本地钩子是唯一的自动检查，因此约定：

- 每人克隆仓库后必须执行一次 `pre-commit install`；未安装钩子的机器上产生的提交，reviewer 应要求补齐格式。
- 不允许使用 `--no-verify` 跳过检查。
- 提交 PR 前，作者在本地至少执行一次 `ruff check .`、`ruff format --check .` 与 `pytest -q`，并把结果写进 PR 描述。
- reviewer 在合并前至少本地拉取该分支跑一次上述检查，作为 CI 的人工替代。
- 涉及模型调用的测试默认走 mock，不产生真实费用；真实调用的验证以命令输出或截图形式附在 PR 中。

若后续希望启用 CI，只需补充 GitHub Actions 工作流，再回到 Ruleset 中打开 `Require status checks to pass` 并选中对应检查名称。

## 十二、规范原文与相关文档

本规范依据以下公开约定：

- [GitHub Flow 官方文档](https://docs.github.com/zh/get-started/using-github/github-flow)
- [Conventional Commits 1.0.0 中文版](https://www.conventionalcommits.org/zh-hans/v1.0.0/)
- [Conventional Branch 中文版](https://conventionalbranch.org/zh/)

仓内相关文档：

- 团队分工与文件归属：`team-roles.md`
- 代码规范：`coding-standards.md`
- 模块接口契约：`interfaces.md`
