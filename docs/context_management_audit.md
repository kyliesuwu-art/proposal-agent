# 上下文管理审计

日期：2026-09-14。范围：项目 Markdown、配置指南和既有测试清单。未修改生产代码、测试、数据库、outputs 或 PPT V4 产物。

## KEEP_IN_DECISIONS

| 原有材料 | 分类 | 原因 |
| --- | --- | --- |
| D-001 / D-002：混合 RRF 检索与可移植文档/页码身份 | 保留 | 解释为何双检索模式和来源可追溯是架构要求。 |
| D-003 / D-004：Markdown-first 与单一 `proposal.md` | 保留 | 定义核心内容架构及拒绝的工作区/模板方案。 |
| D-005 / D-006：LLM 与确定性控制边界；纯检索入口 | 保留 | 解释信任边界和模块职责分工。 |
| D-008 / D-009：独立渲染器；不采用复杂编排 | 保留 | 防止未来重构时重新引入已拒绝的系统形态。 |
| D-010 / D-011 / D-012 / D-015：溯源、适用角色、规范化与图片契约 | 保留并合并 | 属于长期的证据/发布设计，不是日常实现细节。 |
| D-013 / D-016 / D-017：审阅与正式交付分层、溯源侧车、PPT briefing 范围 | 保留并修正/合并 | 解释为什么正式 Office 成品保持干净、审阅材料仍可追溯。未实现的 H5/企微设想不作为当前决策保留。 |
| D-007：测试数据库基线 | 简要保留 | 记录了有安全意义的基线及已拒绝的旧数据库路径。 |

## MOVE_TO_SPEC

| 原有材料 | 去向 | 原因 |
| --- | --- | --- |
| 产品目标、模块关系、Markdown-first 操作规则、引用、图片路径和交付规则 | `PROJECT_SPEC.md` | 这是当前系统契约，不是历史取舍理由。 |
| 正式质量门禁和审阅质量状态 | `PROJECT_SPEC.md` | 是贡献者需要实现和验证的持续行为。 |
| 数据库安全、fake 服务测试和外部服务禁令 | `PROJECT_SPEC.md` | 是稳定的运行安全规则。 |
| PPT 内容、图片、字体和视觉审阅原则 | `PROJECT_SPEC.md` 及既有 `config/PPT_GENERATION_GUIDE.md` / `config/PPT_VISUAL_DESIGN_GUIDE.md` | Spec 给出长期边界，指南提供 renderer/设计细节。 |
| 旧 `DECISIONS.md` 的文档用途和更新说明 | `CODEX_WORKFLOW.md` | 属于工作方式，而非设计决策。 |

## ALREADY_COVERED_BY_TESTS

| 不变量范围 | 既有证据 |
| --- | --- |
| 修订期间引用 ID 保持原始形式、可见引用映射至来源列表、无效/内部泄露会失败 | `tests/test_markdown_proposal.py` |
| Markdown H1、标题、待确认项、图片资产、来源侧车和写盘前质量门禁 | `tests/test_markdown_proposal.py` |
| 独立图片块语法及 Word/PPT 共同消费图片 | `tests/test_markdown_images.py`、`tests/test_formal_delivery.py` |
| 正式 Office 文本去除禁用标记，正式 PPT 强制 15–25 页 | `tests/test_formal_delivery.py` |
| PPT 计划/内容保留、来源交接、图片处理、PPTX 重开、几何审计及 briefing 上限 | `tests/test_render_pptx.py` |
| RRF 确定性、技术字段抽取、supporting pages 预算及来源身份可移植性 | `tests/test_hybrid_v2.py`、`tests/test_retrieval_v1.py`、`tests/test_document_identity.py` |
| 配置路径稳定且测试覆盖可隔离运行时路径 | `tests/test_config_paths.py` |

测试候选项（本任务未实现）：在可用页面渲染器存在时渲染真实 Office 页面；检查无空白页；补充 PPT Markdown 语法泄露回归 fixture；增加只读/副本式测试，证明不会改动保留的数据库。这些应成为后续测试工作，而非反复写入 Agent 提示词。

## EPHEMERAL_HISTORY

| 原有材料 | 为什么是临时历史 | 保留位置 |
| --- | --- | --- |
| “当前状态快照”、attempt4 交付状态、一次运行的页数、某次交付的图片选择 | 描述特定实验/交付，不是系统规则。 | `outputs/` 报告、交付内部目录、Git 历史。 |
| 当前阻塞、下一步、一次 PPT 几何/视觉审阅发现 | 很快过期，属于活动任务。 | 当前 Codex 会话、issue/实验报告。 |
| D-014 中的 API 超时、重试事故、测试总数、提交、GitHub 443 失败和运行时间线 | 有追溯价值，但不是长期架构决策。 | Git 历史、运行日志、输出报告。 |
| V2/V3 实验行及当前 V4 执行进度 | 属于特定实验并由报告或当前会话拥有。 | `outputs/ppt_style_*`、实验报告、Git 历史。 |

## UNCERTAIN

| 材料 | 原因 | 处理方式 |
| --- | --- | --- |
| 未来 H5 审阅和企微集成 | 曾被计划但未实现，且不属于当前方案管线。 | 不写入长期决策；实施获批后再建立决策。 |
| 未来显式只读 CLI 数据库选择参数 | 旧文件标记为待决定，本任务未解决。 | 保持未决定，不在 Spec 中承诺。 |
| PPT Style V4 / Art Director 实现细节 | 另一工作区仍在开发；本审计没有检查或修改其脚本/outputs。 | 稳定后，其已确认架构方向可成为后续决策。 |
