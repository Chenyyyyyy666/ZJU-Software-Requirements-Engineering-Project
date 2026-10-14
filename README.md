# 智能体动态记忆管理 Agentic Dynamic Memory Management

> ZJU Fall 2026 软件需求工程（Software Requirements Engineering）课程项目
> 队名：`[team_name]` ｜ 队伍 ID：`[team_id]`

## 项目简介 Overview

在长程、多轮对话中，用户的状态、计划和偏好会持续变化。普通的向量检索虽然能召回与问题相关的历史信息，但可能同时召回当前状态与已被后续信息更新的历史状态，从而产生错误回答或错误决策。

本项目基于 LoCoMo 长期多会话对话数据，开发一个支持**结构化记忆构建、查询驱动的动态状态解析、有效记忆检索与可视化**的 Web 应用。系统在回答问题前先判断历史记忆之间的保留、更新替代与冲突关系，确定当前仍然有效的记忆，再据此生成答案，并在网页端展示记忆、原始证据与状态演化过程。

## 团队 Team

| 成员 | 角色 | 主要职责 |
| --- | --- | --- |
| 陈易 | 组长：项目管理与集成架构 | 里程碑计划与进度追踪、主持周会、仓库与权限管理、整体架构与集成、评测接口对齐、部署与演示环境、对外提交 |
| 鲁瑞特 | 数据、记忆与检索基础设施 | 数据清洗、Memory 结构设计、结构化抽取与 JSON 导出、检索索引与接口、依赖与版本管理 |
| 于国庆 | 动态状态解析（关键路径） | Query 分析、RETAIN / UPDATE / SUPERSEDE / CONFLICT 关系判定、有效记忆选择、prompt 设计与答案生成 |
| 许子萌 | Web 应用与可视化 | 后端接口与前端页面、记忆与原始证据视图、状态演化与关系可视化、演示素材 |
| 张崇洋 | 评测与文档主笔 | 三个评测指标的脚本与对照实验、实验记录与图表、各里程碑文档的合并定稿与 PDF 导出、slides 排版 |

完整的角色边界、辅助工作归属、文档责任与会议轮值见 [`docs/team-roles.md`](docs/team-roles.md)。

## 仓库结构 Repository Structure

```text
.
├── README.md                # 项目说明（本文件）
├── .gitignore               # 忽略规则：密钥、数据集、缓存与临时文件
├── .gitattributes           # 换行符与二进制文件约定（macOS 与 Windows 协作）
├── .editorconfig            # 编辑器缩进、编码与换行统一配置
├── .env.example             # 环境变量模板（真实密钥填在本地 .env，不入库）
├── .pre-commit-config.yaml  # 提交前自动检查：ruff、大文件、私钥
├── pyproject.toml           # ruff 与 pytest 工具配置
├── docs/                    # 各里程碑交付文档（Markdown 源 + 导出 PDF）
├── slides/                  # 课堂展示的幻灯片源文件与 PDF
├── src/                     # 源代码
├── process/                 # 过程材料：会议纪要、会议截图、周计划
└── experiments/             # 实验配置与结果记录
```

各目录的用途、命名规则与负责人见目录内的 README：[`docs`](docs/README.md)、[`slides`](slides/README.md)、[`src`](src/README.md)、[`process`](process/README.md)、[`experiments`](experiments/README.md)。

## 环境搭建 Setup

```bash
git clone <仓库地址>
cd ZJU-Software-Requirements-Engineering-Project

python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
```

依赖清单由鲁瑞特维护，已包含 Memory 与 BM25 最小链路所需依赖。在虚拟环境激活后执行 `python -m pip install -r requirements.txt`；无需密钥的完整演示见 [src/README.md](src/README.md)。

向量与混合检索另安装 `python -m pip install -r requirements-vector.txt`，默认使用本地 MiniLM，无需 embedding API key。首次运行会下载固定版本的模型，随后复用本地模型与向量缓存。

启用提交前自动检查（每位成员在自己的机器上执行一次）：

```bash
pip install pre-commit
pre-commit install
```

启用后每次 `git commit` 会自动运行 ruff 格式化与检查，并拦截大文件、私钥与未解决的冲突标记。本项目不配置 CI，因此在提交 Pull Request 前请在本地再手动执行一次 `ruff check .` 与 `pytest -q`，并把结果写进 PR 描述。

团队同时使用 macOS 与 Windows，因此约定：代码中一律使用 `pathlib` 处理路径、所有文件读写显式指定 `encoding="utf-8"`、文件名统一小写加下划线、不把项目放在带中文或空格的路径下。

仓库已包含 `.editorconfig` 与 `.gitattributes`，编辑器的缩进、编码与换行符会自动统一。若 Windows 上出现"整个文件都显示为已修改"的情况，执行一次 `git config core.autocrlf false` 即可。

## 配置 Configuration

不要提交任何真实密钥。完整变量清单见 `.env.example`（含 API、数据路径、检索 top-K、缓存目录、日志级别与 Web 端口），复制模板后填入自己的 key：

```bash
cp .env.example .env
```

```dotenv
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

约定：统一使用 DeepSeek 官方接口（不使用中转站），调用时关闭 thinking 模式。`.env` 已被 `.gitignore` 忽略，每位成员各自维护一份，不互相传递密钥。

## 数据 Data

项目使用课程选定的 LoCoMo 长对话数据子集，原始数据不入库。

1. 取得课程提供的数据文件，放在仓库**根目录**下并命名为 `locomo.json`（这是课程评测约定的路径）。
2. 原始对话为 JSON 格式；数据中的图片链接字段在构建记忆时过滤掉，本项目不使用多模态部分。
3. 数据划分、开发集与测试集对话编号：`[待课程助教公布后补充]`。

## 使用 Usage

> 各命令随模块实现逐步补充，最终以 `src/README.md` 与代码入口为准。

```bash
# 1. 构建结构化记忆
python -m src.memory.build --conversation [conv_id]

