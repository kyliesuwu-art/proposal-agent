# DECISIONS.md

## 文档用途

记录当前有效的架构选择、已替代方案和有意义的变更。它不是调试日志；代码、用户确认事实与离线测试优先于旧记录。

## 当前状态快照（2026-09-11）

- 唯一测试库：`runtime_data/word_test_db`。
- 数据集：10 份来源文档、214 条索引页；SQLite FTS5 与 Chroma 各 214 条记录，collection 为 `electrical_pages_v2`。
- 单 Markdown proposal V1 已完成离线实现、受控 DashScope 尝试与发布诊断；attempt4 已生成可交付的 Markdown、来源侧车和真实图片资产。
- DOCX/PPTX 本地渲染器已存在，均只消费已验证的 Markdown 交付物；attempt4 在 DOCX 本地预检处停止，尚未进入 PPTX 或视觉验收。
- attempt4 原件保持不变；其离线 `attempt4_delivery` 副本已用共享图片块契约修复，Word 已嵌入全部 5 张图。PPT 计划识别全部 5 张图，但 PPTX 在既有 `slide-041` 几何越界处失败，未做视觉验收。

## 当前有效的架构决策

### D-001 混合检索使用 Chroma、SQLite FTS5 和 RRF

- 状态：有效
- 日期：2026-08
- 背景：语义检索适合理解自然语言，精确设备、型号、电压和容量需要词法召回。
- 决策：Chroma 语义候选与 SQLite FTS5 候选以稳定 RRF 融合，并保留融合原因与命中 metadata。
- 原因：兼顾广召回、精确参数命中和可解释性。
- 影响：检索页保留 `keywords`、`entities`、`parameters` 与来源身份；supporting pages 受预算约束。

### D-002 文件与页面来源必须可追溯

- 状态：有效
- 日期：2026-08
- 决策：`source_key` 使用相对逻辑路径；文档和版本身份分离；输出引用以“文件名 + 页码”为准。
- 原因：避免同名文件混淆，也避免将机器绝对路径持久化。
- 影响：`SearchHit`、`Citation` 与 proposal 引用都必须映射到真实来源页。

### D-003 方案采用 Markdown-first

- 状态：有效
- 日期：2026-09
- 决策：先生成、验证并审查一个 Markdown 方案，再由独立渲染阶段消费它。
- 原因：内容、证据和审查可独立验证，避免文档格式逻辑干扰生成质量。
- 影响：DOCX/PPTX 渲染器不得反向决定方案内容。

### D-004 每次方案只维护一个 proposal.md

- 状态：有效
- 日期：2026-09
- 决策：计划、证据和审查意见仅驻留内存；每章完成或修订时整体更新同一 `proposal.md`。
- 原因：避免 draft/final、章节文件、plan/audit JSON 与工作区状态失真。
- 影响：不建立 `sections/`、状态机或 proposal 工作区数据库。

### D-005 LLM 与确定性代码职责分离

- 状态：有效
- 日期：2026-09
- 决策：LLM 规划、写作、审查和定向修订；代码完成检索、证据 ID 校验、来源/图片映射和渲染。
- 原因：降低引用幻觉和参数编造风险。
- 影响：资料不足必须标记 `【待确认】`；用户输入条件不能伪装为资料事实。

### D-006 retrieve_evidence 是纯检索入口

- 状态：有效
- 日期：2026-09
- 决策：`pipeline.retrieve_evidence()` 接收已规划查询，执行混合检索和 supporting pages，不调用最终答案生成。
- 原因：分章写作不能先为每章生成一次普通 RAG 答案。
- 影响：proposal 工作流可注入 fake retriever；SQLite FTS 使用只读连接，但 Chroma `PersistentClient` 会维护 SQLite/HNSW 文件，真实验证必须在完整临时副本中运行。

### D-007 当前唯一测试库是 runtime_data/word_test_db

- 状态：有效
- 日期：2026-09
- 决策：默认 RAG 路径使用项目相对路径 `runtime_data/word_test_db`。
- 原因：该库是本机唯一保留且完整的混合检索测试库。
- 影响：`v2_test_db`、`v3_candidate_db` 和 `v3_candidate_db_rebuilt` 已删除，不应被默认使用、恢复或重建。

