# 智能体动态记忆管理 Agentic Dynamic Memory Management

> ZJU Fall 2026 软件需求工程（Software Requirements Engineering）课程项目
> 队名：`6767组` ｜ 队伍 ID：`2`

## 项目简介 Overview

在长程、多轮对话中，用户的状态、计划和偏好会持续变化。普通的向量检索虽然能召回与问题相关的历史信息，但可能同时召回当前状态与已被后续信息更新的历史状态，从而产生错误回答或错误决策。

本项目基于 LoCoMo 长期多会话对话数据，开发一个支持**结构化记忆构建、查询驱动的动态状态解析、有效记忆检索与可视化**的 Web 应用。系统在回答问题前先判断历史记忆之间的保留、更新替代与冲突关系，确定当前仍然有效的记忆，再据此生成答案，并在网页端展示记忆、原始证据与状态演化过程。

## 当前状态 Status

> 更新于 2026-11-02。

| 事项 | 状态 |
| --- | --- |
| 里程碑 1 团队工作流 | 已提交，tag `v0.1-milestone1` |
| 里程碑 2 Proposal | 已于 10 月 9 日展示，tag `v0.2-proposal` |
| 里程碑 3 软件需求规格说明书 | 已提交，tag `v0.3-srs` |
| 里程碑 4 中期：设计、编码与测试 | 已提交，tag `v0.4-midterm` |
| 里程碑 5 最终交付 | 进行中；最终报告框架已建立，正式实验和最终材料待完成 |
| 已实现模块 | `src/common/`、`src/memory/`、`src/index/`、`src/reasoning/`、`src/web/`、`src/eval/` |
| 开发集 Memory | 四个指定开发集已生成真实 DeepSeek MemoryBundle，共 951 条 Memory；145 个抽取块可由本地缓存复现 |
| 当前验证 | 向量检索专项测试通过；已完成 36 组检索冒烟检查和 24 对状态关系初步评测 |
| 尚未完成 | 四个开发集的正式 QA F1、Recall@1/5、状态混淆矩阵、平均 token 与 Full Text / Vector RAG 对照；最终测试集运行、报告 PDF、slides 和 Process ZIP |

