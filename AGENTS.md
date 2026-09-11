# AGENTS.md

## 1. 项目目标

本项目基于本地知识库和用户需求，通过多次 LLM 调用生成并修订一份带证据引用的 `proposal.md`；后续再由独立、稳定的渲染器转换为 DOCX。当前是内部验证项目，不建设多用户系统、高并发服务或复杂编排平台。

## 1.1 项目仓库与本地路径

- GitHub 仓库：`https://github.com/kyliesuwu-art/proposal-agent.git`
- 本地项目实际路径：`C:\Users\comking\Desktop\方案智能体\Core`
- 后续 Codex 在本地完成代码修改并通过必要测试后，应立即提交本次相关改动并推送到 GitHub 当前分支 `origin`。
- 自动提交和推送时只包含本次 Codex 修改的文件；必须保留并避开用户已有的工作树修改。推送失败时必须报告原因，不能假装已同步。

## 2. 当前架构

- `src/markdown_proposal.py`：单 Markdown proposal V1 编排。
- `src/pipeline.py::retrieve_evidence()`：纯检索入口；执行 Chroma 语义检索、SQLite FTS5 词法检索、RRF 融合与 supporting pages 选择，不生成答案。
- `src/query_result.py`：`SearchHit`、`Citation`、`QueryResult` 传递结构化检索结果。
- `src/adapters/llm_client.py`：DashScope 文本调用封装。
- `src/main.py proposal`：唯一的真实方案生成 CLI；`src/render_word.py` 与 `src/render_pptx.py` 是仅消费已交付 Markdown 的独立下游渲染器。

LLM 负责大纲规划、分章写作、全文审查和命中章节的定向修订；确定性代码负责检索、证据 ID 校验、文件名/页码/图片路径映射与 Markdown 组装。

## 3. 当前唯一测试数据库

当前唯一有效的测试库是 `runtime_data/word_test_db`，包含 10 份来源文档、214 条索引页、`hybrid_lexical.sqlite3`、`chroma_v2/chroma.sqlite3` 和 collection `electrical_pages_v2`。

`v2_test_db`、`v3_candidate_db` 与 `v3_candidate_db_rebuilt` 已删除。未经用户明确要求，不得搜索、恢复或重建它们，也不得因旧 README、历史报告或对话仍提到它们而改变当前数据源。数据库变更时，必须同步更新 `src/config.py`、本文件、`DECISIONS.md` 与直接相关 README。

## 4. 数据与外部服务安全规则

- 不得擅自删除、移动、重建、覆盖或写入数据库、缓存和客户资料。
- 在不存在路径上不得实例化可能创建文件的 Chroma client；只读检查先确认目录与文件存在，SQLite 必须以 URI `mode=ro` 打开。Chroma `PersistentClient` 即使只查询也会维护 SQLite/HNSW 文件，真实验证必须对完整临时副本运行，不能直接打开原库。
- 未获明确授权不得调用 DashScope、embedding、MinerU、企微或其他外部服务。
- 测试默认使用 fake LLM、fake retriever 与 pytest 临时目录，不连接真实数据库。
- 不得输出 API key、令牌或敏感客户数据；持久化文档身份和数据库元数据不得写入机器绝对路径。
- 保留用户已有工作树修改；禁止 `git clean`、`git reset --hard` 等破坏性操作，除非用户明确要求。

## 5. Markdown-first 方案生成规则

- 唯一用户方案内容源是一个 `proposal.md`。
- 不生成章节 Markdown、`plan.json`、`audit.json`、draft/final 双文件、工作区数据库或状态机文件。
- 大纲、证据映射和审查任务默认仅存在内存；每完成或修订一章可整体重写同一文件。
- 用户明确列出的交付项必须在最终标题结构中逐项可识别；合理归并时使用明确的 H3，不能只在正文隐含覆盖。
- 不引入 LangGraph、多智能体框架、复杂工作区或状态机。
- 不恢复固定 Word 模板、三个占位符或手写 OOXML 生成器。
- DOCX 与 PPTX 的本地渲染器已存在，且只能作为已验证 `proposal.md` 及其来源侧车的独立消费者；不得反向决定方案内容。

## 6. 引用、参数与图片规则

- LLM 只能使用程序提供的证据 ID 与图片 ID；代码将其替换为真实文件名、页码和路径。
- 内存中的章节原文必须始终保留 `[S#]` / `[IMG#]`；审查可查看可读渲染稿，但修订只能接收原始 ID 正文。只有写入 `proposal.md` 时才允许转换为可见来源和图片。
- 有证据的技术事实与参数必须引用；无证据内容写 `【待确认】`。
- 用户明确给出的参数属于“用户输入条件”，不得伪装成知识库来源。
- 地方政策、同类案例、产品能力和标准清单必须分别表述为参考、案例、可选能力或待适用性确认，不能直接写成本项目既定指标、收益、能力或承诺。
- 图片只能来自实际检索结果；不得编造图片路径、caption、文件名或页码。
- 最终仅复制实际使用图片到 `proposal.md` 同级的 `assets/image-###.<ext>`，Markdown 仅引用该稳定相对路径；无有意义 caption/description/页面标题的图片不自动插入。
- 未知证据/图片 ID 必须拒绝或经一次受控修复后拒绝，不能写入用户可见 Markdown。
- 正式审阅稿写入前必须通过确定性质量门槛：合法 H1/标题语法、无内部 ID、无 `cache/` 或绝对路径、引用时来源列表非空、图片文件存在、待确认项非空且具体、以及用户明确要求均有标题覆盖。失败必须带阶段报错，不能标记成功或额外调用 LLM 修补。

## 7. 修改代码的工作流程

1. 先阅读适用说明、相关代码与现有测试，执行 `git status --short --branch` 和 `git diff --stat`。
2. 保持改动最小；功能、配置、路径、模块或行为变更必须补充离线测试。
3. 运行适当测试和 `git diff --check`，不得用外部服务替代离线验证。
4. 有意义的功能、配置、数据路径或架构变更完成后，更新 `DECISIONS.md` 变更记录；改变既有决策时同时更新决策正文和旧决策状态。
5. 最终报告修改文件、测试结果、外部调用、数据库影响及未解决问题。

## 8. 测试与验证命令

```powershell
uv run python -m pytest tests -q
uv run python -m py_compile src/config.py src/main.py src/pipeline.py src/markdown_proposal.py
uv run python src/main.py --help
git diff --check
```

`proposal`、`query`、入库、批量缓存处理和外部评测可能调用服务或访问真实数据；除非任务明确授权，不运行它们。

## 9. 文档与决策记录规则

`AGENTS.md` 记录当前操作规则，`DECISIONS.md` 记录已确认架构决策、废弃决策和有意义变更。不要把决策记录写成逐文件 diff 或调试日志；不确定的想法必须标为“待决定”。

## 10. 当前非目标

- 任意 Word 模板语义填充或固定占位符替换。
- 多 Agent 框架、复杂工作区和状态机。
- 自动重建全部知识库。
- 用多模态模型直接修改 DOCX。
- 在 Markdown 内容与引用质量真实验证前实现复杂 DOCX 排版。
