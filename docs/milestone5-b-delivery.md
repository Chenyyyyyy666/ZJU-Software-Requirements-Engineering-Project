# Milestone 5：B 构建说明与交付状态

负责人：鲁瑞特（B）｜更新日期：2026-10-30。

## 当前范围与状态

依据组长《milestone5 安排.md》，已完成 B 的四开发集真实 DeepSeek MemoryBundle、质量抽查记录和构建说明。负责人配置密钥并授权执行后，于北京时间 2026-10-29 23:17:20 至 23:31:45 完成真实构建。抽查由 Codex 比对原文完成，不冒充成员人工签署。

| 交付内容 | 当前状态 |
| --- | --- |
| 四个开发集的数据预检查 | 已完成，使用本机公开 LoCoMo 文件，尚未确认与课程文件一致 |
| 一键构建、运行记录、token/耗时统计 | 已实现并完成真实运行；145 次请求全部一次成功 |
| 缓存重建验证 | 四个真实 Bundle 均禁用联网重建，145 个缓存命中，结果完全一致 |
| 四个真实 MemoryBundle | 已生成，共 951 条记忆 |
| 真实人物、事件、状态、时间质量抽查与错误案例 | 已核对 47 条定向样本，记录 7 条问题或限制，见质量报告 |

本机公开数据路径：`cache/public_locomo10.json`。课程默认路径 `locomo.json` 当前不存在，工具不会自动把公开数据标记成课程数据。

| 开发集 | 清洗后文本轮数 | 默认分块数 |
| --- | ---: | ---: |
| conv-26 | 419 | 28 |
| conv-30 | 369 | 27 |
| conv-41 | 663 | 46 |
| conv-42 | 629 | 44 |
| 合计 | 2080 | 145 |

预检查记录：`outputs/milestone5-b/runs/20261029T150607Z-64b75e0d/manifest.json`。这些数字仅说明输入规模，不代表抽取质量或实际 token 用量。

## 真实构建结果

运行记录：`outputs/milestone5-b/runs/20261029T151720Z-ba46dc1f/manifest.json`。

| 开发集 | Memory 数 | 构建秒数 | 输入 token | 输出 token | 总 token |
| --- | ---: | ---: | ---: | ---: | ---: |
| conv-26 | 204 | 177.67 | 45607 | 15460 | 61067 |
| conv-30 | 161 | 162.74 | 39354 | 11757 | 51111 |
| conv-41 | 282 | 267.59 | 71396 | 21068 | 92464 |
| conv-42 | 304 | 254.41 | 66084 | 23270 | 89354 |
| 合计 | 951 | 862.41 | 222441 | 71555 | 293996 |

输入中 34176 token 为 API 报告的服务端缓存命中，188265 为未命中；所有请求均提供 usage。总墙钟时间约 864.75 秒（含四次本地缓存复验及日志写入）。这是真实 Memory 构建用量，不是问答阶段平均 token 或全组评测用量。

请求模型与 API 返回模型名均为 `deepseek-flash`；API 未返回底层权重的不可变版本号，不能宣称锁定快照。实际配置：`memory-facts-v1`，配置 ID `6a02958d90c918c0`，每块至多 20 轮/12000 字符，最大输出 8192 token，timeout 60 秒，最多 3 次尝试，temperature 0，thinking disabled。完整 build_id、输入 SHA-256 和 prompt SHA-256 均在运行记录中。

质量记录见 [真实记忆质量抽查](milestone5-b-quality.md)。保留原始模型产物，已发现的语义问题没有通过手工改写 JSON 隐藏。

## 交接与离线验收

交付包：`outputs/milestone5-b-delivery.zip`。压缩包按仓库相对路径组织，包含四个 Bundle、真实运行记录、47 条抽查记录、145 个抽取缓存、36 组检索结果、交付说明及本轮构建工具改动；不包含 `.env` 或密钥。合并代码时正常评审，不要盲目覆盖同伴对同一文件的修改。

