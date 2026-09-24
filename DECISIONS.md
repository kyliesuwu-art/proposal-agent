# 架构决策

## 2026-09 — PPT live producer requires explicit argv and a pre-call budget

Decision: The WeCom bot keeps PPT generation offline by default. A live PPT run must receive an explicit CLI enable flag, a positive `PPT_MODEL_LIVE_APPROVED=1` gate, a JSON argv producer command, and a positive model-call budget. The producer inherits credentials only through the process environment; it receives no secrets as argv. Unknown placeholders and pre-existing job Scene Graph folders fail closed.

Impact: A historical experiment Scene Graph cannot be reused to claim a live ArtifactJob. The V4 request wrapper consumes its call budget before each Ark request, including critic and revision calls. The production producer also requires an explicit, read-only visual-reference root; it validates the approved logo and reference sheets before its first Ark request, rather than discovering a historical task directory implicitly.

## 2026-09 — PPT runtime artifacts are portable within a job root

Decision: V4 Ark-call records, V4 visual-calibration reports, and the producer manifest serialize generated artifact locations relative to their active ArtifactJob output root. They do not encode the repository root or absolute machine paths.

Impact: A guarded producer can run from a separate worktree against a task rooted elsewhere, and its persisted records remain relocatable while retaining paths needed by the job consumer.

## 2026-09 — PPT model attempts are streaming, transport-bounded operations

Decision: V4 PPT model stages retain streaming JSON-object responses because this long-reasoning model can emit early SSE chunks while generation continues. Consumers receive content only after the terminal `[DONE]` event, then apply JSON syntax and schema validation. Each logical stage has at most two total budgeted HTTP attempts, shared by transient transport, JSON syntax, and schema failures. Every attempt writes redacted, job-relative telemetry even if the SSE body is interrupted.

Impact: A partial SSE response is never parsed or combined with a retry. Transient `IncompleteRead`, connection, timeout, 408, 429, and 5xx failures can consume the single remaining attempt; authentication, configuration, local validation, and budget failures stop immediately. The producer-wide 26-call budget remains enforced before every network request, while first/last chunk timings distinguish stream progress from a stalled request.
## 2026-09 — PPT model JSON responses are bounded, lossless contracts

Decision: Every V4 model JSON stage accepts only lossless transport normalization (BOM/whitespace, one complete JSON fence, or one string-aware top-level object), validates its declared schema, and may issue at most one new budgeted request after a parse or schema failure. Each response is persisted under a distinct attempt filename with structured, job-relative call evidence.

Impact: The producer never silently repairs model facts or syntax. A malformed response remains diagnosable, retry consumption remains within the single producer-wide call budget, and invalid Global Art Direction cannot reach page generation.


此处只记录长期架构或产品决策：即系统重构或长期工作方向调整时，仍需理解其取舍原因的选择。

不要记录日常进度、普通实现细节、测试运行结果、临时 API/网络故障、当前阻塞或下一步行动。这些信息应保留在 Git 历史、提交信息、输出报告或当前工作会话中。

系统当前契约见 [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md)。小范围任务不需要默认阅读全文。

## 2026-08 — 混合且可追溯的检索

决策：
采用 Chroma 语义检索与 SQLite FTS5 词法检索，并以确定性 RRF 融合；检索、引用映射全过程保留来源和页码身份。

原因：
语义检索适合理解自然语言意图，词法检索可保障设备型号、电压、容量等精确参数的召回。稳定融合和可追溯身份使结果可解释，并避免同名文件混淆。

拒绝的替代方案：
纯 Chroma 加 LLM 回答；持久化机器绝对路径；只按文件名识别来源。

影响：
`retrieve_evidence()` 只负责检索。持久化身份使用可移植的相对 `source_key`，交付引用必须映射到真实文件名和页码。

## 2026-09 — Markdown-first 的单一方案内容源