### D-008 DOCX/PPTX 是独立渲染阶段

- 状态：有效
- 日期：2026-09
- 决策：本地 DOCX/PPTX 渲染器仅消费已验证的 `proposal.md` 与来源侧车，不使用固定章节占位符。
- 原因：保证内容真实性优先于排版。
- 影响：不引入 DOCX 模板语义填充、手写 OOXML 或多模态 DOCX 修改。

### D-009 当前不引入复杂编排框架

- 状态：有效
- 日期：2026-09
- 决策：不使用多智能体、LangGraph、状态机、每章落盘或复杂 proposal workspace。
- 原因：V1 只需要可测试的单文件顺序流程。
- 影响：依赖注入和内存运行时对象足以支持离线测试。

### D-010 正式审阅稿在最终渲染时实施质量门槛

- 状态：有效
- 日期：2026-09
- 背景：首次真实生成暴露出修订后来源映射丢失、临时图片路径、空待确认项和案例/政策适用范围混淆。
- 决策：章节运行时始终保存带内部证据/图片 ID 的原文；审查只看可读版本，修订仍使用原文。最终渲染才转换可见引用、复制实际使用图片到同级 `assets/`，并执行确定性质量检查。
- 原因：将 LLM 内容决策与可验证的来源、图片交付和 Markdown 语法分开，避免修订破坏可追溯性。
- 影响：有引用而无来源列表、残留内部 ID/缓存路径、破损图片、空待确认项或缺失用户明确标题覆盖时，proposal 以 `quality_gate` 阶段失败；不会自动重试整篇或调用额外 LLM。

### D-011 证据适用角色必须显式表达

- 状态：有效
- 日期：2026-09
- 决策：写作、审查和修订提示词明确区分用户输入条件、项目已知事实、地方政策参考、同类案例、产品能力、设计建议和待确认项。
- 原因：来源真实不等于可直接成为本项目参数、指标、收益或承诺。
- 影响：异地政策不得作为本项目强制指标，案例数据不得作为本项目测算，产品能力不得作为已承诺能力；行业标准适用性须结合最终系统和并网条件确认。

### D-012 Markdown 审阅稿在写盘前统一规范化并汇总待确认项

- 状态：有效
- 日期：2026-09
- 决策：最终完整 Markdown 在写盘前进行确定性结构规范化和质量检查；章节不得自行保留“待确认项”专章，系统从正文的具体 `【待确认】` 事实稳定去重后统一生成唯一的 `## 待确认项`。
- 原因：真实生成样本出现粗体包裹标题、转义列表、重复转义粗体以及重复待确认章节，说明只验证章节中间态不足以保证审阅稿质量。
- 影响：最终质量门槛拒绝残留异常 Markdown、内部 ID、缓存/绝对路径、重复待确认标题、破损图片和空来源列表；失败不写入 proposal 正文，也不额外调用 LLM。

### D-013 后续审阅与正式交付的来源展示模式

- 状态：有效（产品方向计划中、尚未实现）
- 日期：2026-09
- 决策：当前 Markdown 审阅稿继续保留行内来源，便于核对事实。计划中的 H5 将提供逐章审阅、批注、直接编辑和待确认参数填写；计划中的企业微信将作为需求、通知和确认入口。未来正式 Word 正文不显示来源标号或长来源标记，仅在文末列出“来源与依据”。
- 影响：本决策不实现 H5、企业微信、Word 或其版本/会话管理；它们仍是独立后续工作。

### D-014 proposal 的远程调用、review 容错与运行可观察性

