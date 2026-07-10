#parser.py

"""MinerU 官方 SDK 封装：解析 PPTX 文件，按 slide（page_idx）返回结构化内容。"""

import hashlib
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

from mineru import MinerU

# MinerU 官方 SDK 使用 MINERU_TOKEN 环境变量
MINERU_TOKEN = os.environ.get("MINERU_TOKEN", "")

# 轮询间隔（秒）：每隔多久查一次任务状态
_POLL_INTERVAL = 10

# 最大等待时间（秒）：超过这个时间抛出异常
_MAX_WAIT = 1800

# 入库时忽略的 block 类型（页脚等噪音内容）
_IGNORED_TYPES = {"page_footnote"}

# 调试用 zip 包的保存目录：每个源文件各留一份最新结果，重新解析时覆盖旧的，
# 不同源文件之间互不覆盖，方便排查是哪个 pptx 解析出的问题。
_DEBUG_ZIP_DIR = Path("debug_zips")

# 图片素材的保存目录：按 pptx 文件名分子目录存放提取出的图片。
# 每次重新解析同一个 pptx 时会先清空对应子目录再重新提取，避免新版本
# slide/图片数量变少后，旧版本残留的图片文件混在里面。
_IMAGES_DIR = Path("images")


class MinerUParser:
    """调用 MinerU 官方 SDK 解析 PPTX，按 page_idx 把内容分组成 slide。"""

    def __init__(self) -> None:
        if not MINERU_TOKEN:
            raise ValueError("请设置 MINERU_TOKEN 环境变量")
        self._client = MinerU(MINERU_TOKEN)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    @staticmethod
    def compute_file_hash(file_path: str | Path) -> str:
        """计算 pptx 文件内容的 MD5，用于判断相比上次入库是否发生了变化。

        纯本地文件读取和哈希计算，不涉及任何网络调用，用来在真正调用
        MinerU（花钱）之前先判断这次 ingest 是不是在处理一个没变过的文件。
        """
        file_path = Path(file_path)
        return hashlib.md5(file_path.read_bytes()).hexdigest()

    def parse_pptx(self, file_path: str | Path) -> list[dict]:
        """解析本地 PPTX，返回按 slide（page_idx）分好的内容列表。

        返回格式：
            [
              {
                "slide_number": 1,        # page_idx + 1
                "title": "封面标题",      # text_level == 0 的文字块，可能为空
                "content": "正文文字",    # 同页其余 text/list/table/image 拼接
                "source_file": "xxx.pptx"
              },
              ...
            ]
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 第一步：提交任务，立刻拿到 batch_id，不等待结果
        print(f"  提交解析任务: {file_path.name}")
        batch_id = self._client.submit(str(file_path), model="vlm")

        # 第二步：每隔 _POLL_INTERVAL 秒查一次状态，直到完成或超时
        zip_bytes = self._poll_until_done(batch_id, file_path.name)

        # 第三步：从 zip 包里取出 content_list.json，按 page_idx 分组
        return self._split_by_page(zip_bytes, file_path.name)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _poll_until_done(self, batch_id: str, file_name: str) -> bytes:
        """轮询任务状态，完成后返回结果 zip 包的原始字节。

        Args:
            batch_id: submit() 返回的批次 ID
            file_name: 用于打印日志，以及命名调试用 zip 包

        Returns:
            MinerU 返回结果 zip 包的字节（内含 content_list.json / _model.json / full.md）

        Raises:
            TimeoutError: 超过 _MAX_WAIT 秒仍未完成
            RuntimeError: 任务失败，或返回结果中没有 zip 包
        """
        deadline = time.monotonic() + _MAX_WAIT
        elapsed = 0

        while True:
            # 查一次状态，get_batch 立刻返回，不阻塞
            results = self._client.get_batch(batch_id)
            result = results[0]

            if result.state == "done":
                if not getattr(result, "_zip_bytes", None):
                    raise RuntimeError("MinerU 返回结果中没有 zip 包，无法提取 content_list.json")

                # 调试用：按源文件名保存 zip 包，方便排查解析结构问题。
                # 同一文件重新解析会覆盖旧的调试包（不保留历史版本），
                # 不同文件各自独立命名，互不覆盖。
                _DEBUG_ZIP_DIR.mkdir(exist_ok=True)
                debug_zip_path = _DEBUG_ZIP_DIR / f"{Path(file_name).stem}.zip"
                debug_zip_path.write_bytes(result._zip_bytes)
                print(f"  已保存调试用 zip 包到: {debug_zip_path.resolve()}")

                return result._zip_bytes

            if result.state == "failed":
                raise RuntimeError(f"MinerU 解析失败: {result.error}")

            # 还在运行中，打印进度
            if result.progress:
                p = result.progress
                print(f"  [state={result.state}] {p.extracted_pages}/{p.total_pages} 页（已等待 {elapsed}s)")
            else:
                print(f"  [state={result.state}]（已等待 {elapsed}s)")

            # 检查是否超时
            if time.monotonic() > deadline:
                raise TimeoutError(f"解析超时（>{_MAX_WAIT}s),batch_id={batch_id}")

            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    @staticmethod
    def _split_by_page(zip_bytes: bytes, source_file: str) -> list[dict]:
        """从 zip 包中读取 content_list.json，按 page_idx 把 block 分组成 slide。

        content_list.json 是扁平的 block 列表，每个 block 用 page_idx 标明属于第几页
        （从 0 开始）。这比对 Markdown 做字符串切分稳定得多——MinerU 对 PPTX 输出的
        Markdown 是连续的（标题用 "##"，没有 "---" 分隔符），按分隔符切分本来就不可靠。

        同时会把每页里的图片提取到本地 images/ 目录（见 _render_page /
        _extract_image），所以这里需要保持 zip 处于打开状态，而不是像之前
        那样只读 content_list.json 就关闭。
        """
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            blocks = MinerUParser._read_content_list(zf)

            # 重新解析同一个 pptx 前，先清空它专属的图片子目录，避免新版本
            # 图片变少之后，旧版本残留的图片文件还留在目录里造成脏数据。
            images_dir = _IMAGES_DIR / Path(source_file).stem
            if images_dir.exists():
                shutil.rmtree(images_dir)

            # 按 page_idx 分组，page_idx 从 0 开始
            pages: dict[int, list[dict]] = {}
            for block in blocks:
                page_idx = block.get("page_idx", 0)
                pages.setdefault(page_idx, []).append(block)

            slides = []
            for page_idx in sorted(pages.keys()):
                slide_number = page_idx + 1
                title, content, images = MinerUParser._render_page(
                    pages[page_idx], zf, source_file, slide_number
                )
                slides.append({
                    "slide_number": slide_number,
                    "title": title,
                    "content": content,
                    "source_file": source_file,
                    # 每项为 {"path": 本地图片路径, "caption": 图片描述}，
                    # caption 可能是空字符串（不是所有图片都有说明文字）
                    "images": images,
                })

            return slides

    @staticmethod
    def _read_content_list(zf: zipfile.ZipFile) -> list[dict]:
        """从已打开的 zip 中找到 content_list.json 并解析成 block 列表。"""
        content_list_name = next(
            (name for name in zf.namelist() if name.endswith("content_list.json")),
            None,
        )
        if content_list_name is None:
            raise RuntimeError(
                "zip 包中未找到 content_list.json，请检查 debug_zips/ 下"
                "对应调试包的实际目录结构"
            )

        with zf.open(content_list_name) as f:
            return json.load(f)

    @staticmethod
    def _render_page(
        blocks: list[dict],
        zf: zipfile.ZipFile,
        source_file: str,
        slide_number: int,
    ) -> tuple[str, str, list[dict]]:
        """把同一页的 block 列表渲染成 (title, content, images)。

        - type == "text" 且 text_level == 0：作为 slide 标题（取第一个出现的）
        - type == "text" 且 text_level 为其他数字：子标题，保留 Markdown 层级
        - type == "text" 且没有 text_level：正文
        - type == "list"：list_items 逐条拼接
        - type == "table"：table_caption + table_body 按行拼接
        - type == "image"：图片文件提取到本地（不管有没有 caption 都保留）；
          caption 非空时额外拼进正文，让图片描述也能参与语义检索——caption
          为空的图片依然会出现在返回的 images 列表里，只是不影响这页文字
          的检索命中，方案员搜到这页时照样能拿到对应的图片素材。
        - type == "page_footnote"：忽略（页脚噪音，如公司名）
        """
        title = ""
        parts: list[str] = []
        images: list[dict] = []
        image_idx = 0

        for block in blocks:
            block_type = block.get("type")

            if block_type in _IGNORED_TYPES:
                continue

            if block_type == "text":
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                text_level = block.get("text_level")
                if text_level == 0 and not title:
                    title = text
                elif text_level is not None:
                    parts.append(f"{'#' * (text_level + 1)} {text}")
                else:
                    parts.append(text)

            elif block_type == "list":
                items = block.get("list_items") or []
                parts.extend(str(item).strip() for item in items if str(item).strip())

            elif block_type == "table":
                caption = (block.get("table_caption") or "").strip()
                if caption:
                    parts.append(f"[表格] {caption}")
                for row in block.get("table_body") or []:
                    row_text = " | ".join(str(cell).strip() for cell in row)
                    if row_text.strip(" |"):
                        parts.append(row_text)

            elif block_type == "image":
                caption = (block.get("image_caption") or "").strip()
                img_path_in_zip = block.get("img_path", "")

                if img_path_in_zip:
                    local_path = MinerUParser._extract_image(
                        zf, img_path_in_zip, source_file, slide_number, image_idx
                    )
                    if local_path is not None:
                        images.append({"path": str(local_path), "caption": caption})
                        image_idx += 1

                if caption:
                    parts.append(f"[图片] {caption}")

        return title, "\n".join(parts).strip(), images

    @staticmethod
    def _extract_image(
        zf: zipfile.ZipFile,      # 已经打开的 Zip 文件对象，相当于操作系统的“句柄”。
        img_path_in_zip: str,     # 图片在压缩包内的相对路径（比如 images/page_1_0.png）。
        source_file: str,         # 原始 PPTX 的文件名（用于在本地创建对应的文件夹，把图片分类）。
        slide_number: int,        # 当前处理的是第几页（用于给图片重命名）。
        image_idx: int,           # 当前页的第几张图（防止多图同页，名字冲突）。
    ) -> Path | None:
        """把 zip 包内的一张图片提取到本地 images/<pptx文件名>/ 目录。

        文件名固定为 slide_{页码}_{页内图片序号}，同一 pptx 重新解析时
        _split_by_page 会先清空整个子目录，保证目录里只留最新一次解析
        的图片，不会和旧版本的文件混在一起。

        Returns:
            提取成功返回本地文件路径；zip 里找不到这个图片（理论上不
            应该发生，但留一层保护）则返回 None，调用方会跳过这张图。
        """
        try:
            img_bytes = zf.read(img_path_in_zip)
        except KeyError:
            print(f"  警告: zip 中找不到图片 {img_path_in_zip}，跳过该图")
            return None

        ext = Path(img_path_in_zip).suffix or ".jpg"
        out_dir = _IMAGES_DIR / Path(source_file).stem
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / f"slide_{slide_number}_{image_idx}{ext}"
        out_path.write_bytes(img_bytes)
        return out_path