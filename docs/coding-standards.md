# Python 代码规范

适用于 B 的数据、记忆与检索模块以及后续共享代码。项目选择 Python 3.11 作为协作基准（见 `.python-version`），使用虚拟环境；直接依赖固定在 `requirements.txt`，升级时附验证结果。

## 数据与接口

- 共享数据对象放在 `src/common/models.py`，使用类型标注和 Pydantic 校验；未知字段拒绝，允许未知的值显式写为 null。
- 持久化与检索保持 `conversation_id`、`build_id` 和来源轮次一致。修改数据结构同步更新 `docs/interfaces.md`，不静默改变旧字段含义。
- 文件路径使用 `pathlib.Path`，文本读写显式指定 UTF-8。JSON 使用标准有限数值，持久化先校验再原子替换。
- 函数保持模块边界：B 返回候选与证据，C 判断查询相关状态，D 提供 HTTP 路由，E 计算指标。

## 配置与异常

- 密钥只从环境变量或本地 `.env` 获取，不写入源文件、输出 JSON、缓存或异常文本。
- CLI 加载 `.env`，已有环境变量优先；库函数不隐式读取 `.env`，由调用方负责配置。
- 模型调用设置超时与有界重试；认证等永久性错误立即返回，瞬时失败或不合规输出才重试。
- 抽取失败保留已验证块的缓存并报出失败块编号，不保存完整成功结果。错误信息不包含 HTTP 响应正文或密钥。
- 时间无法识别时显式警告并保留未知值，禁止填入当前日期。模型输出能通过字段验证，不代表事实与时间推导已被人工证实。

## 检查与测试

在仓库根目录、虚拟环境激活后运行：

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

测试数据使用人工合成样例，真实课程数据不入库。模型测试使用 `httpx.MockTransport` 或显式 mock，不发起真实 API 请求。重点验证数据隔离、来源可追溯、失败行为与跨模块链路；真实模型质量由单独实验评估。

## 命名与提交

文件和函数使用 snake_case，类使用 PascalCase，常量使用 UPPER_SNAKE_CASE。行宽 100，采用双引号，import 排序由 ruff 管理。Git 流程沿用 [git-conventions.md](git-conventions.md)；本地检查结果写入 PR 描述。