- 状态：有效
- 日期：2026-09-11
- 背景：医院园区高可靠供电与智慧配电方案的首次真实运行在 review 返回结构校验处失败；此前 `proposal.run.log` 只在结束时写入，无法区分远程调用、review、修订或发布失败。
- 决策：DashScope OpenAI-compatible 调用采用 15 秒连接超时、300 秒读取超时及调用方有限重试；OpenAI SDK 内建重试关闭，避免嵌套、不可观察的无限等待。proposal 启动即建立 `proposal.run.log`，并追加脱敏事件：初始化、规划/检索/写作/review/修订阶段完成、模型调用开始/完成/失败与耗时。日志只记录 provider、endpoint、model、超时、stage、purpose、异常类型和安全 operation，不记录完整 prompt、API Key、Authorization 或环境变量值。
- review 容错：仅显式 `advisory`、`info`、`warning` 的孤立格式损坏项可跳过，并记录 `review_advisory_item_skipped`、received/valid/skipped 计数及 warning；未分类、blocking 或 critical 的损坏项必须失败。无法解析 JSON 或缺少 issues 顶层列表时仅允许一次格式修复调用；修复仍失败即以 `review` stage 显式失败。
- 发布诊断：Markdown/assets/sources 的暂存、校验与原子发布失败包装为 `ProposalWriteError`，以 `write_operation` 和底层异常类型写入 run log，不暴露内容。
- 已完成：`0e96f7f fix: harden proposal review and add run observability`、`f21eecf fix: trace proposal publication failures`、`d74f78b fix: record quality gate failure diagnostics`、`b886df2 fix: distinguish pre-render quality failures`、`0917629 docs: record proposal reliability decisions`、`5c3b0e6 fix: enforce proposal image publication invariants`、`603f90d docs: update proposal delivery status`。图片/H1 修复后的完整离线测试为 148 passed、6 warnings，`compileall` 与 `git diff --check` 通过。
- 推送状态：`0917629` 已同步；`5c3b0e6` 与 `603f90d` 尚未同步。两次 `git push origin main` 均因 GitHub `443` 无法连接而失败；不得 force push 或重写历史。
- 已完成的真实验收：DashScope 与 `qwen3.7-plus` 可用。attempt2 在 `markdown_write` 失败；retry 在最终渲染前失败且未保留中间 Markdown；attempt3 的确定性根因是同一选中 `[IMG#]` 标记重复渲染为多个链接，而 `_asset_plan` 只产生一个资产。暂存校验器在 `validate_staged_markdown` 拒绝了图片链接、复制图片和 assets 文件数量不一致的交付，行为正确。各失败目录仅保留 `proposal.run.log`，没有 `proposal.md`、`proposal.sources.json`、`proposal.request.json` 或 assets；不得伪造 sidecar。
- attempt4：在修复后唯一一次受控真实运行中，`proposal.md`、`proposal.sources.json`、`proposal.request.json` 和 5 个真实 assets 均成功发布。验收结果为 1 个 H1、15 个标题、69 个可见引用、12 项具体待确认；5 个图片链接、5 个唯一链接和 5 个资产文件一一对应，且分布在 5 个相关业务章节。不存在内部 ID、缓存路径或绝对路径；医院容量、负荷、电价、投资和收益等未提供参数均标为 `【待确认】`，案例数值明确标为参考。
- 当前阻塞：attempt4 的 `render-word` 在本地 DOCX 验证器以 `RenderWordError: local DOCX validation failed` 停止，未生成 DOCX。根因是最终 Markdown 的图片图注规范化不完整：图片正则可提取 5 个图片链接，但仅 4 个处于完整独立行；第 3 张图前有 4 个不完整的 `![caption]` 片段。渲染器因此只嵌入 4 张图片，而来源扫描期望 5 张，预检正确拒绝发布。PPTX、几何审计和 WPS/PowerPoint 视觉验收均未启动。
- 后续计划：先离线修复图片图注/Markdown 行级规范化，使每个选择的图片严格渲染为一个独立完整的 Markdown 图片行，并补充“可提取链接数与可渲染独立图片行数一致”的回归测试。随后运行完整测试、编译和 diff 检查，提交并在网络恢复后普通 push；只有在用户再次明确授权后，才可针对同一已交付 Markdown 重跑 Word 技术验收。Word 通过逐页 PNG 渲染检查后，才可构建 PPTX、执行几何审计；只有生成真实逐页图片并检查后，才能声称 WPS/PowerPoint 视觉验收通过。
- 踩坑与约束：不能仅以图片链接正则数量判断图片可发布性，Markdown 行级语法也必须可被下游渲染器完整消费；不能为通过渲染器而复制重复文件、放宽暂存校验或伪造 sidecar。`_write` 暂存校验继续作为最后防线；最终渲染前以 `link=unique_link=asset_plan=copied=staged_file` 为 FATAL 发布一致性门槛。H1 只由确定性渲染器生成，正文 H1/H2 在代码块外降级为 H3，草稿提示置于 H1 后。

