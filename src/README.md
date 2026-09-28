# src 目录说明

项目源代码目录。整体数据流是：读取 LoCoMo 对话 → 构建结构化记忆 → 按问题检索并解析状态关系 → 生成答案 → 网页端展示与评测。

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

- **接口先定义再实现**：跨模块调用的数据结构与函数签名统一写在 `common/`，同时更新 `../docs/interfaces.md`；修改接口必须通知消费方，并在 Pull Request 中获得确认。
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