决策：
生成、审查、验证和发布唯一的 `proposal.md` 作为方案内容源。大纲、证据映射和审查过程仅驻留内存；Word/PPT 只在已验证交付物之后作为下游渲染产物。

原因：
内容、证据和审查必须先于版式独立验证。单一内容文件可避免章节、草稿、JSON 计划和工作区状态之间的漂移。

拒绝的替代方案：
固定 DOCX 模板及占位符填充；手写 OOXML；章节 Markdown、draft/final 双文件、`plan.json`、`audit.json`，或方案状态机/工作区。

影响：
DOCX/PPTX 渲染器不得决定方案事实或结构。项目不引入 LangGraph、多智能体编排或复杂状态机。

## 2026-09 — LLM 判断与确定性证据/发布控制分离

决策：
LLM 负责规划、写作、审查和定向修订；确定性代码负责检索、证据/图片 ID 校验、来源和资产映射、最终 Markdown 规范化及发布质量门禁。

原因：
保留编辑判断能力的同时，防止编造引用、参数、路径和交付资产。

影响：
有证据支撑的技术事实和参数必须引用；无证据内容明确标注 `【待确认】`。用户输入仍是用户输入，不得伪装为知识库事实。政策、案例、产品能力和标准须分别以参考、案例、可选能力或待确认适用性表述。

## 2026-09 — 明确交付溯源与图片契约

决策：
章节生成和修订过程中保留原始 `[S#]` / `[IMG#]` ID，仅在发布时解析。发布来源侧车；交付图片必须是独立、合法的 Markdown 图片块，并复制到稳定相对路径 `assets/image-###.<ext>`。

原因：
修订不能破坏溯源，且 Word/PPT 消费方需要唯一、无歧义的图片契约。

影响：
未知 ID、编造资产路径、畸形/行内图片块、泄露的内部 ID 及不可发布资产必须在受控发布流程中失败，不能进入用户交付物。

## 2026-09 — 发布前质量门禁与安全诊断

决策：
正式审阅 Markdown 写盘前执行确定性结构和溯源门禁。远程调用、审查和发布失败按阶段报告，并提供不泄露敏感信息的诊断。

原因：
看似正常的文档不代表引用、图片、用户要求标题或待确认项正确。失败信息必须可执行，但不能泄露提示词、凭据或敏感来源数据。

影响：
门禁检查唯一且首位的合法 H1、标题覆盖、可见引用与来源列表一致性、无内部 ID/cache/绝对路径、资产有效性及具体待确认项。关键审查格式错误不得静默通过；只有严格分类的 advisory 缺陷可跳过。

## 2026-09 — 正式 Office 成品是干净派生物，不是另一份事实源

决策：
`proposal.md` 及其溯源侧车仍是审阅事实源。正式 Word/PPT 是只读派生物：不显示可见引用标记、内部 ID、草稿/状态标签及图片来源标签；溯源仍保留在侧车、计划、报告和相应来源资料中，而非被丢弃。

原因：
审阅者需要透明证据，管理层 Office 成品需要干净的阅读界面。分离两种视图可防止展示清理改变事实。

影响：
未确认项目输入集中为具体的深化设计输入条件，不能通过字符串替换伪装为已知事实。渲染文件需进行结构检查，并在条件具备时做真实逐页视觉审阅；几何/OOXML 检查本身不代表视觉质量。

## 2026-09 — PPT 汇报受证据约束并有交付页数范围

决策：
PPT 从已交付 Markdown 和溯源资料构建。正式 briefing 为 15–25 页，通常约 18–20 页，页数上限覆盖所有页面类型。Thinking/内容组织可改善叙事与版式选择，但不得新增事实、参数或图片。

原因：
管理汇报必须简洁，不能将取舍后的素材变成无依据主张。图片必须因信息价值被选用，而不是为填页。

