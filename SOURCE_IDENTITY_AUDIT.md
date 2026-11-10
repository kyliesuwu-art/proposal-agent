# 2B-2 来源身份离线验证

基线 f91c271；仅新增测试和本文，不改生产代码或知识库。测试用 pytest 临时目录中的两份 PDF 名称及不同内容字节构造身份，并直接写入两条模拟解析页（并未解析 PDF）；临时 Chroma + FTS5 使用 LocalHashEmbeddingFunction，三章由 FakeLLM 确保每章引用两条不同证据，无外部服务。实际语义查询与 FTS `PCS` 各召回两条；多查询使用 `医院 光伏 容量` 和 `PCS`。

| 层 | 医院 A，500 kW | 医院 B，800 kW |
| --- | --- | --- |
| source_key / source_file | 医院A/技术方案.pdf | 医院B/技术方案.pdf |
| document_id | doc_fb649462ee46576b9761c636afaa141b | doc_1d6c60a092b85d04b629fe538732a96b |
| version_id | ver_sha256_231de28b600f3998ce6970e4b81ded0c2585c357f898187d6989fe407131073d | ver_sha256_6ce6c7414afcb12cf1849722818005e849fc99eee143c7d399d272187654ecca |
| page_id | A document_id + A version_id + :p12 | B document_id + B version_id + :p12 |
| QueryResult / SearchHit | 独立 hit，metadata 保留 document_id、version_id、page_id、source_key | 独立 hit，metadata 保留各自身份 |
| EvidenceItem | S1、医院A/技术方案.pdf、第 12 页 | S2、医院B/技术方案.pdf、第 12 页 |
| Markdown 可见引用 | [来源: 技术方案.pdf, 第 12 页] | [来源: 技术方案.pdf, 第 12 页] |
| sidecar | S1、技术方案.pdf、[12] | 与 A 合并到同一 S1 |
| Word | 保留 500 kW 和相同可见引用 | 保留 800 kW 和相同可见引用 |
| PPT | 500 kW 所在内容页 source_ids 为 [S1] | 800 kW 所在内容页 source_ids 亦为 [S1] |

检索与 EvidenceItem 仍分别保留不同 source_key；`markdown_proposal._evidence` 只把文件名、页码等带入 EvidenceItem，首次丢弃 document_id、version_id、page_id，但两个 source_key 尚可区分。`_render_body` 经 `_display_name` 生成相同 basename+页码引用，是人类可见身份首次变得不可区分的位置；`_render` 的来源列表同样按 basename 分组。`_source_manifest` 从已歧义的 Markdown 按 basename+页码去重，首次把两份来源实质合并为一个 sidecar S1。`render_pptx._ids_for` 再按文件名和页码映射，两条内容因此均关联这个 S1。PPT 测试验证的是计划 source_ids 和实际 PPT 文本，不声称 PPT 存在独立可恢复的底层文档身份。

正确性测试初次实际失败：期望两个 sidecar 来源，实际只有一个。该断言保留并标为 strict xfail；另有通过的行为刻画测试明确检查 Word、PPT 和冲突。首次运行夹具只给一章被既有 3–8 章规划约束拒绝；修正为三章后，中文 FTS 全词匹配未召回又调整为共同的 PCS 术语。最终分别证明语义、词法和多查询均命中两条，FakeLLM 断言读取两条不同证据后才引用，排除了这些夹具问题。

## 兼容修复方向（待后续阶段实施）

- 沿 SearchHit → EvidenceItem → 最终渲染保留稳定 document_id、version_id、page_number 与可移植 source_key；旧 hit 缺字段时采用明确兼容策略，不能把 basename 假当唯一键。
- 冲突时生成不包含绝对路径的可读区分标签；对非冲突文档尽量保留现有引用外观。需先确定标签与现有质量门槛和引用解析器兼容，禁止泄露机器路径。
- 构建 sidecar 时从结构化的已用证据建立来源唯一映射，而不是从已丢失身份的可见文本反推；保存文档、版本和页信息，并让每个实际来源有唯一 source_id。
- 协同更新 PPT 的引用映射和来源显示，Word 保持可读文本；用明确的 schema 兼容分支读取历史 sidecar。不能从历史同名合并记录凭空恢复已经丢失的身份，应明确提示歧义。
- 不改知识库或索引格式；使用现有 metadata。补充同名同页、同名不同页、版本更替、路径隐私、旧 sidecar 与 Word/PPT 回归。任何单改检索去重都不能修复交付层的二次合并。
