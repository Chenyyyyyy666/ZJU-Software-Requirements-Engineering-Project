# src 目录说明

项目源代码目录。整体数据流是：读取 LoCoMo 对话 → 构建结构化记忆 → 按问题检索并解析状态关系 → 生成答案 → 网页端展示与评测。

## 当前可运行范围

B 的 `common/`、`memory/`、`index/` 已实现 `0.1` 数据接口、BM25、MiniLM 向量检索与 RRF 混合检索。`reasoning/`、`web/`、`eval/` 尚未实现。Memory 有两种构建模式：

- `deepseek`：通过官方 API 抽取事实，关闭 thinking，使用 JSON 输出、来源校验及块级缓存。需要用户本地配置密钥，真实抽取效果尚未验证。
- `turns`：每个文本轮次保存为一条 Memory，entity/attribute/value 和事件时间留空，标记为 `turn_baseline`。用于离线验证管线，不宣称完成语义抽取。

### 无需密钥的演示

以下命令使用仓库自带的人工合成数据，可在 Windows 或 macOS/Linux 的已激活 Python 环境运行。WSL 请在 WSL 内独立创建虚拟环境，不复用 Windows 创建的 `.venv`。

```bash
python -m pip install -r requirements.txt
python -m src.memory.build --data tests/fixtures/locomo_synthetic.json --conversation demo-01 --mode turns
python -m src.index.search --memory outputs/memory/demo-01.json --query "adoption interviews" --top-k 3 --output outputs/demo-search.json
```

生成的检索 JSON 包含排名、分数、Memory 与原始轮次引用。查看原文时用 `src.memory.store.get_source_turns`，数据保存在同一 MemoryBundle 的 source_turns 中。

### 真实数据构建

将课程文件放在根目录 `locomo.json`，复制 `.env.example` 为 `.env` 并填写个人密钥；然后使用真实 sample_id 替换下方占位值。公开数据的 conv-26 只是样例，不能据此推定课程开发集。

```bash
python -m src.memory.build --conversation <sample_id> --mode deepseek
python -m src.index.search --memory outputs/memory/<sample_id>.json --query "your question"
```

读取器使用官方 LoCoMo 的 sample_id / conversation / session_N / dia_id / text 字段；忽略 qa、event_summary、observation、session_summary 和图片派生字段。按会话切块，默认每块最多 20 轮、12000 个文本字符；超限的单轮明确报错，可通过 `--chunk-turns`、`--chunk-chars` 调整，绝不静默截断。

抽取重试最多 3 次；已校验成功的块缓存在 `cache/extractions/`。配置、prompt、模型或输入变化会使用新缓存键。接口结构损坏、无效来源或截断输出不会保存为成功缓存。

### 向量与混合检索

安装可选依赖后，使用同一个 Memory JSON 即可选择 `vector` 或 `hybrid`。默认仍为 BM25，基础模式不加载 embedding 模型。

```bash
python -m pip install -r requirements-vector.txt
python -m src.index.search --memory outputs/memory/demo-01.json --query "progress toward becoming a parent" --mode vector --top-k 3 --output outputs/demo-vector.json
python -m src.index.search --memory outputs/memory/demo-01.json --query "adoption interviews" --mode hybrid --top-k 3 --output outputs/demo-hybrid.json
```

默认在 CPU 本地运行 `sentence-transformers/all-MiniLM-L6-v2`，无需 API key。首次运行从 Hugging Face 下载固定版本的模型到 `cache/embedding_models/`；下载完成后可加 `--local-files-only` 禁止联网下载。可用 `--device cuda` 或 `--device mps` 显式选择已配置的 GPU 环境。Windows、WSL 和 macOS 使用各自的虚拟环境。

Memory 的 content 与问题由同一模型编码为 384 维向量，归一化后计算余弦相似度；人物/属性筛选在排名截断前执行。每路先取 candidate_k，hybrid 按 Memory ID 去重并以等权 RRF（k=60）合并，输出保留每路排名和原始分数。向量模式不设最低相关性阈值，因此有候选不等于一定有答案。

