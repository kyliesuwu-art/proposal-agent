# 方案知识库

内部电气工程方案知识库项目。系统基于本地混合检索库和用户需求，生成带页码来源的单一 Markdown 方案；DOCX/PPTX 由独立本地渲染器消费已验证的 Markdown，不能反向决定内容。当前仍处于受控验证阶段，尚未完成正式环境验收。

## 当前状态

- 当前唯一有效测试库：`runtime_data/word_test_db`。
- 该库包含 10 份来源文档、214 条索引页、SQLite FTS5 和 Chroma collection `electrical_pages_v2`。
- `v2_test_db`、`v3_candidate_db`、`v3_candidate_db_rebuilt` 已删除，不能作为默认路径。
- 单 Markdown proposal V1 已完成离线测试并进行过受控真实 DashScope 尝试；失败运行不发布 Markdown 或来源侧车。

## 架构概览

```text
用户需求 → LLM 规划大纲 → retrieve_evidence（Chroma + FTS5 + RRF）
→ LLM 逐章写作 → 同一个 proposal.md → 全文审查 → 定向修订
```

`proposal.md` 是唯一内容源。大纲、证据映射和审查意见只保存在内存；不会创建 `plan.json`、`audit.json`、章节文件或 draft/final 双文件。引用、来源页和图片路径由代码映射，不能由 LLM 编造。

## 常用离线验证

```powershell
uv sync --locked --group dev
uv pip check
uv run python -m pytest tests -q
uv run python src/main.py --help
```

提交和拉取请求会在 Windows 上自动执行锁定依赖安装、依赖兼容性检查、语法检查、命令入口检查和完整离线测试。两项真实 DashScope 测试默认禁用；自动检查没有外部服务密钥。

正式运行前应显式配置 `RAG_DB_PATH` 指向经过授权的目标库，并核对该路径。代码默认值 `runtime_data/word_test_db` 仅供本项目当前受控验证使用，不应当作正式环境配置。`src.config` 导入时会加载仓库根目录的 `.env`；修改环境变量后须重启进程。

以下命令可能调用外部服务或读取真实库，必须获得明确授权后才能运行：`proposal`、`query`、`ingest`、`ingest-cache-dir`、`annotate` 以及外部评测命令。

`scripts/ark_quickstart.py` 仅用于 Ark Managed Agents 连通性验证，不属于 DashScope proposal 生成链路。

真实 proposal 的形式为：

```powershell
uv run python src/main.py proposal "需求描述" --output outputs/proposal.md
```

统一入口 `generate` 会先生成同一份 Markdown，再按请求渲染 Office 文件：

```powershell
uv run python src/main.py generate "需求描述" --formats md docx pptx --output-dir outputs/新的空目录
```

详细操作规则见 [AGENTS.md](AGENTS.md)，架构决策和变更记录见 [DECISIONS.md](DECISIONS.md)。
