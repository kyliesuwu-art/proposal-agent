# 离线知识库一致性检查

本工具不接入生成或入库流程。**本轮只对 pytest 临时数据运行；检查真实知识库快照必须另获用户授权。** 检查器只接受系统临时目录下明确声明为可丢弃的目录，拒绝副本中的符号链接。Chroma `PersistentClient` 可能修改检查副本；不能将源库、封存副本或正在运行的数据库传给它。

命令（退出码：PASS/WARNING 为 0，BLOCKED 为 1）：

```powershell
python -X utf8 -m src.kb_consistency_check <系统临时目录中的可丢弃副本> --disposable-copy --attestation <副本内的确认清单.json>
```

确认清单由有权人员依据可信原始文件和入库记录人工编制；检查器不自动生成或验证其真实性。`zip_inventory_complete` 需要确认缓存清单覆盖本次验收范围，不可仅因为缓存清单为空就机械填写 true。例子中所有值都是虚构的：

```json
{
  "confirmed_by": "审查人",
  "basis": "可信原始文件和入库记录的人工核对说明",
  "zip_inventory_complete": true,
  "documents": {"doc_example": "ver_example"}
}
```

检查器要求 `hybrid_lexical.sqlite3`、`chroma_v2/chroma.sqlite3`、`cache_ingest_manifest.sqlite3` 和 `electrical_pages_v2` collection；读取 SQLite 使用 `mode=ro`，复用已有 `_read_integrity` 与 `NoRemoteEmbedding`。核对 pages、FTS5 的 ID 集合、重复和存储字段、Chroma 的 ID/metadata，核对文档/版本/source key 的对应关系及缓存清单可检测的碰撞；多版本、缺页、缺字段、损坏、空库、异常、缺少确认清单或不匹配均 BLOCKED。相同 ZIP hash 被记录为不同来源名称时 WARNING。工具只验证确认清单内容与索引相符，**不会认证确认人、来源真伪、清单完整性或当前版本权威性**；依赖人工确认，不能根据哈希或分数推断版本。

`PASS` 是基于列出的检查和人工声明的必要条件，不是对知识库质量、真实模型检索、Chroma/HNSW 和 FTS 内部索引完整性、向量准确性或来源事实真实性的全面保证。`WARNING` 可进入下一阶段，但需人工审阅；`BLOCKED` 不得进入。报告列出各项依据、发现、未完成检查与局限，不输出页面正文。[示例报告](kb_consistency_example.json)来自单页合成测试库和虚构确认清单，不代表实际知识库结果。

## 静止快照操作方案（未经授权不得执行）

1. 获授权后由数据负责人停止所有入库、生成及可能修改 Chroma 的进程，确认无写入者；对底层 SQLite/WAL 和 Chroma 文件使用数据库/存储层支持的一致快照机制，包含 SQLite 数据库及其 WAL/SHM、完整 Chroma 目录、sidecar、图片和缓存清单。仅复制主 SQLite 文件不能证明一致性。
2. 完成后记录快照范围、时间、静止证据和校验值，封存原快照；由负责人在无并发写入条件下创建系统临时目录中的第二份可丢弃检查副本，并核对复制完整性。检查器本身不能证明源库已静止或复制一致。
3. 根据可信材料独立审核版本和 ZIP 覆盖范围，人工编制副本内确认清单；执行检查并保存报告。任何 BLOCKED 或未核实关键证据均停止验收。检查副本可能被 Chroma 写入，不可当作封存原件或直接替代正式库。
4. 不自动删除、修复、迁移、重建或切换版本；若需业务生成，另行制定授权与受控运行方案。