当前 36 组检索检查用于验证接口、排序稳定性和来源回溯，不能代替 Recall@K。24 对状态关系样本的初步结果为 Accuracy `0.7083`、Macro-F1 `0.4710`，但对应 gold 标注仍需整理为可复现实验。可运行范围与已知限制以 [src/README.md](src/README.md) 为准。

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
├── src/                     # 源代码（common、memory、index、reasoning、web、eval）
├── tests/                   # 自动化测试与合成数据 fixture
├── examples/                # 明确标记的手工合成演示数据
├── process/                 # 过程材料：会议纪要、会议截图、周计划
├── experiments/             # 评测脚本、划分配置与实验记录
├── cache/                   # 本地数据、模型和抽取缓存；不入库
└── outputs/                 # 本地 Memory、答案、指标与验证产物；不入库
```

各目录的用途、命名规则与负责人见目录内的 README：[`docs`](docs/README.md)、[`slides`](slides/README.md)、[`src`](src/README.md)、[`process`](process/README.md)、[`experiments`](experiments/README.md)。

## 技术栈 Technology Stack

以下描述当前仓库已经采用的实现，而不是后续可选的扩展方案。

| 层次 | 当前实现 |
| --- | --- |
| 前端 | 原生 HTML5、CSS3 与 JavaScript；浏览器通过 Fetch API 调用本地 JSON 接口，并使用 DOM 渲染记忆、证据与关系视图 |
| 后端 | Python 3.11；Web 接口使用标准库 `ThreadingHTTPServer` |
| 核心能力 | BM25、MiniLM 向量检索和 RRF 混合排序；DeepSeek 官方接口负责结构化抽取、Query 分析、状态关系判断和答案生成 |
| 数据存储 | UTF-8 JSON 文件保存 `MemoryBundle`、检索上下文、答案和实验结果 |
| 工程工具 | pytest、ruff、pre-commit、Git 与 GitHub Flow |

### 文档索引 Documents

| 文档 | 内容 | 收口人 |
| --- | --- | --- |
| [docs/proposal.md](docs/proposal.md) | 目标范围、功能路线图、里程碑计划（里程碑 2） | 陈易 |
| [docs/srs.md](docs/srs.md) | 软件需求规格说明书（里程碑 3，IEEE 830 改编结构） | 张崇洋 |
| [docs/design-and-test.md](docs/design-and-test.md) | 中期设计、实现与测试追踪（里程碑 4） | 张崇洋 |
| [docs/interfaces.md](docs/interfaces.md) | 模块接口与数据结构 | 陈易、于国庆 |
| [docs/memory-design.md](docs/memory-design.md) | 记忆结构与状态关系设计 | 鲁瑞特 |
| [docs/final-report.md](docs/final-report.md) | 最终报告框架、章节分工与图表占位 | 张崇洋主笔、全员供稿 |
| [docs/milestone5-b-delivery.md](docs/milestone5-b-delivery.md) | 四个开发集 Memory 构建、复现与交接说明 | 鲁瑞特 |
| [docs/milestone5-b-quality.md](docs/milestone5-b-quality.md) | 真实 Memory 定向质量抽查与已知问题 | 鲁瑞特 |
| [docs/team_workflow.md](docs/team_workflow.md) | 团队工作流与阶段计划（里程碑 1） | 陈易 |
| [docs/git-conventions.md](docs/git-conventions.md) | 分支、提交、PR 与版本标签规范 | 陈易 |
| [docs/team-roles.md](docs/team-roles.md) | 角色分工、文档责任与会议轮值 | 陈易 |

SRS 使用“引言 → 总体描述 → 系统功能 → 外部接口 → 非功能需求 → 其他需求”的标准结构，并以词汇表、分析模型和待决问题作为附录；项目不涉及的硬件接口和物理安全章节已按实际需求裁剪。

## 环境搭建 Setup

```bash
git clone <仓库地址>
cd ZJU-Software-Requirements-Engineering-Project

python3.11 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
```

项目以 Python 3.11 为基准。依赖清单由鲁瑞特维护：基础依赖和 BM25/RRF 使用 `requirements.txt`，MiniLM 向量检索使用 `requirements-vector.txt`。

如果本机没有 Python 3.11，macOS 可先执行 `brew install python@3.11`；Windows 可通过 Python Launcher 使用 `py -3.11 -m venv .venv` 创建虚拟环境。

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-vector.txt
```

