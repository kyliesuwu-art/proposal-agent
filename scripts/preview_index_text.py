"""把已有 MinerU debug zip 渲染为拟入库文本的 Markdown 预览。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.parser import MinerUParser  # noqa: E402


def _raw_summary(blocks: list[dict]) -> str:
    """给不入库页面保留便于人工核对的简短原始 block 摘要。"""
    lines = []
    for index, block in enumerate(blocks, 1):
        block_type = block.get("type", "unknown")
        value = (
            block.get("text")
            or block.get("table_caption")
            or block.get("image_caption")
            or block.get("img_path")
            or ""
        )
        compact = " ".join(str(value).split())
        if len(compact) > 180:
            compact = f"{compact[:177]}..."
        lines.append(f"- #{index} `{block_type}`{f': {compact}' if compact else ''}")
    return "\n".join(lines) or "- （本页没有可摘要的 block）"


def render_preview_markdown(zip_path: str | Path) -> str:
    """读取一个缓存 zip 并输出逐页 Markdown；不访问外部服务或本地数据库。"""
    zip_path = Path(zip_path)
    pages = MinerUParser.preview_debug_zip(zip_path)
    source_file = zip_path.name.removesuffix(".zip")
    lines = [
        "# 拟入库文本预览",
        "",
        f"- Source 文件名：`{source_file}`",
        f"- Debug zip：`{zip_path}`",
        "- 本预览仅读取 `content_list.json`；图片 caption 为 MinerU fallback，未调用视觉模型。",
        "",
    ]

    for page in pages:
        lines.extend([
            f"## Page {page['slide_number']}",
            "",
            f"- page_number：{page['slide_number']}",
            f"- indexable：`{str(page['indexable']).lower()}`",
        ])
        if page["toc_reasons"]:
            lines.append(f"- 目录页规则：{'；'.join(page['toc_reasons'])}")
        lines.extend([f"- title：{page['title'] or '（无）'}", ""])

        if not page["indexable"]:
            lines.extend([
                "> 本页保留原始解析结果和页码，但**不入库**、不生成 embedding。",
                "",
                "### Raw 摘要",
                "",
                _raw_summary(page["raw_blocks"]),
                "",
            ])

        lines.extend(["### 最终 index_text", "", "```text"])
        index_text = MinerUParser.build_index_text(page)
        lines.append(index_text or "（空）")
        lines.extend(["```", "", "### 表格 Markdown", ""])
        if page["table_markdown"]:
            for number, table in enumerate(page["table_markdown"], 1):
                lines.extend([f"#### 表格 {number}", "", table, ""])
        else:
            lines.extend(["（无）", ""])

        lines.extend(["### 最终图片 caption（MinerU fallback）", ""])
        captions = [image.get("caption") for image in page["images"] if image.get("caption")]
        if captions:
            lines.extend(f"- [图片] {caption}" for caption in captions)
        else:
            lines.append("（无；无 caption 的图片仍保留在 images metadata）")
        lines.extend(["", f"### 图片（{len(page['images'])} 张）", ""])
        if page["images"]:
            lines.extend(
                f"- `{image['path']}`{f" — {image['caption']}" if image.get('caption') else ''}"
                for image in page["images"]
            )
        else:
            lines.append("（无）")
        lines.append("")

    return "\n".join(lines)


def write_preview(zip_path: str | Path, output_path: str | Path) -> Path:
    """渲染并写入报告；输出文件由调用者指定，适合放在已忽略目录。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_preview_markdown(zip_path), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="从已有 MinerU debug zip 生成拟入库文本预览")
    parser.add_argument("debug_zip", type=Path, help="包含 content_list.json 的本地 debug zip")
    parser.add_argument("output", type=Path, help="输出 Markdown 文件路径（建议放在 debug_zips/ 下）")
    args = parser.parse_args()

    if not args.debug_zip.is_file():
        parser.error(f"debug zip 不存在: {args.debug_zip}")
    output = write_preview(args.debug_zip, args.output)
    print(f"已生成预览: {output.resolve()}")


if __name__ == "__main__":
    main()
