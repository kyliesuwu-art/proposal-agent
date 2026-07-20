# 操作参考手册

git 日常操作 + Claude Code 常用命令速查。
方便随时翻，不用记。命令块里用的是 PowerShell 语法（Windows 环境）。

---

## Git 日常操作
### 日常流程（每次改动后）
```powershell
git add .
git commit -m "简单描述这次改了什么，比如：修复引用溯源幻觉问题"
git push
```

### 大改动前先存档点
在做有风险的改动之前（比如接下来要动检索逻辑、动数据结构），先单独提交一次"存档点"，方便万一改坏了能退回来：
```powershell
git add .
git commit -m "checkpoint: 当前稳定可跑版本"
git push
```
**注意**：这里的 `checkpoint` 只是提交说明文字里的一个自定义标记，不是 git 的特殊功能，跟分支无关，只是让自己以后在历史记录里能一眼认出"这是个稳妥的存档点"。

### 查看上一次提交具体改了什么
```powershell
git show HEAD
```
或者：
```powershell
git diff HEAD~1 HEAD
```
这两个效果一样，精确对比"最近一次提交"和"再上一次提交"之间的差异。
**注意**：不要只写 `git diff HEAD~1`（不带第二个参数）——那样比较的是"上一次提交"和"你当前工作目录里的实际文件"，如果这中间你又有没提交的改动，会被一起算进去，看着容易 confuse。
- `HEAD`：当前所在的提交点
- `HEAD~1`：往前数一个提交

### 放弃这次没提交的改动，退回最近一次提交的状态
```powershell
git reset --hard HEAD
```
**注意**：这条退回的是"最近一次 commit 时的状态"，不是"更早一个版本"。
如果想真正退回到上一个提交（比当前状态还要早一步），要写成：
```powershell
git reset --hard HEAD~1
```
这条命令会丢弃所有未提交的改动，谨慎使用。

### 把已经被提交的文件/目录从 git 移除（但保留本地磁盘文件）
如果发现某个不该进仓库的文件/目录已经被 commit 过了：
```powershell
git rm -r --cached 目录名或文件名
```
配合 `.gitignore` 一起用：
- 如果还没 push 过，改完之后可以直接 `git commit --amend` 合并进原来的提交，保持提交历史干净
- 如果已经 push 过了，就用普通 commit 记一笔新的清理记录

### 常用检查命令
- `git status`：看现在有哪些改动还没提交
- `git log`：看提交历史
- `git ls-files`：看当前被 git 追踪的所有文件（排查有没有不该被追踪的东西时很有用）
---

## Claude Code 常用 slash 命令

这些是在 Claude Code 会话内部直接打出来控制行为的命令：
- `/compact`：把当前对话历史压缩成一份摘要，释放上下文空间，但保留"之前发生过什么"的记忆——适合会话进行很久、上下文快满的时候用，不用整个清空重来。
- `/clear`：彻底清空当前对话，开始一个全新的、不带任何历史包袱的会话（项目记忆即 claude.md 还在，只是清空聊天记录）。
- `/context`：查看当前上下文用了多少，快满了没有。
- `/resume`：恢复之前某次会话，可以接着聊。
- `/rewind`（也叫 `/checkpoint`，或者按两下 `Esc`）：回滚到之前某个检查点，可以选择只回滚代码、只回滚对话，或者两个都回滚——这个跟手动用 git commit/reset 做的事情类似，但是 Claude Code 自己内置的、更细粒度的版本。
- `/plan`（或 `Shift+Tab` 切换）：只读模式，Claude 只分析不动手，方案确认了再让它真正执行——审查习惯强的人适合多用这个模式。
- `/model`、`/effort`：切换模型和调整算力 / 推理深度档位。
---

## 并行开发多个功能（进阶，暂不急用）

如果以后需要同时推进两个互不依赖的功能（比如"修类型过滤"和"加图片描述"同时做），可以用 git worktree 让同一个仓库同时开出多个独立工作目录，每个目录对应一个分支，各开一个终端窗口、各跑一个 Claude Code 会话，互不干扰，做完再合并回主分支：

```powershell
git worktree add ../项目名-功能A -b feature/功能A
git worktree add ../项目名-功能B -b feature/功能B
```

每个目录里单独跑 `claude` 启动会话。全部完成后回到主目录合并：

```powershell
git checkout main
git merge feature/功能A
git merge feature/功能B
```

**备注**：这个目前用不上（项目还是一个人在推进单一功能线），先记下来，以后需求变多、要并行推进的时候再用。