影响：
演示可压缩支持性文字，但须保留覆盖记录。图片需记录选用/拒绝理由；正式页面不显示来源 ID。来源资料内部可追溯；缺少真实页面渲染时视觉状态只能是 `BLOCKED`/`NOT_RUN`，不能标记 `PASS`。

## 2026-09 — 唯一测试数据库明确且受保护

决策：
当前唯一测试数据库为 `runtime_data/word_test_db`，其 Chroma collection 为 `electrical_pages_v2`。

原因：
必须有唯一、已知且完整的基线，避免意外回退到已删除的实验库。

拒绝的替代方案：
默认使用、恢复或重建 `v2_test_db`、`v3_candidate_db` 或 `v3_candidate_db_rebuilt`。

影响：
数据库检查默认只读。SQLite 使用 URI `mode=ro`；Chroma 验证在完整临时副本上运行，因为 `PersistentClient` 可能维护本地文件。

## 2026-09 — PPT live producer command files remain data-only

Decision: The guarded WeCom PPT entry point accepts exactly one producer argv source: the existing inline JSON option or a UTF-8 JSON file. File inputs must be regular, readable files no larger than 64 KiB; their top level must be a non-empty string array and they pass the same placeholder validation as inline argv.

Impact: PowerShell callers can pass a single path containing spaces or Chinese characters without command-line JSON quoting risk. The bot never expands a shell, evaluates configuration, or logs the configuration content; the downstream process continues to receive an argv list with shell=False.

## 2026-09 — Persisted Task paths require an explicit trust root

Decision: Task paths stored in the WeCom SQLite database are resolved from an explicit task-path root, not the process current directory or the code worktree. Relative values are rooted there; legacy absolute values remain readable only when they are canonical descendants of the same root. Artifact processing also requires the resolved approved Markdown to remain under the configured task output directory for its Task ID.

Impact: A separate PPT integration worktree can safely consume an existing Task without copying or rewriting approved.md. Traversal, drive-relative paths, UNC paths, symlinks, missing or empty files, non-Markdown files, and any containment escape fail before an ArtifactJob or runner starts.

## 2026-09 — PPT Scene Graph image references are task-manifest sources

Decision: In the production V4 Scene Graph, `id` remains a unique element name only. The only rendered image reference is `image_source`, which must exactly match a validated, task-relative source from the already-created `assets_manifest.json`. The page prompt receives the same validated asset records (asset ID, source and dimensions); attached reference decks are explicitly style-only and never become allowed content images.

Impact: Diagnostics identify invalid `image_source` values without rejecting ordinary node names such as `screenshot`. The renderer interface and Scene Graph field names remain unchanged, while visual-reference paths, URLs, absolute paths and traversal values fail closed because they are absent from the manifest whitelist.
## 2026-09 — Direct PPT producer scripts bootstrap their own worktree root

Decision: `scripts/run_ppt_v4_ark_full20.py` derives its project root from `__file__` and inserts that root into its process-local `sys.path` before importing `src`. It remains independent of the caller cwd, `PYTHONPATH`, the original Core checkout and editable-install state.

Impact: `ProductionPptRunner` can invoke the configured producer by absolute script path from an ArtifactJob subprocess without an import-stage failure. The bootstrap is limited to this direct entry point and does not mutate global environment or alter production command execution.

## 2026-09 — Explicit PPT resume creates a lineage child only after checkpoint preflight

Decision: A PPT resume is an explicit ArtifactService operation with a caller-provided failed source job. It validates the approved Markdown hash, source output-root containment, source-job binding, checkpoint manifest, canonical Global/continuous Page checkpoints, and remaining producer budget before atomically creating a new lineage child. The child receives a separate output root and passes the parent root only as a read-only resume input to the existing runner.

Impact: Failed jobs are never returned to RUNNING and are never overwritten. Structured runner failures persist their code, stage and retryability on the child; evaluation failure is terminal and blocks delivery. Normal PPT generation retains its existing no-resume behavior.