向量缓存以 `indexes/<hash>.json` 保存，包含记忆 ID、构建版本、完整 bundle 哈希、模型 revision、运行库版本、设备和向量校验和。记忆或模型身份变化后自动使用新缓存；缓存损坏直接报错，不静默降级为 BM25。模型失败时 hybrid 也明确失败。

固定模型 revision 为 `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`。为避免静默丢失证据，单条记忆或问题超过模型 256 token 上限时明确报错；需将长记忆拆成更短的事实。此限制按模型 tokenizer 计数，包含特殊 token。[官方模型说明](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

本地验证：47 项自动化测试通过（模型相关单元测试使用测试编码器）；另外使用真实 MiniLM 在 CPU 离线模式下检索公开 conv-26 的 419 条逐轮基线记忆，生成 419 × 384 向量。查询 `adoption interviews` 时 D19:1 在 vector 中排名 3，在 hybrid 中排名 1。该单例验证说明链路可运行，不等于开发集评测结果或普遍优于 BM25，也不代表已完成真实 DeepSeek 抽取验证。

数据格式依据 [LoCoMo 官方仓库](https://github.com/snap-research/locomo)。API 参数依据 DeepSeek 的 [JSON 输出](https://api-docs.deepseek.com/guides/json_mode/) 与 [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) 文档；实际实现与 mock 测试使用 `response_format=json_object` 和 `thinking.type=disabled`。

## 模块划分

| 目录 | 负责人 | 职责 | 对外提供的接口 |
| --- | --- | --- | --- |
| `memory/` | 鲁瑞特 | 读取清洗 LoCoMo（过滤图片链接）、结构化抽取人物/事件/时间/状态、统一 JSON 导出 | Memory 结构的读写函数 |
| `index/` | 鲁瑞特 | 建立检索索引（关键词 / 向量），实现检索接口 | 检索函数，返回带评分的命中列表 |
| `reasoning/` | 于国庆 | Query 分析、RETAIN / UPDATE / SUPERSEDE / CONFLICT 关系判定、选出当前有效记忆、prompt 与答案生成 | 状态解析结果、最终答案 |
| `web/` | 许子萌 | 后端接口与前端页面、记忆与证据视图、状态演化与关系可视化 | HTTP 接口与页面 |
| `eval/` | 张崇洋 | 问答 F1、Recall@K、状态更新准确率的评测脚本与对照实验 | 评测入口与结果输出 |
| `common/` | 陈易汇总，由定义方编写 | 跨模块共用的数据结构与工具函数 | 见 `../docs/interfaces.md` |

## 开发约定

- **接口与实现同步**：跨模块数据结构统一放在 `common/`，同时更新 `../docs/interfaces.md`。当前按 B 负责人授权直接实现 0.1，后续破坏性变更记录版本并在 PR 中说明影响。
- **谁改哪个目录**：每个目录只由对应负责人修改，其他人通过 Pull Request 提建议，避免两人同时改同一个文件。
- **入口保持参数化**：评测入口需读取根目录的 `locomo.json`，接收对话编号与问题参数，把答案写到根目录的结果文件中，详见 `../docs/interfaces.md`。
- **编码规范**：见 `../docs/coding-standards.md`（ruff 格式化与检查）。
- **分支与提交**：见 `../docs/git-conventions.md`。
- **禁止入库**：API key、数据集、模型输出的大文件与缓存。

## 环境与运行

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # 填入自己的 DEEPSEEK_API_KEY
```

- 统一使用 DeepSeek 官方接口，并关闭 thinking 模式。
- 新成员验收标准：从克隆仓库到跑出第一条答案不超过 10 分钟，且不需要询问他人。

## 测试

- 使用 `pytest` 运行测试；涉及模型调用的测试默认走 mock，不产生真实费用。
- 检索与评测模块的关键行为必须有单元测试，由张崇洋编写并反馈问题。

## 本目录维护

目录结构与骨架由陈易维护；各模块内部组织由对应负责人自行决定。新增、移动或重命名顶层模块需在周会提出并由陈易执行，同时更新根目录 `README.md` 的 Repository Structure 一节。
