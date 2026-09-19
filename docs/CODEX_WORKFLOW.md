# Codex 工作方式

使用完成当前任务所需的最小上下文。不要默认读取整个仓库或完整 `DECISIONS.md`。

## 普通小迭代

读取当前任务涉及的代码和测试 → 在范围内修改 → 运行相关离线测试 → 检查结果。默认不重新总结整个项目，也不做全仓审计。

## 架构或大功能任务

先读 `PROJECT_SPEC.md`、`DECISIONS.md` 中直接相关的决策，以及受影响代码/测试，再确定方向。只有新增或改变长期架构/产品决策时，才更新 `DECISIONS.md`。

## 阶段结束

仅当一个完整阶段结束且当前工作树可安全隔离时，再考虑 commit、push 和更广泛 review。临时进度、失败、指标和实验过程应保留在 Git 历史或对应报告中，不写进 `DECISIONS.md`。

## 发布或稳定版本

运行完整回归和适当的全仓审计；参考 `PROJECT_SPEC.md` 与相关架构决策。

## 阅读路由表

| 任务 | 默认读取 |
| --- | --- |
| 修小 bug | 相关源码和测试 |
| 调 PPT renderer | `PROJECT_SPEC.md`、相关 PPT 指南、renderer 和 renderer 测试 |
| 改 RAG/检索 | `PROJECT_SPEC.md`、检索代码和检索测试 |
| 改架构 | `PROJECT_SPEC.md`、相关 `DECISIONS.md` 条目、受影响代码/测试 |
| 新 Codex 接手长期工作 | `PROJECT_SPEC.md` 和当前模块 |
| 发布 | `PROJECT_SPEC.md`、测试和相关决策 |

四类信息各有归属：Spec 说明做什么；Tests 保证可量化不变量；Decisions 说明为什么；Git 与报告保留实现/实验历史。