### D-015 proposal、Word 与 PPT 共享独立图片块契约

- 状态：有效
- 日期：2026-09-11
- 决策：合法交付图片仅为 fenced code block 外、独占一行的 `![单行纯文本图注](assets/相对路径)`。图注由资料侧规范化为单行纯文本；禁止方括号、嵌套图片语法、行内图片、孤立 `![图注]` 与不安全路径。`src/markdown_images.py` 是 proposal 发布校验、Word 与 PPT 解析的唯一实现。
- 影响：图片子串或宽松正则的计数不能代表可渲染图片数。新 proposal 发现畸形图块在发布前失败；带 `proposal.sources.json` 的 PPT 输入发现畸形图块也必须失败。attempt4 无需重新生成：只在独立的 delivery 副本移除可确认的模型包装残片，并保留原始目录、来源侧车、正文、引用和图片顺序。
- 验收：attempt4 原件有 5 个 assets 子串、但只有 4 个独立图片块；delivery 有 5 个独立块。Word 报告的 `markdown_image_path_count`、`standalone_image_block_count`、`embedded_image_count`、`unique_asset_count` 和 DOCX media 均为 5。PPT slide plan 识别 5 张图；PPTX 在无关的 `slide-041` 几何越界失败，未进行几何或视觉验收。

### D-016 管理汇报 briefing 的硬上限与来源版式

- 状态：有效
- 日期：2026-09-11
- 决策：PPT 始终从已交付 proposal 和来源侧车构建；`briefing --max-slides` 为封面、目录、正文、图示和参考资料共用的硬上限。briefing 可省略支持性文字并记录 coverage，不得新增事实或参数；剩余页数优先为已选图片建立主题页，并记录无法使用的原因。正文页脚展示至多两项来源并标出总数，完整可追溯来源由侧车和参考资料页消费。
- 影响：presentation/faithful 保留所有数字和续页；briefing 不以未选择的支持性数字阻止发布。参考资料按文件名合并页码并自动分栏，防止来源行坐标越过页面；不得删除来源、隐藏对象、关闭几何检查或缩小正文到不可读字号。
- 验收：attempt4 delivery 的 presentation 旧计划为 41 页，`slide-041` 的 39 条逐行来源使末行纵向越界。briefing 生成 13 页，解析/嵌入 5 张图，几何边界通过；本机无 PowerPoint/LibreOffice/Poppler 渲染器，视觉 PNG 验收仍为 BLOCKED。

## 已废弃或已替代的决策

- 固定 DOCX 模板、三个占位符和手写 OOXML 整篇填充已移除：它们把内容生成绑定到固定版式，无法可靠验证内容与来源。
- 多份章节 Markdown、`plan.json`、`audit.json` 及 proposal 工作区方案未采用：单个 `proposal.md` 是唯一内容源。
- `v2_test_db`、`v3_candidate_db`、`v3_candidate_db_rebuilt` 不再是当前数据源：这些路径已删除，不是恢复目标。
- 旧的“纯 Chroma + LLM query”说明已被混合检索和 `retrieve_evidence` 替代；兼容代码不代表当前 proposal 架构。

## 待决定事项

- 修复 PPTX `slide-041` 的几何越界后，使用已经通过 Word 的 attempt4 delivery 完成 PPTX 几何与真实页面渲染验收；不得重新生成 proposal。
- 是否为未来不同测试数据集增加显式、只读的 CLI 数据库参数；当前不扩大 CLI 范围。

## 变更记录