# 2. 回答问题
python -m src.reasoning.answer --conversation [conv_id] --questions [questions.json]

# 3. 启动 Web 应用
[待实现]
```

### 课程评测接口 Course Evaluation Interface

课程统一约定：数据文件 `locomo.json` 放在仓库根目录，评测入口接收对话编号与问题参数，把答案写入根目录的结果文件。

```bash
python [评测入口] --conversation [conv_id] --output answers.json
```

输出要求：每题一句话答案，标明题号与答案。系统内部的记忆结构不要求统一格式。正式格式以助教后续通知为准，入口脚本保持参数化以便适配。

## 评测 Evaluation

| 指标 | 说明 |
| --- | --- |
| 问题回答 F1 | 答案与标准答案的词级 F1，使用 LoCoMo 官方评测脚本 |
| 证据记忆检索 Recall@K | 检索到的前 K 条记忆中是否包含该题标注的关键证据 |
| 动态状态更新判断准确率 | RETAIN / UPDATE / SUPERSEDE / CONFLICT 判定是否正确 |

对照方法：Full Text（全文交给模型）与 Vector RAG（只检索、不做状态关系判断）。

评测实验的配置、结果与结论记录在 [`experiments/`](experiments/README.md)。注意：标注的 gold evidence 只用于评测，不得进入记忆构建流程或提供给模型。

## 结果 Results

| 方法 | 问题回答 F1 | Recall@K | 状态更新准确率 | 平均 token |
| --- | --- | --- | --- | --- |
| Full Text | 待填 | 待填 | 待填 | 待填 |
| Vector RAG | 待填 | 待填 | 待填 | 待填 |
| 本组方法 | 待填 | 待填 | 待填 | 待填 |

## 里程碑 Deliverables and Milestones

| # | 里程碑 | 权重 | 交付物 | 截止 |
| --- | --- | --- | --- | --- |
| 1 | 团队工作流 Team Workflow | 15% | 文档 PDF（不超过 5 页）+ process ZIP | 10 月 10 日 24:00 |
| 2 | Proposal：目标范围与功能路线图 | 5% | slides PDF（不超过 10 页）+ process ZIP | 上台展示 10 月 9 日；材料 10 月 10 日 24:00 |
| 3 | 需求规格说明书 Software Requirements Specification | 15% | 文档 PDF + process ZIP | 10 月 24 日 24:00 |
| 4 | 中期：设计、编码与测试 | 35% | 设计/测试文档 + 代码 + process ZIP | 10 月 24 日 24:00 |
| 5 | 最终报告与展示 | 30% | 最终报告 + 代码 + process ZIP | 11 月 9 日 24:00 |

**本组展示任务**：仅提案（Proposal）一次，10 月 9 日，约 10 分钟展示 + 5 分钟提问。其余里程碑按要求提交材料，展示安排以课程通知为准。

提交方式：邮件发送给助教，命名 `PROJECT-MILESTONE[n]-[team_id]-[team_name]`。

## 团队工作流 Team Workflow

- **仓库**：私有仓库，由 Ruleset `protect-main` 保护默认分支 `main`：所有改动必须通过 Pull Request 合并、至少一位成员审核通过、禁止删除主干与强推覆盖历史、合并方式仅允许 Squash。
- **分支**：`feat/`、`fix/`、`docs/`、`chore/`、`exp/` 前缀，一人一分支，分支寿命控制在数天内。
- **提交**：遵循约定式提交，如 `feat(memory): 抽取人物与时间字段`。
- **本地检查**：提交前由 `pre-commit` 自动运行 ruff 与大文件、私钥检查；不配置 CI，因此 PR 描述中需附上本地 `ruff check .` 与 `pytest -q` 的执行结果。
- **会议**：每周一次例会，组织者固定为陈易，会议纪要由轮值成员于当天上传至 `process/`。
- **记录**：每个里程碑打 tag（如 `v0.1-milestone1`），过程材料当天入库。

详细规范见 [Git 协作规范](docs/git-conventions.md)（陈易）与 `docs/coding-standards.md`（鲁瑞特，第一阶段）。

## 路线图 Roadmap

- [x] 仓库初始化、目录结构与协作约定
- [ ] 结构化记忆构建（LoCoMo 对话 → 统一 JSON）
- [ ] 检索与动态状态解析（RETAIN / UPDATE / SUPERSEDE / CONFLICT）
- [ ] Web 应用与记忆、证据、状态演化可视化
- [ ] 三指标评测与 baseline 对照分析
- [ ] 最终报告与展示材料
- [ ] 扩展方向（选做）：图记忆与多跳关系、记忆合并压缩、自适应检索与复杂时间推理

## 参考资料 References

- Maharana A, et al. *Evaluating Very Long-Term Conversational Memory of LLM Agents*. ACL 2024.
- LoCoMo 数据集与代码：https://github.com/snap-research/locomo
- DimMem：结构化记忆与维度检索思路
- MRAgent：Cue–Tag–Content 图与主动检索思路

## 致谢 Acknowledgements

课程指导教师与助教提供的项目课题、数据与答疑支持。
