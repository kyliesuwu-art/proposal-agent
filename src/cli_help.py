"""保留已有命令帮助输出。"""

CLI_HELP = """方案知识库的命令行入口。

本文件只负责解析命令行参数和调用业务模块；检索、生成、渲染等具体逻辑分别在
``pipeline.py``、``markdown_proposal.py``、``render_word.py`` 和 ``render_pptx.py`` 中。

核心业务命令：
    # 查询知识库，将检索结果输出为 Markdown。
    uv run python src/main.py query "需求描述" [--output outputs/query.md]

    # 使用 RAG 检索和大模型生成方案 Markdown。
    uv run python src/main.py proposal "需求描述" --output outputs/proposal.md [--debug] [--run-log outputs/proposal.run.log]
    uv run python src/main.py generate "需求描述" --formats md docx pptx [--output-dir outputs/generate-run]

    # 将已有方案 Markdown 渲染为 Word。
    uv run python src/main.py render-word --input outputs/proposal.md --output outputs/proposal.docx

    # 从方案 Markdown 生成适合演示的中间 Markdown 和幻灯片计划。
    uv run python src/main.py build-slides --input outputs/proposal.md --output outputs/slides.md [--sources outputs/proposal.sources.json] [--mode briefing] [--max-slides 15]

    # 将方案或中间 Markdown 渲染为 PPTX；可复用 build-slides 生成的计划。
    uv run python src/main.py render-pptx --input outputs/slides.md --output outputs/proposal.pptx [--plan outputs/slides.slide-plan.json]

数据库维护命令：
    # 只读检查 MinerU 缓存目录，不打开目标数据库。
    uv run python src/main.py ingest-cache-dir <缓存目录> --db <候选数据库目录> --dry-run

    # 将缓存恢复并分批写入明确指定的候选数据库。
    uv run python src/main.py ingest-cache-dir <缓存目录> --db <候选数据库目录> --resume --limit 10 --batch-size 32

    # 只读生成受影响文档的定向重建预检报告；当前不执行实际重建。
    uv run python src/main.py reindex-affected --cache-dir <缓存目录> --source-db <源数据库目录> --target-db <候选数据库目录> --plan <计划文件> --dry-run

诊断命令：
    # 检查已有方案 Markdown，并输出可移植的诊断报告。
    uv run python src/main.py proposal-diagnose outputs/proposal.md [--output outputs/proposal.diagnosis.md]

    # 检查 PPTX 几何布局；未提供页面渲染图时，视觉检查会标记为受阻。
    uv run python src/main.py audit-pptx --input outputs/proposal.pptx --plan outputs/slides.slide-plan.json --output-dir outputs/pptx-audit
"""