| 日期 | 范围 | 变更 | 验证 | Commit |
|---|---|---|---|---|
| 2026-08 | 检索 | 引入 Chroma + FTS5 + RRF 混合检索与页面来源字段 | 既有离线检索测试 | 未提交 |
| 2026-09 | 方案生成 | 移除旧 Word 模板链路，新增单 Markdown proposal V1 与 `retrieve_evidence` | 65 passed，6 warnings | 未提交 |
| 2026-09 | 检索安全 | proposal 检索路径采用只读 SQLite 与既有 Chroma collection | 离线测试 | 未提交 |
| 2026-09 | 数据路径与文档 | 统一默认库为 `runtime_data/word_test_db`；合并 CLAUDE 说明到 AGENTS/DECISIONS | 66 passed，6 warnings | 未提交 |
| 2026-09 | proposal 诊断 | 增加阶段错误、`--debug` traceback 与输出父目录测试；确认 Chroma 查询需临时副本隔离 | 80 passed，6 warnings | 未提交 |
| 2026-09 | proposal 审阅稿质量 | 原始 ID 跨审查/修订保留；来源按正式文件名分组；仅复制已用图片到稳定相对路径；新增适用角色提示词与最终质量门槛 | 离线 proposal 回归测试 | 未提交 |
| 2026-09 | Markdown 审阅稿修复 | 写盘前统一规范化标题、列表、异常粗体和缩进；最终门槛检查完整 Markdown；待确认项集中清洗、稳定去重并只输出一个专章 | 离线 proposal 回归测试 | 未提交 |
| 2026-09 | proposal 待确认项质量 | 用户明确列出十类缺失项目参数时，最终统一输出规范化且无重复的十项清单；识别并移除模型生成的“待确认内容/事项”及中文编号变体标题 | 26 个 proposal 离线测试、90 个全量测试 | 未提交 |
| 2026-09 | proposal 待确认项质量 | 保留“电价”等两字但具体的待确认参数，避免短文本过滤造成用户明确参数遗漏 | 27 个 proposal 离线测试、91 个全量测试 | 未提交 |
| 2026-09 | proposal 编排与交付韧性 | 规划前使用只读混合检索的受限证据概览；分章写作共享代码构造的 document brief；审查增加跨章节问题。质量门禁改为 PASS/DRAFT_WITH_WARNINGS/FATAL，非致命问题写入带提示的 Markdown 草稿并记录结构化指标 | proposal/retrieval 离线测试 | 未提交 |
| 2026-09 | proposal 图片与交付质量 | 图片按全文 3～5 张目标、主题优先和来源页去重选择；候选、选择、复制和移除原因进入运行日志。Markdown 与 assets 在临时目录验证后原子发布；待确认项按固定业务类别归并；角色、范围与重复问题纳入三级质量状态和只读诊断 | 离线 proposal 回归测试 | 未提交 |
| 2026-09 | proposal 真实图片兼容 | 已确认测试库图片为 `assets/.../page_<页>_<序号>.<ext>` 相对路径；无图注且无 page title 时从页面正文标题保守生成一条候选说明。最终状态按暂存 Markdown、相对资产与计数一致性判定，候选≥3但零图为 `image_pipeline_failure` | 真实库只读字段核对与离线回归测试 | 未提交 |
| 2026-09 | proposal 图片章节归属 | 图片候选、选择、资产、复制、分章渲染和最终回查统一使用规划产生的 `section_id`；优先覆盖有候选的 preferred 章节，代码补选贴近同来源同页证据段落。最终 Markdown 若图片落在非所属 H2，按 `image_placement_mismatch` 失败；仅标准 `![alt](assets/...)` 计入图片链路 | fake 图片离线回归测试 | 未提交 |
| 2026-09 | proposal 引用交付 | 在单一 `proposal.md` 发布时同步原子写出 `proposal.sources.json`；按最终可读来源首次出现顺序分配稳定 `S#`，并记录图片的 `figure_id/source_id/source_file/page/section_id/asset_path`，供本地 Word/PPTX 渲染器消费 | proposal 侧车专项测试与全量离线测试 | 未提交 |
| 2026-09 | MinerU block 兼容 | 解析器版本升级为 `mineru-blocks-v2`：同页保留 aside、公式和 chart 图元/图注/脚注/bbox；目录型 index 过滤而有价值索引保留，未知 block 按页聚合告警。未来仅以文档级新版本解析、embedding 与验证成功后原子替换旧版本；本轮只读审计，不重处理数据库 | 366 ZIP 脱敏审计、P2 专项测试与全量离线测试 | 未提交 |
| 2026-09 | 定向升级准备 | P3 以 ZIP SHA-256 与稳定 document ID 生成 legacy/failed/duplicate 互斥计划；`reindex-affected --dry-run` 禁止源目标同路径和已存在目标，且保证零数据库/网络/embedding 调用。实际候选库创建与 `--resume` 需独立授权 | 86/5/9 真实计划与 dry-run、离线测试 | 未提交 |
| 2026-09 | PPT 下游交付 | `build-slides` 将已交付 Markdown 重组为含稳定 slide ID/layout 的演示稿和 slide plan；本地 PPTX 按图注主题重新分配图片，单页默认一图，并在容量不足时生成续页而不截断。历史 Markdown 缺少 sidecar 时仅从可见来源降级，不伪造侧车。成功 proposal 另存不含密钥的 `proposal.request.json` 以便复现 | PPT 专项、真实 Markdown→slides→PPTX 本地验收 | 未提交 |
| 2026-09 | PPT briefing 上限 | `briefing --max-slides` 是封面、目录、正文与参考资料均计入的硬上限；规划器以可追踪 coverage 省略辅助说明，不能生成 continuation 绕过上限。`presentation`/`faithful` 保留全文 continuation 行为。PPTX 另做几何审计；没有 PowerPoint/LibreOffice 页面渲染时明确标记视觉验收 BLOCKED | briefing 硬上限、coverage、几何审计与真实历史输入验收 | 已提交 |
| 2026-09-11 | proposal 可靠性 | DashScope 使用显式连接/读取超时与有限重试；review 仅跳过显式 advisory 的孤立坏项，JSON 顶层失败允许一次修复；run log 从启动开始记录脱敏阶段与模型调用事件；发布失败增加安全 operation 追踪；attempt3 识别出重复图片标记导致暂存交付不一致，新增最终标记去重、H1 规范化和发布前一致性门槛；attempt4 成功发布 Markdown/sidecar/assets，但 Word 预检识别出图片图注造成的行级 Markdown 缺陷 | 148 passed，6 warnings；compileall；diff check；attempt4 真实 Markdown/来源验收 | `0e96f7f`、`f21eecf`、`d74f78b`、`b886df2`、`0917629`（已推送）、`5c3b0e6`、`603f90d`（待网络恢复后推送） |
| 2026-09-11 | 图片下游契约 | 新增共享独立图片块解析；proposal、Word、PPT 统一校验代码块外的安全 `assets/` 独立图块。attempt4 delivery 离线修复后 Word 嵌入 5/5 图；PPT plan 识别 5 图，但 PPTX 在 `slide-041` 几何越界停止 | 图片专项、proposal/PPT 专项、全量离线测试、compileall、diff check；Word 实际渲染 | 本次提交 |
| 2026-09-11 | PPT 管理汇报 | briefing 在硬上限内压缩已交付 Markdown 并记录 coverage；每张选中图片有主题页或明确未用原因；来源页按文件合并、自动分栏，briefing 页脚限制显示来源数。attempt4 delivery 13 页、5 图，几何通过；视觉渲染因本机无渲染器阻塞 | PPT 专项、全量离线测试、compileall、diff check；真实 delivery PPTX 与几何审计 | 本次提交 |

| 2026-09 | 外部服务配置 | 新增 `scripts/ark_quickstart.py`（纯标准库，不依赖 curl/jq）用于验证火山方舟 Managed Agents 连通性；`.env` 与 `.env.example` 增加 `ARK_API_KEY` 与可选 `ARK_BASE_URL`。该脚本只做外部连通性验证，不参与方案生成链路，生成侧仍使用 DashScope | 编译检查与缺 Key 报错路径离线验证 | 未提交 |

今后完成有意义的功能、配置、路径、模块或行为变更时追加本表；若改变既有决策，同时更新该决策状态，并将旧决策移入“已废弃或已替代的决策”。
