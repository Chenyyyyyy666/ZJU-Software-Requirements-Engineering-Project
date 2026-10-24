# B 阶段交付与验收记录

记录日期：2026-10-14（北京时间）｜负责人：鲁瑞特（B）。

本文件是本次工作记录，不是历史会议纪要，也不是消费方审批或课程提交回执。按组长最新通知，SRS 在 10/16 前完成，基础前后端在 10/21 前完成，设计编码测试文档在 10/23 前补全。

范围确认：B 负责人已明确本轮暂不开展真实 API 测试。因此本轮交付为 SRS 供稿、接口实现与离线/Mock 验证；API 密钥和课程数据不是本轮交付的前置条件。真实抽取质量和正式数据验收保留为后续工作，尚未执行，不标为本轮阻塞。

## 本次范围与来源

以远端主干 `4615553`（2026-10-14 的 SRS 骨架）为基础，工作分支为 `feat/b-oct16-deliverables`。拉取远端后已看到里程碑 1、2 的归档 PDF，以及 `v0.1-milestone1`、`v0.2-proposal` 标签。此前本地分支落后，不能据本地缺少 PDF 判断团队未提交；本次未查看邮件提交回执。

## 交付清单

| 项目 | 当前状态 | 证据或下一步 |
| --- | --- | --- |
| Proposal 数据方案、Memory 结构、依赖与代码规范 | 文件已有，本次复核 | `docs/proposal.md`、`memory-design.md`、`interfaces.md`、`coding-standards.md`、依赖文件 |
| SRS 的 B 章节 | 已完成本轮供稿 | `docs/srs.md`：FR-01–FR-07、NFR 数据/检索要求；其他负责人章节仍待补，不能声称全组 SRS 已定稿 |
| 标注隔离约束澄清 | 已修改供评审 | 离线构建禁止 QA 标注；在线检索/推理允许当前待答问题，仍禁止标准答案和其他标签 |
| 中期设计与测试 B 章节 | 已提前供稿 | `docs/design-and-test.md`，含设计取舍、错误处理和需求测试追踪 |
| C/D/E 的接口交接入口 | 实现及消费契约测试已通过 | `src/index/context.py`、`tests/test_reasoning_context.py`、`docs/b-handoff.md` |
| 真实对话读取、JSON 导出、检索、证据回溯 | 公开数据逐轮基线已验证 | 419 轮 conv-26，详见下表；不等于真实语义抽取 |
| 真实 DeepSeek 语义抽取 | 后续工作，本轮不执行 | 按 B 负责人确认暂不做真实 API 测试；届时配置密钥并核对事实与时间，不阻塞当前供稿 |
| 课程数据验收 | 后续工作，本轮不执行 | 正式测试时取得课程数据及对话划分；公开 conv-26 不标作课程开发集 |
| C 实际推理模块联调、接口确认 | 尚未完成 | 当前仓库无 `src/reasoning/`；已有可运行消费入口，需对方实际接入并留下结果 |
| 第 1 周会议纪要 | 缺原始事实，未补造 | process 中未发现当周纪要；真实时间、参会人、决议和截图需由当事人提供 |
| 跨平台统一 | 已提供依赖与复现步骤，未全验收 | 本次仅 Windows / Python 3.13.5；Python 3.11/macOS 尚未执行，A 负责整体验收 |

本次没有代替他人签署确认、发送消息、提交课程材料，或把历史未记录的会议写成已发生事实。

> 2026-10-24 更新：课程随后已确认 `conv-26`、`conv-30`、`conv-41`、`conv-42` 为开发集，并从其余 6 段中任意选取 2 段作为独立测试集；上文“本轮不执行”和“不标作课程开发集”的描述保留为当时的交付状态。

## 自动化验证

在当前 Windows 虚拟环境执行以下等价 Python 命令：

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

测试结果：**54 passed**；ruff 检查通过。新增 7 个参数化展开后的测试覆盖初始 Top-K 保持、旧状态/历史截断、全部原文可回溯、返回对象与存储隔离、非法历史参数、跨对话拒绝，以及保存 JSON 后的消费 CLI。

模型单元测试使用 Mock 或测试编码器；不把测试中的假响应当真实抽取。真实模型验证如下。

## 公开对话与真实向量模型验证

- 数据：本机已有的 `cache/public_locomo10.json`，对话 `conv-26`；419 个可用文本轮次。
- 构建：`turn_baseline`，419 条 Memory；没有人物/事件/状态语义抽取。
- 构建 ID：`5aa0edee7c6dc01da2268fea294105f44b7b96e7ddce6462aa00474a86d7b8ae`。
- 环境：Windows，Python 3.13.5，CPU；sentence-transformers 5.1.2、torch 2.8.0、transformers 4.57.1。
- 编码器：MiniLM，revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`，384 维，`local_files_only=True`。
- 查询：`adoption interviews`，top_k=5、candidate_k=30。
- 所有模式的每条命中均在交接对象中找到原始来源；每模式连续执行 30 次，返回对象一致。

| 模式 | 命中数 | 来源轮次数 | D19:1 排名 | 暖机后查询 p50 / p95（毫秒） |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 5 | 5 | 1 | 0.28 / 0.30 |
| vector | 5 | 5 | 3 | 26.97 / 28.98 |
| hybrid | 5 | 5 | 1 | 27.79 / 33.73 |

计时方式：先建立索引、每种模式运行一次，再以 `perf_counter` 计时 30 次 `retrieve_context`；p50 为中位数，p95 为排序后第 29 个值。建索引耗时约 0.275 秒，复用了已有向量缓存，不含首次下载、模块导入和查询模型首次加载，不能视为冷启动或首次全量编码耗时。以上是单机、单对话、单查询的工程烟雾验证，不是正式性能保证或 Recall@K/F1/状态准确率。

本机完整产物（已忽略，不入库）：

- `outputs/oct16/memory/conv-26-turns.json`
- `outputs/oct16/context-bm25.json`、`context-vector.json`、`context-hybrid.json`
- `outputs/oct16/verification.json`

复现构建与证据导出（需本机已有公开数据；不需 API 密钥）：

```bash
python -m src.memory.build --data cache/public_locomo10.json --conversation conv-26 \
  --mode turns --output outputs/oct16/memory/conv-26-turns.json
python -m src.index.context --memory outputs/oct16/memory/conv-26-turns.json \
  --query "adoption interviews" --mode hybrid --top-k 5 --local-files-only \
  --output outputs/oct16/context-hybrid.json
```

## 后续真实验收与团队协作

1. 后续进入真实测试阶段时，再配置有效密钥并取得课程数据及可用对话 ID，按 `docs/b-handoff.md` 执行真实抽取。当前无需配置密钥；FR-02 的需求与实现可交付，真实质量验收仍标为未执行。
2. 抽查真实 Memory 的来源支持性、人物/事件/状态和时间，不仅检查 schema。记录错误样本及修复结论。
3. C 用实际推理代码消费 context，确认构建身份与历史扩展；D/E 分别确认来源展示与证据口径。自动化消费测试不替代人或实际模块的确认。
4. E 合并所有成员的 SRS 章节并压缩到课程页数要求；本次完成的是 B 的供稿。

本轮约定的 B 文档、接口代码和离线验证已完成。真实测试按负责人安排留到后续；消费方确认、全组 SRS 定稿及历史纪要仍需对应事实和团队协作，不能据本轮完成推定这些事项也已完成。