当前锁定的向量组件为 `sentence-transformers 5.1.2`、`transformers 4.57.1`、`torch 2.8.0`、`numpy 2.3.3`。MiniLM 使用固定 revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`；首次运行把模型下载到 `cache/embedding_models/`，随后可离线复用。无需 embedding API key。

启用提交前自动检查（每位成员在自己的机器上执行一次）：

```bash
python -m pip install pre-commit
pre-commit install
```

启用后每次 `git commit` 会自动运行 ruff 格式化与检查，并拦截大文件、私钥与未解决的冲突标记。本项目不配置 CI，因此在提交 Pull Request 前请在本地再手动执行一次 `ruff check .` 与 `pytest -q`，并把结果写进 PR 描述。

团队同时使用 macOS 与 Windows，因此约定：代码中一律使用 `pathlib` 处理路径、所有文件读写显式指定 `encoding="utf-8"`、文件名统一小写加下划线、不把项目放在带中文或空格的路径下。

仓库已包含 `.editorconfig` 与 `.gitattributes`，编辑器的缩进、编码与换行符会自动统一。若 Windows 上出现"整个文件都显示为已修改"的情况，执行一次 `git config core.autocrlf false` 即可。

### 本地被忽略的交付数据

以下内容由 `.gitignore` 排除，不随 `git clone` 下载，需要通过课程数据、交付 ZIP 或共享文件获得：

| 路径 | 内容 |
| --- | --- |
| `cache/public_locomo10.json` | 官方 LoCoMo 10 段公开数据 |
| `cache/extractions/` | DeepSeek Memory 抽取缓存 |
| `cache/embedding_models/` | 固定 revision 的 MiniLM 模型 |
| `outputs/milestone5-b/memory/` | 四个开发集的真实 MemoryBundle |
| `outputs/eval/` | 评测答案和指标结果 |
| `outputs/*.zip` | 模块交付包 |

发布 ZIP、缓存和大型运行产物前应先检查是否含密钥；不要为了共享而直接取消整个目录的忽略规则。

## 配置 Configuration

不要提交任何真实密钥。完整变量清单见 `.env.example`（含 API、数据路径、检索 top-K、缓存目录、日志级别与 Web 端口），复制模板后填入自己的 key：

```bash
cp .env.example .env
```

```dotenv
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
LOCOMO_DATA_PATH=./locomo.json
CACHE_DIR=./cache
WEB_MEMORY_DIR=./outputs/milestone5-b/memory
```

开发阶段可把 `LOCOMO_DATA_PATH` 指向官方公开数据 `./cache/public_locomo10.json`；课程最终提供根目录 `locomo.json` 后应改回该路径。`WEB_MEMORY_DIR` 指向当前实际使用的 MemoryBundle 目录。

约定：统一使用 DeepSeek 官方接口（不使用中转站），调用时关闭 thinking 模式。`.env` 已被 `.gitignore` 忽略，每位成员各自维护一份，不互相传递密钥。

## 数据 Data

项目使用 LoCoMo 长对话数据子集，原始数据不入库。

1. 课程最终评测约定把数据放在仓库根目录并命名为 `locomo.json`。开发阶段可使用官方 `data/locomo10.json`，本地保存为 `cache/public_locomo10.json`。
2. 原始对话为 JSON 格式；数据中的图片链接字段在构建记忆时过滤掉，本项目不使用多模态部分。
3. 开发集固定为 `conv-26`、`conv-30`、`conv-41`、`conv-42`，用于调试 prompt、检索参数和 K 值。
4. 独立测试集可从其余 `conv-43`、`conv-44`、`conv-47`、`conv-48`、`conv-49`、`conv-50` 中任选 2 段。选择必须在正式测试前固定并记录到 `experiments/splits.json`；选择并冻结后不得根据测试结果继续调参。
5. 当前四个开发集已有真实 MemoryBundle，共 951 条 Memory。它们位于 `outputs/milestone5-b/memory/`，由交付 ZIP 提供，不进入 Git。

## 使用 Usage

```bash
# 0. 激活环境
source .venv/bin/activate

# 1. 运行四个开发集的数据预检查
python -m src.memory.devbuild \
  --data cache/public_locomo10.json \
  --source-kind public \
  --preflight

# 2. 构建单段结构化记忆（需要 DEEPSEEK_API_KEY）
python -m src.memory.build \
  --data cache/public_locomo10.json \
  --conversation conv-26 \
  --mode deepseek \
  --output outputs/memory/conv-26.json

# 3. 检索：bm25 / vector / hybrid
python -m src.index.search \
  --memory outputs/milestone5-b/memory/conv-26.json \
  --query "What is Caroline's adoption status?" \
  --mode hybrid \
  --top-k 5

# 4. 启动本地 Web 页面
python -m src.web.server
```

状态解析、答案生成、Web 和评测 CLI 均已提供实现。Web 默认读取 `.env` 中的 `WEB_MEMORY_DIR`；当前本地已解压四个开发集 Bundle。在线问答会调用 DeepSeek，浏览 Memory 和原始证据不需要模型 API。当前可运行范围、参数与已知限制见 [src/README.md](src/README.md)。

### 课程评测接口 Course Evaluation Interface

课程统一约定：数据文件 `locomo.json` 放在仓库根目录，评测入口接收对话编号与问题参数，把答案写入根目录的结果文件。

```bash
python -m src.eval \
  --conversation conv-26 \
  --questions cache/public_locomo10.json \
  --memory outputs/milestone5-b/memory/conv-26.json \
  --output outputs/eval/conv-26-answers.json \
  --metrics-output outputs/eval/conv-26-metrics.json
```

输出要求：每题一句话答案，标明题号与答案。系统内部的记忆结构不要求统一格式。正式格式以助教后续通知为准，入口脚本保持参数化以便适配。

四个开发集可按顺序批量运行：

```bash
python experiments/run_dev_eval.py \
  --questions cache/public_locomo10.json \
  --memory-dir outputs/milestone5-b/memory \
  --mode hybrid \
  --top-k 5 \
  --candidate-k 30 \
  --output-dir outputs/eval/dev-hybrid
```

批量汇总本组方法与两个基线：

```bash
python -m src.eval.aggregate \
  --dataset dev \
  --input ours=outputs/eval/dev-hybrid/dev-answers.json \
  --input full_text=outputs/eval/full-text-answers.json \
  --input vector_rag=outputs/eval/vector-rag-answers.json \
  --output-dir experiments/2026-11-05-dev-results
```

## 评测 Evaluation

| 指标 | 说明 |
| --- | --- |
| 问题回答 F1 | 答案与标准答案的词级 F1，使用 LoCoMo 官方评测脚本 |
| 证据记忆检索 Recall@K | 检索到的前 K 条记忆中是否包含该题标注的关键证据 |
| 动态状态更新判断准确率 | RETAIN / UPDATE / SUPERSEDE / CONFLICT 判定是否正确 |

对照方法：Full Text（全文交给模型）与 Vector RAG（只检索、不做状态关系判断）。

评测实验的配置、结果与结论记录在 [`experiments/`](experiments/README.md)。注意：标注的 gold evidence 只用于评测，不得进入记忆构建流程或提供给模型。

状态关系评测需要单独的人工 gold 表 `experiments/memory_pairs_gold.csv`。当前仅有 24 对样本的阶段性报告，原始标注和逐题预测明细尚未随 Git 保存，因此不能把该报告直接当作最终可复现指标。

## 结果 Results

> 下表是正式实验结果占位，不代表当前系统效果。数字产生后须同时记录数据划分、检索参数、模型版本、样本量和对应 commit。

| 方法 | 问题回答 F1 | Recall@1 | Recall@5 | 状态更新准确率 | 平均 token |
| --- | --- | --- | --- | --- | --- |
| Full Text | 待运行 | 不适用 | 不适用 | 不适用 | 待统计 |
| Vector RAG | 待运行 | 待运行 | 待运行 | 不适用 | 待统计 |
| 本组方法 | 待运行 | 待运行 | 待运行 | 待正式标注 | 待补代码统计 |

当前已确认但没有填入正式结果表的数据：

| 项目 | 当前证据 |
| --- | --- |
| 四开发集 Memory 构建 | 951 条 Memory、145 个抽取块、293,996 token；真实构建记录与缓存复验完整 |
| 检索工程检查 | 36 组 BM25/Vector/Hybrid 检查均能回溯来源；不是 Recall@K |
| 状态关系初步评测 | 24 对样本 Accuracy `0.7083`、Macro-F1 `0.4710`；SUPERSEDE 召回为 0，样本和标注文件需补齐 |
| 正式 QA F1 / Recall | 尚未运行 |
| Full Text / Vector RAG | 尚未运行 |
| 两段独立测试 | 尚未选择并冻结；尚未运行 |

## 里程碑 Deliverables and Milestones

| # | 里程碑 | 权重 | 交付物 | 截止 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | 团队工作流 Team Workflow | 15% | 文档 PDF（不超过 5 页）+ process ZIP | 10 月 10 日 24:00 | 已提交 |
| 2 | Proposal：目标范围与功能路线图 | 5% | slides PDF（不超过 10 页）+ process ZIP | 展示 10 月 9 日；材料 10 月 10 日 24:00 | 已完成 |
| 3 | 需求规格说明书 Software Requirements Specification | 15% | 文档 PDF（不超过 5 页）+ process ZIP | 10 月 24 日 24:00 | 已完成 |
| 4 | 中期：设计、编码与测试 | 35% | 设计/测试文档（不超过 5 页）+ 代码 + slides PDF（不超过 10 页）+ process ZIP | 展示 10 月 22–23 日；材料 10 月 24 日 24:00 | 已完成 |
| 5 | 最终报告与展示 | 30% | 最终报告 + 代码 + process ZIP | 11 月 9 日 24:00 | 进行中 |

**本组展示任务**：提案（Proposal）已于 10 月 9 日完成；中期展示安排在 10 月 22–23 日，约 10 分钟展示 + 5 分钟提问。即使某个里程碑没有上台展示，对应材料仍需按期提交。中期是否需要单独提交 slides，助教答复为准，本仓库按需要准备。

提交方式：邮件发送给助教，命名 `PROJECT-MILESTONE[n]-[team_id]-[team_name]`，如 `PROJECT-MILESTONE3-2-6767组`。

版本标签：`v0.1-milestone1`、`v0.2-proposal`、`v0.3-srs`、`v0.4-midterm`。最终交付将使用 `v1.0-final`。

## 团队工作流 Team Workflow

- **仓库**：成员按角色授予写入权限；由 Ruleset `protect-main` 保护默认分支 `main`：所有改动必须通过 Pull Request 合并、至少一位成员审核通过、禁止删除主干与强推覆盖历史、合并方式仅允许 Squash。
- **分支**：`feat/`、`fix/`、`docs/`、`chore/`、`exp/` 前缀，一人一分支，分支寿命控制在数天内。
- **提交**：遵循约定式提交，如 `feat(memory): 抽取人物与时间字段`。
- **本地检查**：提交前由 `pre-commit` 自动运行 ruff 与大文件、私钥检查；不配置 CI，因此 PR 描述中需附上本地 `ruff check .` 与 `pytest -q` 的执行结果。
- **会议**：每周一次例会，组织者固定为陈易，会议纪要由轮值成员于当天上传至 `process/`。
- **记录**：每个里程碑打 tag（如 `v0.1-milestone1`），过程材料当天入库。

详细规范见 [Git 协作规范](docs/git-conventions.md)（陈易）与 `docs/coding-standards.md`（鲁瑞特，第一阶段）。

## 路线图 Roadmap

- [x] 仓库初始化、目录结构与协作约定
- [x] 结构化记忆构建（LoCoMo 对话 → 统一 JSON）
- [x] 基础检索（BM25 / MiniLM 向量 / RRF 混合）
- [x] 动态状态解析（RETAIN / UPDATE / SUPERSEDE / CONFLICT）与答案生成
- [x] Web 应用与记忆、证据、问答和状态关系可视化（已实现，待最终手工验收）
- [x] 评测 CLI、离线指标计算和结果聚合工具（已实现，待真实实验）
- [x] 四开发集真实 Memory 构建与质量抽查（存在 7 条已记录问题）
- [ ] 正式 QA F1、Recall@1/5 与状态关系指标
- [ ] Full Text、Vector RAG 对照分析
- [ ] 固定两段测试对话并在冻结后运行
- [ ] 最终报告、slides、演示环境和 Process ZIP
- [ ] 扩展方向（选做）：图记忆与多跳关系、记忆合并压缩、自适应检索与复杂时间推理

## 参考资料 References

- Maharana A, et al. *Evaluating Very Long-Term Conversational Memory of LLM Agents*. ACL 2024.
- LoCoMo 数据集与代码：https://github.com/snap-research/locomo
- DimMem：结构化记忆与维度检索思路
- MRAgent：Cue–Tag–Content 图与主动检索思路

## 致谢 Acknowledgements

课程指导教师与助教提供的项目课题、数据与答疑支持。
