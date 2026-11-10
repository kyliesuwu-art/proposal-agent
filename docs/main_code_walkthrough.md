# 从 main.py 读懂方案智能体

本文对应当前代码。建议打开 `src/main.py`，从上往下阅读，再按下面的调用链跳转。这里的“入口”指程序从哪里开始执行，不表示所有工作都在入口文件里完成。

## A. 逐段阅读 main.py

1. 文件首行的三引号字符串是模块说明，导入模块不会执行这段文字。
2. `import sys` 导入 Python 的运行时模块；`from pathlib import Path` 导入表示文件路径的类。
3. `Path(__file__).resolve().parent.parent` 根据当前文件计算项目根目录；`sys.path.insert(0, ...)` 把它加入模块搜索路径，使 `python src/main.py` 可以找到 `src` 包。这会改变当前进程的模块搜索顺序，是保留的启动副作用。
4. `from src.config import load_project_environment` 引入已有配置函数，下一行调用它加载项目根目录的 `.env`。`src.config` 自身导入时也会调用一次环境加载；这是既有行为，不能误认为纯导入没有副作用。
5. 后面的 `from ... import ...` 导入已有业务函数、异常类型和类。它们是历史模块级接口，CLI 和现有调用方可以访问或替换这些名称；导入这些类并不等于立即创建 LLM 或打开知识库。
6. `cli_ingest_helpers` 与 `proposal_logging` 的重导出保留 `_ingest_path`、缓存入库函数、日志与请求记录函数的旧导入路径。它们的实现分别位于各自模块。
7. `_proposal_workflow()` 是无参数函数，调用 `build_proposal_workflow(...)` 并返回一个 `ProposalWorkflow` 对象。各个 `名字=值` 是关键字参数：例如 `retriever=pipeline.retrieve_evidence` 传递的是函数本身，并没有在这一行调用它。`workflow_class=ProposalWorkflow` 保留原有替换工作流类的能力。
8. `main()` 在调用时导入 `dispatch`，然后把 `sys.modules[__name__]` 所表示的当前模块传给它。CLI 通过该对象访问历史依赖替换点；CLI 不反向导入 `src.main`。这个参数是兼容手段，也意味着入口与 CLI 仍通过模块属性耦合。
9. `if __name__ == "__main__": main()` 仅在文件作为程序运行时调用 `main()`；被 `import src.main` 时不会自动执行命令。直接执行脚本时模块名是 `__main__`，本入口不会再次导入 `src.main`；如果其他外部代码之后主动同时导入两种名称，Python 仍可能创建两个模块对象。

兼容导入逐行看：`pipeline` 提供查询及检索函数；`SUPPORTED_EXTENSIONS` 描述旧入库支持的文件类型；`LLMClient` 是模型客户端类；`markdown_proposal` 一行导入生成函数、诊断函数及异常类型；`render_word` 和 `render_pptx` 两行导入渲染/审计函数及对应异常；`application` 一行导入请求、工作流和工作流异常。`cli_ingest_helpers` 一行重导出三个旧入库辅助函数；`proposal_logging` 一行重导出两个记录函数；`bootstrap` 一行导入组装函数。这些名称并非全部由入口直接调用，但保留它们可以避免改变旧调用和 CLI 依赖获取方式。

当前帮助文本保存在 `src/cli_help.py`，以保持既有 CLI 输出，虽然其中部分历史描述仍称入口负责解析。这是输出兼容文本，不应当作为现有模块职责说明。

## B. 一次 generate 的实际调用链

命令 `uv run python src/main.py generate "医院光伏方案" --formats md docx pptx` 的流程：

1. Python 运行 `src/main.py`，完成路径和环境初始化；`main()` 调用 `src.cli.dispatch`。
2. `dispatch` 读取 `sys.argv`，在 `generate` 分支用 `argparse` 解析需求和 `--formats`；它构造 `GenerateRequest`，然后调用入口兼容工厂 `_proposal_workflow()`。
3. 该工厂把 `LLMClient`、`pipeline.retrieve_evidence`、`generate_markdown_proposal`、Word/PPT 渲染函数及日志函数传给 `src.bootstrap.build_proposal_workflow`。组装函数导入 `build_slides_markdown` 并创建 `ProposalWorkflow`，此时还没有调用 LLM 或检索器。
4. `ProposalWorkflow.run` 检查请求与输出目录，初始化日志和 LLM 客户端，再将已注入的检索器传给 `generate_markdown_proposal`。该函数先规划，再按章节调用检索器和 LLM，最后生成 `proposal.md` 和来源 sidecar。真实调用会使用配置的外部服务，测试使用替身。
5. 请求了 `docx` 时，工作流调用 `render_word` 读取 Markdown；请求了 `pptx` 时，先调用 `build_slides_markdown`，再调用 `render_pptx`。它们是已有 Markdown 的下游消费者。
6. 工作流返回 `GenerateResult`；CLI 打印生成路径。异常由工作流和 CLI 边界按既有阶段与退出状态处理，运行日志由 `proposal_logging` 写入。

## C. Python 基础概念

- **import**：让当前模块使用其他模块的名称，例如 `from src.config import load_project_environment`。首次导入通常会执行被导入模块的顶层代码。
- **function 和调用**：`main`、`build_proposal_workflow` 是函数；`main()` 的括号表示调用。函数的输入是参数，`return` 把结果交给调用方；没有显式返回值时返回 `None`。
- **class 和 object**：`ProposalWorkflow` 是类，调用它创建工作流对象。类描述对象的行为；对象保存该次组装时使用的依赖。
- **`__init__` 和 `self`**：`ProposalWorkflow.__init__` 在创建对象时接收依赖；`self` 指正在初始化或操作的那个对象，`self._retriever` 保存它的检索函数。
- **依赖注入**：组装代码把 `pipeline.retrieve_evidence` 和其他函数传给工作流，而不是让工作流自己硬编码构造它们。测试可以传入 FakeRetriever；函数被传递不表示已经执行。

## D. 代码中的架构边界

- **入口层**：`main.py` 完成启动；`cli.py` 负责命令解析、分发和命令级错误展示，维护与诊断命令使用 `cli_admin.py`、`cli_diagnostics.py`。
- **应用层**：`src/application/proposal_workflow.py` 协调生成和渲染的先后顺序及失败阶段，不实现检索算法。
- **业务逻辑**：`markdown_proposal.py` 负责方案编排与引用规则；`pipeline.py` 中的检索入口负责检索编排。它们仍含既有历史职责，并非本轮全部拆分。
- **基础设施**：`hybrid_v2.py`、`adapters/llm_client.py` 分别连接知识库存储和模型服务；`render_word.py`、`render_pptx.py` 负责本地文档生成。
- **封装与解耦**：调用方通过函数、`GenerateRequest` 和 `QueryResult` 交互，不必直接操作每个存储细节。工作流依赖可调用对象而非自行创建具体检索器，是依赖注入；从工作流看，它依赖所需行为而非固定数据库构造，是有限的依赖倒置。
- **尚未完成的边界**：CLI 仍读取传入的入口模块属性，入口仍保留兼容导出与环境加载；`pipeline.retrieve_evidence` 仍直接构造具体存储。项目没有引入通用依赖注入容器，也没有实现严格只读的 Chroma。