C/D/E 可直接读取 `outputs/milestone5-b/memory/conv-26.json` 等四个文件。需适配 Web 默认路径时由集成方选择读取目录，避免把这些真实 Bundle 与早期 turns 基线混淆。解压到同一版本项目后，保持缓存位置 `cache/extractions/` 即可按相同配置复用。

Web 读取本批真实 Bundle 的 PowerShell 启动方式：

```powershell
$env:WEB_MEMORY_DIR = "outputs/milestone5-b/memory"
python -m src.web.server
```

评测入口通过 `--memory outputs/milestone5-b/memory/conv-26.json` 指定同一对话的 Bundle，配合 E 的 `--conversation`、`--questions`、`--output` 参数；正式问答会调用模型，由 E 统一跑实验。C 使用 `load_memories`、`build_index`、`retrieve_context` 的示例见 `b-handoff.md`。

最终报告供稿见 [B 最终报告章节](final-report-b.md)，过程记录及未完成的协作手续见 [10/30 收尾记录](../process/2026-10-30-b-closeout.md)。

10/30 追加验收：将交付 ZIP 解压至隔离临时目录，从 Bundle 的 source_turns 重建白名单输入，使用包内缓存并将 HTTP 请求函数替换为硬失败；四个 Bundle 均完全一致，共 145 个缓存命中、0 次 API 请求。该检查不依赖本机原始数据文件或抽取缓存，记录在 `closeout-verification.json`。

真实数据离线工程验收：每个对话分别选取首、中、末三条记忆内容作为查询，对 BM25/vector/hybrid 各检查一次，共 36 组；所有命中均可回溯到同一 build_id 的原文，重复检索结果一致。MiniLM 使用本机固定 revision 的模型缓存，不请求 embedding API。报告在 `outputs/milestone5-b/retrieval-verification.json`。这是接口烟雾验证，不能代替 E 的 Recall@K 正式实验。

复现此验收（仓库根目录运行，需要已有本地 MiniLM 模型）：

```powershell
python -c "import runpy; runpy.run_path('outputs/milestone5-b/verify_handoff.py', run_name='__main__')"
```

复现本次公开数据的缓存构建（不调用 API）：

```powershell
python -m src.memory.devbuild --data cache/public_locomo10.json --source-kind public --cache-only
```

原始公开数据不打包；恢复构建需要接收方持有相同数据内容。也可以直接消费 Bundle 中已包含的 source_turns，无需为读取与检索重新构建。

## 离线预检查

在仓库根目录、已安装 `requirements.txt` 的 Python 环境中执行：

```powershell
python -m src.memory.devbuild --data cache/public_locomo10.json --source-kind public --preflight
```

此模式读取四个指定开发集、验证文本与时间字段、统计分块并记录配置及数据哈希，不调用 API、不生成 MemoryBundle。构建输入仅包含白名单对话字段，不含 QA 标准答案、gold evidence 或生成式总结。

## 构建与复现操作

以下命令用于复现。真实运行采用公开数据命令；课程数据命令仅供取得课程文件后使用。密钥只放本机 `.env`，不写入运行记录、不进入 Git。已有本次缓存时，优先使用后文 `--cache-only`，无需再次请求 API。

课程数据构建命令：

```powershell
python -m src.memory.devbuild --data locomo.json --source-kind course
```

本次实际运行使用现有公开数据：

```powershell
python -m src.memory.devbuild --data cache/public_locomo10.json --source-kind public
```

仅构建 `conv-26`、`conv-30`、`conv-41`、`conv-42`。默认每块最多 20 轮、12000 字符，不跨会话；最大输出 8192 token，温度 0，关闭 thinking，最多尝试 3 次。模型沿用本地配置，程序会记录请求模型名和 API 实际返回的模型名；服务未提供模型快照版本时不能声称已固定底层权重版本。

输出位置：

