# 开发协作说明

项目是电气工程方案知识库的内部 Demo：解析历史文档，写入本地 Chroma，检索后由
DashScope Qwen 生成带来源提示的方案初稿。共享项目事实以 [README.md](README.md)
和 [DECISIONS.md](DECISIONS.md) 为准；`CLAUDE.md` 保留历史架构与决策索引。

## 常用命令

```powershell
uv sync --group dev
uv run python src/main.py status
uv run python src/main.py ingest "<文件或目录>"
uv run python src/main.py query "<需求描述>"
uv run python -m pytest tests
```

除非任务明确要求，禁止运行 `ingest`、`annotate`、`query`、批量重入库、企微机器人或
RAGAS/DeepEval：它们可能调用付费服务、发送客户资料或写入真实运行数据。

## 数据与 Git 规则

- 不提交 `.env`、客户源文件、提取图片、Chroma 数据库、数据库备份、调试输出、日志或
  自动生成的评测结果。
- 测试只能使用 pytest 临时目录，绝不能连接项目根目录、`src/` 或备份目录中的 Chroma。
- 禁止使用 `git clean`、`git reset --hard` 或其他会清理未提交工作的破坏性 Git 命令，
  除非用户明确要求。
- 修改路径、文档 ID、Chroma ID、入库删除逻辑或解析逻辑前，先阅读相关决策并添加离线测试。

## 路径约定

运行时默认路径由 `src/config.py` 从项目根目录解析，并可用环境变量覆盖。不得再新增依赖
当前工作目录的运行数据路径。
