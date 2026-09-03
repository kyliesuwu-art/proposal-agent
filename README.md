# 方案知识库

内部电气工程方案知识库实验项目。系统基于本地混合检索库和用户需求，生成带页码来源的单一 Markdown 方案；当前目标是验证内容、引用和审查质量，DOCX 渲染留待后续独立阶段。

## 当前状态

- 当前唯一有效测试库：`runtime_data/word_test_db`。
- 该库包含 10 份来源文档、214 条索引页、SQLite FTS5 和 Chroma collection `electrical_pages_v2`。
- `v2_test_db`、`v3_candidate_db`、`v3_candidate_db_rebuilt` 已删除，不能作为默认路径。
- 单 Markdown proposal V1 已有离线实现和测试；尚未进行真实外部服务生成验收。

## 架构概览

```text
用户需求 → LLM 规划大纲 → retrieve_evidence（Chroma + FTS5 + RRF）
→ LLM 逐章写作 → 同一个 proposal.md → 全文审查 → 定向修订
```

`proposal.md` 是唯一内容源。大纲、证据映射和审查意见只保存在内存；不会创建 `plan.json`、`audit.json`、章节文件或 draft/final 双文件。引用、来源页和图片路径由代码映射，不能由 LLM 编造。

## 常用离线验证

```powershell
uv sync --group dev
uv run python -m pytest tests -q
uv run python src/main.py --help
```

以下命令可能调用外部服务或读取真实库，必须获得明确授权后才能运行：`proposal`、`query`、`ingest`、`ingest-cache-dir`、`annotate` 以及外部评测命令。

真实 proposal 的形式为：

```powershell
uv run python src/main.py proposal "需求描述" --output outputs/proposal.md
```

详细操作规则见 [AGENTS.md](AGENTS.md)，架构决策和变更记录见 [DECISIONS.md](DECISIONS.md)。
