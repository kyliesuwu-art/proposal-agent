# 2A CLI 审计记录

基线：9cebbf7。`src/main.py` 的模块导入会调整 sys.path 并加载项目环境；它导入业务适配器但不主动构造 LLM 或数据库。`main()` 使用 sys.argv，混合手写解析和 argparse。query 调用 pipeline；proposal 初始化 LLM、检索和写入日志/产物；generate 组装 ProposalWorkflow；render/build 操作本地文件；ingest resume 可写目标库；reindex 为预检；diagnose 和 audit 读取输入并写报告。`_ingest_path` 是未注册的历史辅助函数。日志函数、工厂及模块级依赖被现有测试直接调用或替换，因此保留在入口模块，分发时动态获取。脚本方式和 `from src import main` 均须兼容。

最小方案：将现有分发迁至 CLI，维护及诊断的解析与适配分别独立，入口保留组成根和兼容符号；不改解析逻辑、核心实现及副作用。现有业务命令分支仍在 CLI 中，后续可在单独治理中细化，不在本轮改业务。

基线快照包含 27 个帮助、缺参、非法参数及退出状态案例；对 query 和 diagnose 的业务调用设置拦截。已有 workflow 测试覆盖 generate 请求、正常输出、FATAL、失败阶段与真实离线模块。快照不是所有参数组合的穷举；依赖参数原样迁移与现有测试共同验证。历史风险：query 的 `--help` 被当成查询，多个手写解析命令没有标准帮助；全局 sys.argv、导入时环境加载与入口模块级注入耦合继续保留，避免破坏兼容性。
