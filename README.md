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
uv run python src/main.py query "<需求描述>" --output outputs/query-result.md
uv run python -m pytest tests
```

## V2 独立测试库与混合检索

旧 `ingest`、`query` 与 `chroma_db/` 保持原样。V2 只能通过名称明确的命令使用，并且
强制指定样例来源根目录和一个可删除的测试库目录。例如：

```powershell
uv run python src/main.py ingest-v2 "samples/项目A/方案.pdf" --source-root samples --test-db v2_test_db
uv run python src/main.py query-v2 "PCS 35kV 100MW 参数" --test-db v2_test_db
```

`v2_test_db/` 内包含独立的 `chroma_v2/` 语义索引和 `hybrid_lexical.sqlite3` 词法索引；
确认不再需要时可直接删除整个该目录。它绝不会读取、写入或删除旧 `chroma_db/`。

V2 的 embedding 使用干净正文 `retrieval_text`；页面存储则分开保存标题/章节、正文、
context 摘要、keywords、entities、parameters 与文档身份。关键词和实体完全由本地规则提取：
标题术语、PCS/BESS/SVG/STATCOM/EMS/SCADA 等小型可维护同义词表、电压等级、
MW/MWh/kW/kWh/kvar 参数，以及保守的型号和“项目/工程/电站/园区”名称。未知词保留原文。

检索同时取得 Chroma 语义候选和 SQLite FTS5 的精确词法候选，再使用 `RRF(k=60)` 合并。
命中会在 `SearchHit.metadata.retrieval` 中保留语义距离/分数、词法命中词、各自原因和最终
融合原因。词法路径对精确设备、型号、电压和容量问题更强；它不是行业知识理解，也不替代
人工复核或语义检索。V2 不调用 LLM 为页面抽取关键词或实体；但解析仍需 MinerU，语义检索仍需
DashScope embedding 配置。

`.env` 至少需要 `MINERU_TOKEN` 与 `DASHSCOPE_API_KEY`。`DASHSCOPE_BASE_URL`、企微变量和
运行路径变量见 `.env.example`。

运行数据默认位于项目根目录：`chroma_db/`、`files/`、`images/`、`debug_zips/`。它们包含
本地数据库或客户资料，不应提交 Git。路径可通过环境变量覆盖，且默认不依赖启动目录。

当前仍不稳定或未完成的部分包括：方案类型精确过滤、真实入库的事务性替换、图片描述质量、
以及企微业务接入。`query` 会在终端渲染 Markdown，并返回可供其他交付渠道复用的结构化结果；
指定 `--output <路径>` 时才写入 Markdown 文件。引用格式为 `[来源: 文件名, 第 页码 页]`，
以文件名和页码共同区分来源。不要把 `src/test.py` 当作自动化测试；它是联网的
RAGAS 候选评测集生成脚本。
