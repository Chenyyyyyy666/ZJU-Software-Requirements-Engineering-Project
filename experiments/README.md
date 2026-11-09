# experiments 目录说明

存放**实验配置与结果的记录**，用于支撑报告中的对照分析、最终 `Results` 表，以及答辩时的结论说明。

课程对准确率没有硬性要求，但要求把"怎么做的、效果如何、为什么"讲清楚。因此每次有意义的实验都要留痕：用了什么配置、跑了哪些对话、指标是多少、结论是什么。

## 目录结构

```text
experiments/
├── README.md                     # 本文件：目录说明、运行指引
├── dev_probe.py                  # 调试探查脚本，用于检查记忆、检索中间结果
├── evaluate_relations.py         # 关系判定评测主脚本
├── memory_pairs_gold.csv         # 人工标注真值文件（gold 标准）
├── relation_eval_results.csv     # 脚本输出：全部样本预测详情
├── relation-errors.csv           # 脚本输出：错误样本单独汇总
├── measure_retrieval_improvement.py # 旧/新检索 Recall@K 离线对照
├── retrieval-improvement.md      # 窗口兜底、简洁答案与冻结测试记录
├── splits.json                   # 数据集划分配置（开发集 / 测试集划分）
├── 错误案例分析报告.md             # 人工撰写：本轮错误分析文档
├──summary.md                      #总结实验结果
└── 2026-10-15-topk-comparison/  # 单次实验：日期 + 实验主题
    ├── config.md                # 实验配置
    ├── results.json             # 指标结果
    └── notes.md                 # 观察与结论
```

单次实验目录命名：`YYYY-MM-DD-<实验主题>`，例如 `2026-10-18-state-relation-ablation`。

## 运行方式

项目根目录使用 `venv` 虚拟环境

```bash
# 进入项目根目录
cd ZJU-Software-Requirements-Engineering-Project

# 激活虚拟环境（Windows）
.venv\Scripts\activate
```

运行探查脚本：构建记忆、生成记忆对、执行关系预测、缓存中间结果 

```
python experiments/dev_probe.py 
```

运行关系评测脚本，对比预测结果与gold标注，输出指标、混淆矩阵、错误表

```
 python experiments/evaluate_relations.py
```

## 每次实验记录什么

课程已公布的开发集为 `conv-26`、`conv-30`、`conv-41`、`conv-42`；独立测试集由课程从其余 6 段中任意选取 2 段，测试候选对话不得用于调参。

`config.md` 至少写清：

- 检索方式（关键词 / 向量 / 多路合并）与 top-K 取值
- 使用的模型与参数（DeepSeek flash，thinking 关闭）
- 涉及的对话编号（注明属于开发集还是自选的扩展集）
- 记忆构建方式，以及本轮是否复用已有构建结果

`results.json` 记录：

- 问答 F1、证据检索 Recall@K、动态状态更新判断准确率
- 平均 token 消耗与大致费用

`notes.md` 记录：

- 与上一次实验相比的差异
- 失败案例（哪类问题答错，原因是什么）
- 结论与下一步打算

## 对照实验要求

课程要求与两种基准方法对比：**Full Text**（把全文交给模型）与 **Vector RAG**（只检索、不做状态关系判断）。这两项不必自己实现，官方仓库中已有；但结果表要能横向比较三者的指标。

## 结果汇总表

`summary.md` 维护成一张横向对比表，每完成一轮实验追加一行：

| 日期 | 实验主题 | 检索方式 | top-K | QA F1 | Recall@K | 状态更新准确率 | 平均 token | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-15 | 检索方式对比 | 关键词 | 10 | 待填 | 待填 | 待填 | 待填 | 初版流水线 |
| 2026-11-09 | 五轮窗口兜底 | RRF 前三 + 原文 BM25 两槽 | 5 | — | Dev R@5 0.6815→0.7825；Test 0.7940 | 未启用 | 576.6 | `answer_v1` 消融 |
| 2026-11-09 | 简洁答案 `answer_v2` | 同上 | 5 | Dev 0.4325；Test 0.4738 | Dev R@5 0.7825；Test 0.7940 | 未启用 | 659.9 / 672.3 | 冻结后测试零失败 |

## 约定

- **负责人**：张崇洋负责跑批、记录与图表；实验对比的配置由于国庆决定。
- **成本控制**：记忆构建结果要复用，不要每轮实验都重建；先用少量对话跑通，再放开全量。
- **不提交**：API key、原始缓存大文件、数据集本身。缓存如需共享，先确认不含敏感信息。
- 本目录由张崇洋维护。
