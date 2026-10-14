# Memory 与检索接口

本文定义职责 B 向 C（推理）、D（Web）、E（评测）提供的数据契约，当前版本为 `0.1`。2026-10-04 已实现共享类型、LoCoMo 读取、JSON 读写、逐轮基线、DeepSeek 抽取入口和 BM25 检索。按负责人授权直接推进实现，接口调整通过后续版本记录，不以组员确认为实现前置条件。

类型定义见 `src/common/models.py`，构建入口见 `src/memory/build.py`，证据查询见 `src/memory/store.py`，检索见 `src/index/bm25.py`。现已增加 MiniLM 向量检索、向量缓存和 RRF 融合，Memory JSON schema 保持 `0.1`。加载器拒绝旧版 `0.1-draft`，旧草案文件须按当前结构重新导出。

C 的状态解析输出和课程答案输出由对应负责人另行定义；本文只规定其与 Memory ID、构建版本和证据的衔接，不预先决定推理算法或 HTTP 路由。

## 数据对象

所有对象均可序列化为 UTF-8 JSON。存储对象的表中键均应存在，只有类型明确允许的字段可使用 `null`；检索请求的可选字段可省略并采用默认值。空集合使用 `[]`。时间不依据运行机器的时区自动转换。

### MemoryBundle

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema_version` | string | 当前为 `0.1` |
| `conversation_id` | string | 数据源的对话标识，不使用列表位置代替 |
| `build_info` | BuildInfo | 构建信息与版本 |
| `source_turns` | SourceTurn[] | 完整构建时包含该对话所有可用文本轮次，保留原始顺序 |
| `memories` | Memory[] | 结构化记忆；合法空对话可为空列表 |

### BuildInfo

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `build_id` | string | 当前构建的唯一版本，索引和实验记录必须匹配 |
| `method` | string | `llm_extraction`、`turn_baseline` 或人工示例 `manual_course_slide_example` |
| `input_sha256` | string 或 null | 清洗后的源轮次序列哈希；人工示例为 null，真实构建须为 64 位十六进制串 |
| `extractor_config_id` | string | 抽取配置版本，包括分块与时间规范化策略 |
| `model` | string 或 null | 实际模型标识；人工示例为 null |
| `prompt_version` | string 或 null | 实际抽取 prompt 版本；人工示例为 null |
| `note` | string 或 null | 示例声明或构建备注，不进入模型或检索文本 |

哈希序列按原始会话与轮次顺序保存，JSON 使用 `ensure_ascii=False, sort_keys=True, separators=(",", ":")` 后编码为 UTF-8 再计算 SHA-256。浮动的构建时间和 QA 标签不参与输入哈希。该约定只用于清洗后的 SourceTurn 列表。

### SourceTurn 与 SourceRef

| SourceTurn 字段 | 类型 | 含义 |
| --- | --- | --- |
| `conversation_id` | string | 必须与 bundle 相同 |
| `session_id` | integer | 来源会话编号，正整数 |
| `turn_id` | string | 保留原始轮次标识，例如 `D19:1` |
| `speaker` | string | 原始说话人 |
| `session_time` | string 或 null | ISO 8601 会话日期或日期时间；未知为 null |
| `text` | string | 原始文本内容，过滤图片链接；保留原语言及其余措辞 |

SourceRef 只含 `session_id` 和 `turn_id` 两个字段，从所在 Memory 继承 `conversation_id`。每个引用必须对应 bundle 中唯一的 SourceTurn；评测证据键为 `(conversation_id, session_id, turn_id)`。读取来源不把生成摘要当作原始文本。

### Memory

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `memory_id` | string | bundle 内唯一且持久化后稳定的 ID，不依赖数组当前位置 |
| `conversation_id` | string | 必须与 bundle 相同 |
| `content` | string | 单个可验证事实的自然语言描述，非空 |
| `entity` | string 或 null | 事实主体；无法确定时为 null，不猜测代词指向 |
| `attribute` | string 或 null | 属性或事件类别，如 `adoption_status`；无法归类时为 null |
| `value` | string 或 null | 属性值或事件描述；无法拆分时为 null，但保留 content |
| `session_time` | string 或 null | sources 中最后一个来源轮次的会话时间；依原始顺序确定，未知为 null |
| `time_expression` | string 或 null | 事件时间原文，如 `last Friday`；无明确表达时为 null |
| `event_time` | string 或 null | 可确定的日期 `YYYY-MM-DD` 或 ISO 8601 日期时间；不确定为 null |
| `event_time_precision` | string | `day`、`datetime` 或 `unknown`；与 event_time 类型精度一致 |
| `sources` | SourceRef[] | 非空、去重，按原始顺序排列，支持一条事实来自多个轮次 |

一条 Memory 表达一个主要实体的一项事实；复杂关联先保留在 content 和证据中，关系图属于后续扩展。来源文本与 Memory 内容不必逐字一致，但事实必须得到来源支持。抽取新增事实时不覆盖旧状态。

`event_time=null` 当且仅当 `event_time_precision=unknown`。对“去年”等超出当前日期精度的表达保留原文、日期留空，未来扩展时间区间时升级 schema。无时区输入保留无时区形式，不将本地时区当作数据时区。

Memory 不含 gold evidence、答案、查询关系、全局有效性或向量。状态解析结果属于一次查询，须记录 `build_id` 及所引用的 Memory ID；C 负责具体格式。示例完整文件见 [memory-example.json](memory-example.json)。

## 函数接口

以下函数均已实现；类型名对应本文的数据对象，`Path` 为 `pathlib.Path`。使用方法见 [源码说明](../src/README.md)。Memory ID 基于事实内容与来源的哈希生成，build_id 同时绑定输入、配置和完整抽取结果。

```python
def build_memories(
    data_path: Path, conversation_id: str, *, config: BuildConfig
) -> MemoryBundle: ...

