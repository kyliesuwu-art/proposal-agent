# 方案知识库

这是一个面向电气工程历史方案的内部知识库 Demo。它使用 MinerU 解析文档、Chroma 保存
页级检索数据，并通过 DashScope Qwen 生成带来源提示的方案初稿。

主入口是 `src/main.py`：

```powershell
uv sync --group dev
Copy-Item .env.example .env
uv run python src/main.py status
uv run python src/main.py ingest "<文件或目录>"
uv run python src/main.py annotate "<已入库源文件名>"
uv run python src/main.py query "<需求描述>"
uv run python -m pytest tests
```

`.env` 至少需要 `MINERU_TOKEN` 与 `DASHSCOPE_API_KEY`。`DASHSCOPE_BASE_URL`、企微变量和
运行路径变量见 `.env.example`。

运行数据默认位于项目根目录：`chroma_db/`、`files/`、`images/`、`debug_zips/`。它们包含
本地数据库或客户资料，不应提交 Git。路径可通过环境变量覆盖，且默认不依赖启动目录。

当前仍不稳定或未完成的部分包括：方案类型精确过滤、真实入库的事务性替换、图片描述质量、
Markdown 文件持久化，以及企微业务接入。不要把 `src/test.py` 当作自动化测试；它是联网的
RAGAS 候选评测集生成脚本。
