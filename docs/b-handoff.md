# B 模块交接与验收说明

供稿：鲁瑞特（B）｜日期：2026-10-14｜接口版本：Memory 0.1 / Context 0.1。

> 2026-10-29：Milestone 5 已完成四开发集真实构建（951 条记忆）、缓存复验及 47 条定向质量抽查，详见 [B 最终阶段构建说明](milestone5-b-delivery.md) 与 [质量记录](milestone5-b-quality.md)。下文保留 10/14 阶段的交接记录，其中“本轮不执行”仅指当时阶段。

本说明供 C（推理）、D（Web）、E（评测）接入。它记录 B 已提供的接口和验收方式，不代替接收方确认。SRS 需求在 [srs.md](srs.md) 第 3.1、3.2 节，完整字段在 [interfaces.md](interfaces.md)，实际运行结果和未完成项在 [交付记录](../process/2026-10-14-b-delivery.md)。

## 1 环境与最短接入路径

从仓库根目录执行。以下为 bash/WSL 示例；WSL 与 Windows 必须使用各自的虚拟环境，不要跨系统复用 `.venv`。团队基准是 Python 3.11，向量依赖单独安装；WSL 可使用 `.venv-wsl` 并在本机 Git exclude 中排除该目录。

```bash
python3.11 -m venv /tmp/zju-memory-venv
source /tmp/zju-memory-venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-vector.txt  # 仅 vector/hybrid 需要
python -m pytest -q
```

离线合成数据验证（无需密钥）：

```bash
python -m src.memory.build --data tests/fixtures/locomo_synthetic.json \
  --conversation demo-01 --mode turns --output outputs/oct16/demo-memory.json
python -m src.index.context --memory outputs/oct16/demo-memory.json \
  --query "adoption interviews" --top-k 3 --output outputs/oct16/demo-context.json
```

`build_info.method=turn_baseline` 明确标识此演示没有语义抽取。人工结构化示例可用 `docs/memory-example.json` 替换输入文件，构建方式为 `manual_course_slide_example`，同样不能当作真实抽取结果。

## 2 C 的 Python 调用示例

```python
from pathlib import Path
from src.common.models import RetrievalRequest
from src.index import IndexConfig, build_index
from src.index.context import retrieve_context
from src.memory.store import load_memories

bundle = load_memories(Path("outputs/oct16/demo-memory.json"))
index = build_index(bundle, config=IndexConfig(mode="bm25"))
request = RetrievalRequest(
    conversation_id=bundle.conversation_id,
    query="adoption interviews",
    top_k=3,
    candidate_k=30,
    mode="bm25",
)
context = retrieve_context(index, request)
# C 的推理输入：context["retrieval"]["hits"] 和 context["source_turns"]。
# C 的输出应引用 context["retrieval"]["build_id"] 与命中中的 memory_id。
# 在同一对话的后续请求中复用 index；Memory 更新后重新建索引。
```

若 C 已明确 entity/attribute，可以设置请求过滤并传入 `history_limit=20`。未知实体或属性时保持 null，不加硬过滤。历史列表与初始 Top-K 分开，`truncated=true` 表示历史仍有未返回部分；现有接口可将已读 ID 累积到 `exclude_ids` 后继续读取。

实体/属性采用抽取结果中的原始字符串精确匹配；当前没有别名归并。相对事件时间未知时保留 time_expression，event_time 为 null，不能用会话时间替代真实事件时间。

## 3 后续真实抽取验收（本轮不执行）

B 负责人已确认当前暂不做真实 API 测试。本节保留后续操作步骤，不要求现在配置密钥，也不作为 10/16 本轮 SRS、接口与离线验证交付的前置条件。

在本机 `.env` 配置 `DEEPSEEK_API_KEY`，按团队实际可用模型设置 `DEEPSEEK_MODEL`。密钥只在本机使用。把课程数据放到 `locomo.json`，开发调试使用 `conv-26`、`conv-30`、`conv-41`、`conv-42`；课程会从其余 6 段中任意选取 2 段作为独立测试集，最终编号在课程通知前不得自行推定。

```bash
python -m src.memory.build --data locomo.json --conversation YOUR_CONVERSATION_ID \
  --mode deepseek --output outputs/oct16/course-memory.json
python -m src.index.context --memory outputs/oct16/course-memory.json \
  --query "YOUR_QUESTION" --mode hybrid --top-k 5 \
  --output outputs/oct16/course-context.json
```

首次向量运行可能下载固定 revision 的模型；已有模型时加 `--local-files-only` 验证离线运行。读取失败、非法响应和模型缺失必须保持失败状态，不切换到逐轮基线假装完成真实抽取。

验收需要保留：数据来源和对话 ID、构建 ID、输入哈希、模型/prompt/config 版本、命令与结果、每条命中对应原文。将构建结果再次保存/加载，确认一致；再次构建时应复用已验证块缓存，不能为演示反复调用模型。

语义质量单独抽查：人物归属、事件内容、新旧状态各保留、明确日期、相对日期、未知时间各选适用案例；不存在某类案例就记“不适用”。每例记录 Memory ID、来源、判断理由和错误。schema 校验只能证明结构与引用合法，不能证明事实或时间推导正确。不得预填“全部正确”。

本机已有公开 `cache/public_locomo10.json` 时，可将上述数据路径和对话 ID 替换为该文件和 `conv-26` 做公开数据烟雾验证；它不是随 Git 分发的文件，也不替代课程数据验收。

## 4 验收与交接清单

| 接收方 | 已提供 | 接收方需实际核对 |
| --- | --- | --- |
| C 于国庆 | Memory 字段、检索候选、证据、显式关联历史、构建版本 | 用其实际推理模块消费 context；核对实体/属性及输出引用，提交联调结果 |
| D 许子萌 | Memory 与原始轮次读取、可序列化 context | HTTP/页面能展示事实及来源，并保留构建身份和历史截断提示 |
| E 张崇洋 | SRS 第 3.1/3.2 节、非功能要求、测试与证据接口 | 将供稿并入终稿；确认 Recall@K 口径和正式评测，不将历史扩展混进初始 Top-K |
| A 陈易 | 依赖与版本、可运行 CLI、错误约定 | 课程数据/接口编号、跨平台环境、整体集成入口与提交打包 |

SRS 当前将“命中至少一条证据”的题级命中率命名为 Recall@K；技术文档区分这一口径与证据覆盖率。本次沿用 SRS 的课程口径，不在 B 模块计算或重命名指标，E 定稿时应注明分母和集合定义。

本轮无需 B 实现状态关系判断、答案生成、Web 页面或评测脚本。C 的正式联调、他人的审批和课程提交只能按实际记录登记，不能由本说明推定完成。