def save_memories(bundle: MemoryBundle, path: Path) -> None: ...

def load_memories(path: Path) -> MemoryBundle: ...

def build_index(
    bundle: MemoryBundle, *, config: IndexConfig | None = None, encoder: Encoder | None = None
) -> MemoryIndex: ...

def search_memories(
    index: MemoryIndex, request: RetrievalRequest
) -> RetrievalResult: ...

def get_memory(bundle: MemoryBundle, memory_id: str) -> Memory: ...

def get_source_turns(
    bundle: MemoryBundle, sources: list[SourceRef]
) -> list[SourceTurn]: ...

def list_related_memories(
    bundle: MemoryBundle, *, entity: str, attribute: str,
    exclude_ids: list[str], limit: int = 20
) -> RelatedMemoryResult: ...
```

- `BuildConfig` 包含 mode（deepseek/turns）、model、chunk_turns（20）、chunk_chars（12000）、cache_dir、timeout（60 秒）、max_attempts（3）、max_tokens（4096）。config_id 自动计算，prompt 版本由抽取模块维护；密钥从本地环境获取，不写入 bundle。
- `IndexConfig` 包含 mode（bm25/vector/hybrid，默认 bm25）、k1=1.5、b=0.75、vector_cache_dir（indexes）、model_cache_dir（cache/embedding_models）、embedding_device（cpu/cuda/mps，默认 cpu）及 local_files_only（默认 false）。candidate_k 属于每次请求。MemoryIndex 复制并绑定单个 bundle 快照；更新 Memory 后重建索引，或用 `index.validate_bundle(bundle)` 检查一致性。
- 以 vector 或 hybrid 建索引时加载同一套向量资源，可执行三种查询；以 bm25 建索引时只允许 BM25 查询。Encoder 是具有 metadata 和 encode 方法的协议，用于显式注入测试编码器；正常调用使用默认的固定版本 MiniLM。向量缓存绑定模型、运行库版本、设备、构建 ID、完整 bundle 内容及 Memory ID 顺序，损坏缓存须移除后重建。
- `save_memories` 校验后原子写入 UTF-8 文件；`load_memories` 校验 schema、ID 和来源完整性，拒绝不兼容版本。
- `get_source_turns` 按请求顺序返回去重后的轮次，引用无效时报错，不静默跳过。D 的按 ID 查询先调用 get_memory 再读取其 sources。
- `list_related_memories` 对规范化后的 entity 和 attribute 做精确匹配，按来源先后返回历史，排除已知 ID；不判断是否有效。结果为 `{memories: Memory[], truncated: bool}`，limit 为正整数。超过 limit 时返回最早的 limit 条并标记截断，调用方不得把截断结果视为完整时间线。

## 检索请求与响应

### RetrievalRequest

| 字段 | 类型与默认值 | 语义 |
| --- | --- | --- |
| `conversation_id` | string，必填 | 必须与索引一致 |
| `query` | string，必填 | 非空自然语言问题；不包含 gold answer/evidence |
| `top_k` | integer，默认 10 | 正整数，最终唯一 Memory 的最大数量 |
| `candidate_k` | integer，默认 30 | 正整数且不小于 top_k，各通道的候选上限 |
| `mode` | string，默认 `bm25` | `bm25`、`vector`、`hybrid` |
| `entity` | string 或 null，默认 null | 可选精确匹配过滤；由 C 提供，不自动调用模型分析 |
| `attribute` | string 或 null，默认 null | 可选精确匹配过滤；无可靠值时不限制 |

三种模式均已实现。过滤先于各通道截取 candidate_k 执行，不能先从全库取 K 条再丢弃其他对话。不对时间做硬过滤，由 C 处理历史问题。BM25 分词为英文小写词/数字及单个 CJK 字符，无词项交集时返回空 hits；vector 按 content 的余弦相似度取候选，不设正分阈值。没有符合过滤条件的记忆时，两路都返回空结果。

### RetrievalResult

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `conversation_id`、`build_id` | string | 结果的对话与构建版本 |
| `query` | string | 实际检索问题 |
| `mode` | string | 实际执行模式，不能将单路降级伪装成 hybrid |
| `hits` | RetrievalHit[] | 有序、唯一 Memory 列表，长度不超过 top_k |
| `trace` | object | 请求参数和各通道候选数；向量模式另含 embedding 元数据、vector_cache 路径与 fusion（hybrid 为 rrf-k60，否则 null） |

RetrievalHit 包含 `rank`（从 1 开始的连续整数）、`memory`（完整 Memory）、`score`（有限数值）和 `channel_scores`。后者为通道名到 `{rank: int, score: number}` 的映射；未命中的通道不出现。原始通道分数只用于诊断，混合结果的 score 使用 [设计说明](memory-design.md) 中的 RRF 公式。

按 score 降序排序；相同分数按 memory_id 的字典序升序稳定排序，单路候选也遵循该规则。source 由 hit.memory.sources 提供，不另造与它不一致的证据字段。JSON 中不得出现 NaN 或 Infinity。

下面为 hybrid 响应的简化形状示例，数值为手工构造；省略了 trace 中的模型元数据，Memory 正文引用示例 ID，真实接口在该位置返回完整对象：

```text
conversation_id: conv-26
build_id: course-slide12-example-v1
query: What progress has Caroline made toward adoption?
mode: hybrid
hits:
  - rank: 1
    memory: <完整的 conv26_D19_1_001 Memory 对象>
    score: 0.03278688524590164  # 1/(60+1) + 1/(60+1)
    channel_scores:
      bm25: {rank: 1, score: 3.2}
      vector: {rank: 1, score: 0.81}
