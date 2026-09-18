# 企业微信任务命令

命令在普通需求、澄清和方案意图判断之前进行严格完整匹配。命令可忽略输入前后空格；`Word` 不区分大小写。任务编号使用当前 12 位小写十六进制 Task ID。

| 命令 | 别名 | 前置状态 | 创建 Job | 模型调用 | 文件发送 | 重试 | 重复 msgid |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 帮助 | `help` | 无 | 否 | 否 | 否 | 不适用 | 静默去重 |
| 状态 | `任务状态`、`状态 <task_id>` | 当前会话可访问的 Task | 否 | 否 | 否 | 不适用 | 静默去重 |
| 重发Word | `重新发送Word`、`重发Word <task_id>` | 当前会话可访问的成功 Word Job | 否 | 否 | 是，重新发送已有文件 | 静默去重，不重复发送 |
| 确认 | `确认` 等既有确认别名 | 唯一 `WAITING_MD_APPROVAL` Task | 否 | 否 | 否 | 不适用 | 既有 Task 去重 |
| 上传 Markdown | `.md`/`.markdown` 文件 | 唯一 `WAITING_MD_APPROVAL` Task | 否 | 否 | 否 | 不适用 | 既有 Task 去重 |
| 生成Word | `生成 Word`、`导出Word`、`导出 Word` | `MD_APPROVED` Task | 是 | Word Runner 按既有主线决定 | 完成后发送 | 失败后使用重新生成Word | 既有 Task 去重 |
| 重新生成Word | 无 | 最近 Word Job 失败 | 是 | Word Runner 按既有主线决定 | 完成后发送 | 用户显式 | 既有 Task 去重 |

## 用户可见行为

- 帮助只返回简短命令列表，不创建 Task 或 ArtifactJob。
- 状态显示 Task 编号、Markdown 状态、Word 状态、更新时间与下一步建议；只能查看同一 `(userid, chatid)` 的 Task。
- 重发Word 只发送已存在、非空、位于该 Job 受控输出目录的 `.docx` 文件。不会创建 Job、启动 Runner、调用模型或修改 Markdown。
- 找到成功记录但文件不可用时，机器人提示发送“重新生成Word”；不会自动重新生成。
- 非法任务编号会收到安全格式提示，不会落入普通需求处理。

普通文本中出现“帮助”“状态”或“Word”不会被当作命令，例如“请帮助我生成医院配电方案”和“Word 中需要补充当前项目状态”。