- `outputs/milestone5-b/memory/conv-*.json`：四个经过 schema、来源引用及保存/加载一致性校验的 MemoryBundle。
- `outputs/milestone5-b/runs/<run_id>/manifest.json`：命令参数、输入文件 SHA-256、清洗输入哈希、配置及配置 ID、prompt 版本与哈希、build_id、Python 版本、耗时、API 尝试记录和缓存复用结果。
- `outputs/milestone5-b/runs/<run_id>/conv-*-review.json`：质量抽查候选，包含 Memory、原文证据和待填写的审阅字段。
- `cache/extractions/`：验证通过的抽取分块缓存；失败或不完整的模型输出不写入此缓存。

每次运行生成新的日志目录，避免覆盖先前的 token 和耗时记录。Memory 输出路径保持固定以便交接，变更配置重新构建前应归档旧产物。以上产物由仓库现有规则忽略，不随普通 Git 提交分发；交接时应将四个 JSON 和对应运行记录单独打包交给 A/C/D/E。

## 缓存与 token 统计口径

缓存键包含白名单输入、请求模型、prompt、配置 ID、API 地址及输出 token 上限。输入或抽取配置改变会产生新缓存。保留 `cache/extractions/`，中断后用相同参数重跑即可复用已完成分块。缓存损坏时明确报错，不静默转为付费请求。

每个成功构建会自动再执行一次 `cache_only=True` 重建，比较完整 MemoryBundle 是否一致；此模式发现缺失分块立即失败，代码不会调用 API。也可在后续单独检查：

```powershell
python -m src.memory.devbuild --data locomo.json --source-kind course --cache-only
```

`reported_total_tokens` 汇总本次运行所有 API 尝试返回的 `total_tokens`，包括输出截断或校验失败的响应。`usage_missing_attempts` 非零时，汇总只是已知 token 数，不能视为完整费用账单。缓存命中没有本次 API token 消耗，也不代表历史构建免费；应保留首次运行记录。构建耗时包括重试等待，缓存重建耗时另行记录。

构建成功仍显示 `builds_complete_review_pending`，不会自动宣称质量验收完成。失败记录保留错误类型及可用的 HTTP 状态，不记录密钥、请求头或响应正文。

## 质量抽查方法（已执行）

每个对话自动选取人物、已知事件日期、时间表达、未知时间及同实体/属性历史变化的候选，并保留完整引用证据。候选筛选不等于语义判断；其中“历史变化”也不是 C 负责的状态关系人工标签。

对每个对话至少覆盖以下适用类别，必要时补选样本及前后原文：

1. 人物：发言者及代词归属是否有证据，是否把另一人的经历归给当前说话人。
2. 事件：事实是否由证据支持，计划是否误写成已发生事件。
3. 状态：同一实体的新旧状态是否分别保留，是否漏掉更新或把不同属性混为一谈。
4. 时间：明确日期是否正确；相对日期是否基于会话时间合理解析；无法确定的时间是否保持 null。不能直接把 session_time 当作事件日期。

抽查 JSON 已填写审阅方式、逐条结论、理由和问题。错误案例包含 conversation_id、build_id、memory_id、来源轮次、预期解释。样本以相对日期和未知日期为主，不能据此推定所有明确日期场景正确。自动 schema 校验只能证明结构与引用合法，不能证明事实正确。

## 本轮验证

```powershell
python -m pytest tests/test_memory_devbuild.py tests/test_memory_pipeline.py tests/test_reasoning_context.py tests/test_vector_retrieval.py -q
python -m ruff check src/memory tests/test_memory_devbuild.py
python -m ruff format --check src/memory/build.py src/memory/extract.py src/memory/devbuild.py tests/test_memory_devbuild.py
```

本轮相关测试 58 项通过，所改文件 Ruff 检查与格式检查通过。新增测试覆盖缓存缺失时禁止联网、被拒绝响应的 token 计入、四个构建的 Mock 流程、零请求缓存重建及预检查不生成 Bundle。真实 API 验收另见上述运行结果。未声称全仓库测试通过；安排中 C 负责的 `test_judge_pair_deduplicates_sources` 仍需补 Mock，故未执行可能触发网络请求的整套推理测试。