trace:
  top_k: 5
  candidate_k: 30
  entity: null
  attribute: null
  channel_candidates: {bm25: 1, vector: 1}
```

关联历史由 C 在初始检索后显式请求，单列保存，不偷偷插入初始 hits。E 先计算初始 Top-K 指标，如评估扩展后结果则另行标注；任何摘要视图不得直接混入按 Memory 计数的排名。

## 空结果与错误

| 情况 | 约定行为 |
| --- | --- |
| 合法空对话或已建索引中无候选 | 返回空 memories 或 hits，保留版本与 trace |
| 空查询、非正 K、candidate_k 小于 top_k | `ValueError` |
| 数据路径不存在 | `FileNotFoundError` |
| 对话、Memory ID 或来源不存在 | `KeyError`，不伪装成合法空对话 |
| schema 不兼容、重复 ID、跨对话来源、来源缺失 | `ValueError` 并指明字段或 ID |
| 索引与请求对话不符 | `ValueError` |
| 索引未建好或与 bundle 的 build_id 不符 | `RuntimeError`，重建后再检索 |
| 所请求通道未配置、embedding 调用失败 | 明确报错；如需改跑 BM25，由调用方显式发起新请求 |
| 单条记忆或问题超过 embedding 模型 token 上限 | `ValueError`，拆短文本后重试，不静默截断 |
| 向量缓存校验失败或向量维度、数值非法 | `ValueError`，不使用损坏缓存 |
| 抽取失败或响应结构不合法 | 有界重试后报错，记录失败块，不能输出假成功的完整构建 |

## 后续对接事项

- B 与 A：真实 LoCoMo 字段映射、构建版本规则、源码共享类型位置和依赖选择。
- C：entity/attribute 约定、是否需要关联历史接口、查询关系输出如何引用 build_id 与 Memory ID。
- D：来源读取接口能否支持记忆详情与原文展示，HTTP 路由由 D 定义。
- E：课程 evidence 如何映射到 SourceRef，Recall@K 的正式口径，初始检索与扩展检索分开记录。

这些事项记录后续对接需要，不阻塞当前 `0.1` 实现。课程对话划分及正式评测输出仍以课程通知为准；实体别名和 C 的状态输出不在本次实现中。向量模型的实际召回效果需由开发集评测验证。
