"""CLI adapters for admin commands."""

import json
import sys
from pathlib import Path

from src.render_pptx import RenderPptxError


def ingest_cache_dir(context) -> None:
    _ingest_cache_dir_dry_run = context._ingest_cache_dir_dry_run
    _ingest_cache_dir_resume = context._ingest_cache_dir_resume
    if len(sys.argv) == 6 and sys.argv[3] == "--db" and sys.argv[5] == "--dry-run":
        _ingest_cache_dir_dry_run(sys.argv[2], sys.argv[4])
    elif len(sys.argv) == 10 and sys.argv[3] == "--db" and sys.argv[5] == "--resume" and sys.argv[6] == "--limit" and sys.argv[8] == "--batch-size":
        _ingest_cache_dir_resume(sys.argv[2], sys.argv[4], int(sys.argv[7]), int(sys.argv[9]))
    else:
        print("用法: python main.py ingest-cache-dir <缓存目录> --db <候选库目录> --dry-run | --resume --limit <数量> --batch-size <数量>")
        sys.exit(1)

    # 【定向重建预检】验证计划和数据库路径；当前版本只支持不写库的 --dry-run。


def reindex_affected(context) -> None:
    import argparse
    parser = argparse.ArgumentParser(prog="python src/main.py reindex-affected")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(sys.argv[2:])
    if args.dry_run == args.resume:
        parser.error("必须且只能指定 --dry-run 或 --resume")
    from src.targeted_reindex import dry_run
    if args.resume:
        parser.error("实际定向重处理需要单独授权；本版本只提供不会创建候选库的 --dry-run")
    try:
        print(json.dumps(dry_run(args.plan, args.source_db, args.target_db, limit=args.limit), ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.error(str(exc))

    # 【知识库查询】将用户问题交给 pipeline 检索，并把结构化结果渲染为 Markdown。


