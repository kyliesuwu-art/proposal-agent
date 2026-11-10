# 2B 收口：受控验收准备

候选生产代码基线 `d24c756`；包含 2A CLI、2B-1 初始化安全、2B-2 来源身份修复，不包含版本发布实验原型。文档仅是检查设计与操作规范，不证明任何现有知识库已通过验收。

## 已有检查能力及空缺

- `src/v3_hnsw_rebuild.py::diagnose` 以 SQLite `mode=ro` 检查三个 SQLite 文件的完整性、pages/FTS 数量、sidecar 和 embedding 缓存覆盖；它要求相关三个数据库存在，不打开 Chroma，不检查完整 page_id 差集、同文档多版本或有效版本。
- `src/cache_batch.py::inspect_cache_directory` 只读检查缓存档案，报告 `duplicate_source_keys` 和 ZIP hash；实际恢复规则见 `src/cache_recovery.py`。它不证明索引中版本集一致，也不确认哪个版本有效。
- `src/v3_live_query_validation.py::validate` 虽有局部组件核对，但会打开 Chroma 并调用真实 DashScope embedding，禁止作为本轮的只读离线检查。HNSW rebuild 是写入新库操作，不是只读诊断。
- 现有临时数据测试验证了上述工具的部分行为；没有现成的端到端、无网络、完整版本一致性验收门禁。

## 只读检查的最小设计（待实现、待测试）

先停止所有写入及生成任务，以能保证一致性的方式获取包含 SQLite 数据库及其 WAL/SHM、完整 Chroma 和 sidecar/assets 的静止一致快照；核对复制完成和完整性，保留封存副本。不可在源库运行 Chroma PersistentClient。额外创建一次性检查副本，在该副本上检查 Chroma，因为其客户端即使只查询也可能维护文件；检查副本绝不能作为封存原件。

独立检查器只接收显式副本路径、不加载默认知识库、不访问 embedding/网络、不修改生产检索；对 SQLite 用 `mode=ro` 和完整性检查，对 Chroma 先检查目录和文件存在，使用禁用网络 embedding 的现有 guard，并分页读取 collection ID 与 metadata。其报告应包括：

1. 按 document_id 汇总 source_key 与 version_id：多版本、空身份、一个 document 对应多个 source_key 或一个 source_key 对应多个 document 均报告歧义；不根据哈希、RRF 或无序 LIMIT 1 推断活动版本。
2. 对 SQLite pages、FTS 和 Chroma 建立 page_id 集合及计数，比较缺失、重复、孤儿；核对 page_id、document_id、version_id、source_key、页码与 payload/Chroma metadata 是否一致，检查缺失 sidecar/图片。必要文件损坏、不可解析或检查不完整均应失败，而非通过。
3. 比较各 document/version 在 pages、FTS、Chroma 的集合；发现差异或多个版本应阻止验收。仅有一个版本也不能证明它已被权威发布；需业务负责人凭可信来源/入库记录明确确认，并保存证据。没有可信活动版本证据则状态为 UNKNOWN，不能标记 PASS。
4. ZIP 缓存源使用已有 inspection 报告重复 source_key，再与普通来源按完整 source_key/document_id 交叉检查；没有缓存或原始来源时标为未核实，不能推断无碰撞。
5. 输出机器可读报告及人可审阅摘要，仅使用逻辑标识，不包含凭据或机器绝对路径；分离 PASS / FAIL / UNKNOWN / NOT_CHECKED，不对不完整检查发放通过结论。不删除、修复、重建、迁移或调用计费 API。

实现前的测试计划：pytest 临时数据库和本地向量覆盖单/多版本、跨库差集、缺页和孤儿、重复 FTS 行、损坏 schema、metadata 冲突、ZIP 碰撞、缺失可信发布证据、非法路径、禁止网络调用和检查副本写隔离；同时用真实临时 Chroma+FTS 索引对比报告。检查工具本轮未实现，不能把该计划或已有局部测试等同于验收通过。

## MVP 受控使用规范

1. 入库或更新只在隔离候选库进行；生成任务和更新任务串行，禁止在验收期间运行入库、重建、索引修复或并发写入。
2. 每次候选库先静止、做完整快照并执行上面的检查和可信版本确认；多版本歧义、缺页、身份不一致、未检查或缺失权威证据均停止交付。检查通过后由负责人记录候选库身份、检查结果、代码提交和显式切换决定；不自动切换。
3. 封存的验证副本只作为不可修改的来源，生成使用从其派生的隔离运行副本；禁止应用层入库与人为更改，串行运行并记录运行副本和产物的身份。Chroma `PersistentClient` 可能对运行副本执行内部维护，因此当前代码**不保证运行副本在生成期间字节不变**。如验收必须要求生成所用副本绝对不可写，当前实现不能满足，应停止验收并先解决存储能力；不得以操作约定宣称已具备严格只读。
4. `HybridTestStore(readonly=True)` 只保证词法 SQLite 使用 `mode=ro` 且必要文件/表检查；Chroma 严格只读、在线并发更新、跨进程版本快照、自动恢复及活动版本过滤目前均无代码保证。静止采集、版本确认、任务串行、切换审批是操作流程，不是系统自动强制。
5. 本轮没有对当前正式库执行完整快照或检查，知识库有效版本和 ZIP 碰撞状态均为 UNKNOWN；候选代码的离线测试通过不能替代知识库验收。真实 LLM/embedding、客户业务数据和 Office 逐页视觉效果仍须另行受控验收。
