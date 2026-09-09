#!!parser.py再完善一下，后面要拆开，已经包含三个功能了，太乱了。
#hash码的算密码在这里，怎么比密码不在这里，后面要把整个链条化。
#存一份zipfile的这个功能，在后期项目上线了以后要记得删掉，这是只供开发调试写的代码。


#parser.py

"""MinerU 官方 SDK 封装：解析文档并按 page_idx 返回页面结构化内容。"""

import hashlib
import html
import io
import json
import os
import shutil

import re
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from mineru import MinerU
from src.config import DEBUG_ZIPS_DIR, IMAGES_DIR
from src.domain.models import ParseResult

# MinerU 官方 SDK 使用 MINERU_TOKEN 环境变量
MINERU_TOKEN = os.environ.get("MINERU_TOKEN", "")

# 轮询间隔（秒）：每隔多久查一次任务状态
_POLL_INTERVAL = 10

# 最大等待时间（秒）：超过这个时间抛出异常
_MAX_WAIT = 1800

# pending 阶段单独超时（秒）：任务提交后长时间卡在 pending（不转 running），
# 说明大概率是 MinerU 服务端任务丢失/队列拥堵，不用等满 _MAX_WAIT 才报错。
# 调大/调小这个常量即可调整 pending 阶段的耐心程度。
_PENDING_TIMEOUT = 300

# 入库时忽略的 block 类型（页脚等噪音内容）
# PDF/DOC 解析会额外产出 header/footer/page_number 等 block，PPTX 里通常没有
_IGNORED_TYPES = {"page_footnote", "header", "footer", "page_number"}
PARSER_VERSION = "mineru-blocks-v2"
INDEX_SCHEMA_VERSION = "proposal-index-v2"

# 支持解析的文件格式
SUPPORTED_EXTENSIONS = {".pptx", ".pdf",  ".docx",  ".doc"}

# 调试用 zip 包的保存目录：每个源文件各留一份最新结果，重新解析时覆盖旧的，
# 不同源文件之间互不覆盖，方便排查是哪个 pptx 解析出的问题。
# 后期过了开发阶段，可以删掉这个设计，确实不需要保留文件的zip file。
_DEBUG_ZIP_DIR = DEBUG_ZIPS_DIR

# 图片素材的保存目录：按 pptx 文件名分子目录存放提取出的图片。
# 每次重新解析同一个 pptx 时会先清空对应子目录再重新提取，避免新版本
# slide/图片数量变少后，旧版本残留的图片文件混在里面。
_IMAGES_DIR = IMAGES_DIR


class _TableTextParser(HTMLParser):
    """用标准库 HTMLParser 从 MinerU 返回的 HTML 表格标记中提取行文本。

    MinerU 的 table_body 是逐字符的 HTML 列表（如 ['<', 't', 'a', ...]),
    join 后得到完整 HTML。这个 parser 按 <tr> 分组、逐行提取单元格文本，
    每行用 " | " 连接。

    修复记录（原来的版本在这两点上会丢数据）：
    - <th> 之前完全没被处理（只认 <td>），而 MinerU 输出的表格 100% 用
      <th> 做表头行，导致表头整行被丢弃。现在 <td>/<th> 一视同仁。
    - rowspan 之前完全没处理：跨行合并的单元格只会在第一次出现的那一行
      被记录，后续被合并覆盖的行里对应位置直接是空的。现在会把该单元格
      的文本延续填充到 rowspan 覆盖的后续行，并按原来的列位置对齐，不会
      因为对齐问题让后面的列错位。
    - colspan 仍然不处理：跨列单元格只在起始列输出一次，不做列扩展。
      对语义检索影响很小，而且不同表格的真实列数没法从 colspan 可靠推断，
      强行展开反而容易在列对齐上引入新 bug。
    """

    def __init__(self) -> None:
        super().__init__()
        # 每行用 {列号: 文本} 表示，而不是单纯的 list，
        # 这样才能让 rowspan 延续的单元格落在正确的列位置上。
        self._rows: list[dict[int, str]] = []
        self._in_cell: bool = False
        self._current_text: list[str] = []
        self._current_rowspan: int = 1
        self._col_idx: int = 0
        # 还没消耗完的 rowspan 单元格：{列号: [剩余需要填充的行数, 文本]}
        self._pending_rowspans: dict[int, list] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            row: dict[int, str] = {}
            # 新的一行先用还没消耗完的 rowspan 单元格占位
            for col, (remaining, text) in list(self._pending_rowspans.items()):
                row[col] = text
                remaining -= 1
                if remaining <= 0:
                    del self._pending_rowspans[col]
                else:
                    self._pending_rowspans[col][0] = remaining
            self._rows.append(row)
            self._col_idx = 0
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_text = []
            attrs_dict = dict(attrs)
            raw_rowspan = attrs_dict.get("rowspan")
            try:
                self._current_rowspan = int(raw_rowspan) if raw_rowspan else 1
            except ValueError:
                self._current_rowspan = 1
            # 跳过已经被上面 rowspan 占用的列，找到这个单元格真正落在的列号
            if self._rows:
                while self._col_idx in self._rows[-1]:
                    self._col_idx += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            text = "".join(self._current_text)
            # 折叠内部换行/多余空白（MinerU 有些表头单元格文字自带换行，
            # 比如 "每次充电电量\n（KWH）"，原样保留会把 Markdown 表格的
            # 行结构撑坏，这里统一压成单行）
            text = " ".join(text.split())
            if text:
                # HTML 实体解码（&amp; → &，&nbsp; →  等）
                text = html.unescape(text)
            if self._rows:
                self._rows[-1][self._col_idx] = text
            if self._current_rowspan > 1:
                self._pending_rowspans[self._col_idx] = [self._current_rowspan - 1, text]
            self._col_idx += 1
            self._current_rowspan = 1
            self._in_cell = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_text.append(data)

    def get_rows(self) -> list[list[str]]:
        """按列号排序、丢弃空单元格后，返回每行的文本列表。

        适合纯文本检索场景（不要求严格的列对齐）。要生成规范的 Markdown
        表格（列数必须每行一致），请用 get_grid()。
        """
        return [
            [row[col] for col in sorted(row.keys()) if row.get(col)]
            for row in self._rows
        ]

    def get_grid(self) -> list[list[str]]:
        """返回按列对齐的完整二维网格，保留空单元格（用空字符串占位）。

        列数以出现过的最大列号为准；哪一行缺了某一列（比如某行没有
        "规格型号"这一格），就用空字符串补上，而不是像 get_rows() 那样
        直接跳过——这样每一行的列数才能保持一致，才能拼成合法的
        Markdown 表格（表头和数据行列数不一致的话，很多渲染器会显示错乱）。
        """
        if not self._rows:
            return []
        max_col = max((max(row.keys()) for row in self._rows if row), default=-1)
        return [
            [row.get(col, "") for col in range(max_col + 1)]
            for row in self._rows
        ]


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

    def parse_document(self, file_path: str | Path) -> ParseResult:
        """解析本地文档，返回通用 ``ParseResult``。

        ``ParseResult.pages`` 使用通用的 ``Page(page_number=...)`` 模型。
        为兼容尚未迁移的调用方，该结果同时可迭代，迭代时给出原有的页面字典
        （含 ``slide_number``、``source_file`` 等历史字段）。
        """

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"不支持的文件格式: {ext}（目前支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}）"
            )

        # 第一步：提交任务，立刻拿到 batch_id，不等待结果
        print(f"  提交解析任务: {file_path.name}")
        batch_id = self._client.submit(str(file_path), model="vlm")

        # 第二步：每隔 _POLL_INTERVAL 秒查一次状态，直到完成或超时
        zip_bytes = self._poll_until_done(batch_id, file_path.name)

        # 第三步：从 zip 包里取出 content_list.json，按 page_idx 分组。旧字典
        # 视图仍保留，现有 pipeline 与 Chroma schema 不在本批迁移。
        legacy_pages = self._split_into_pages(zip_bytes, file_path.name)
        return ParseResult.from_legacy_pages(file_path.name, ext.lstrip("."), legacy_pages)

    def parse_pptx(self, file_path: str | Path) -> list[dict]:
        """PPTX 兼容入口，继续返回含 ``slide_number`` 的旧页面字典。"""
        return self.parse_document(file_path).to_legacy_pages()

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
        pending_since: float | None = None  # 第一次进入 pending 的时刻
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
                debug_zip_path = _DEBUG_ZIP_DIR / f"{Path(file_name).name}.zip"
                debug_zip_path.write_bytes(result._zip_bytes)
                print(f"  已保存调试用 zip 包到: {debug_zip_path.resolve()}")

                return result._zip_bytes

            if result.state == "failed":
                raise RuntimeError(f"MinerU 解析失败: {result.error}")

            # pending 阶段单独超时：卡在 pending 太久大概率是任务丢失
            if result.state == "pending":
                if pending_since is None:
                    pending_since = time.monotonic()
                elif time.monotonic() - pending_since > _PENDING_TIMEOUT:
                    raise TimeoutError(
                        f"解析任务卡在 pending 状态超过 {_PENDING_TIMEOUT}s，"
                        f"可能是 MinerU 队列拥堵或任务丢失（batch_id={batch_id}）"
                    )

            # 还在运行中，打印进度
            if result.progress:
                p = result.progress
                print(f"  [state={result.state}] {p.extracted_pages}/{p.total_pages} 页（已等待 {elapsed}s)")
            else:
                print(f"  [state={result.state}]（已等待 {elapsed}s)")

            # 检查总超时（running 阶段主要靠这个兜底）
            if time.monotonic() > deadline:
                raise TimeoutError(f"解析总超时（>{_MAX_WAIT}s），batch_id={batch_id}")

            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    @staticmethod
    def _split_into_pages(zip_bytes: bytes, source_file: str) -> list[dict]:
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
            images_dir = _IMAGES_DIR / Path(source_file).name
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
                indexable = not MinerUParser._is_toc_page(
                    pages[page_idx], title, content
                )
                slides.append({
                    "slide_number": slide_number,
                    "title": title,
                    "content": content,
                    "source_file": source_file,
                    # 每项为 {"path": 本地图片路径, "caption": 图片描述}，
                    # caption 可能是空字符串（不是所有图片都有说明文字）
                    "images": images,
                    # 保留原始 block，便于调用方审查；向量层不会写入此字段。
                    "raw_blocks": pages[page_idx],
                    "indexable": indexable,
                })

            return slides

    @staticmethod
    def _split_by_page(zip_bytes: bytes, source_file: str) -> list[dict]:
        """兼容旧内部调用：请优先使用 _split_into_pages()。"""
        return MinerUParser._split_into_pages(zip_bytes, source_file)

    @staticmethod
    def _is_toc_page(blocks: list[dict], title: str, content: str) -> bool:
        """用强信号和保守弱信号识别目录页，不依赖 LLM。

        强信号是标题/正文中的“目录”或“Contents”，以及多个 MinerU `_Toc`
        anchor。弱信号仅接受至少三条“标题 + 连续引导符 + 页码”的文本行，
        不检查 table block，避免将参数表、报价表或工程量表误判为目录。
        """
        return bool(MinerUParser._toc_page_reasons(blocks, title, content))

    @staticmethod
    def _toc_page_reasons(blocks: list[dict], title: str, content: str) -> list[str]:
        """返回目录页命中的规则，供本地预览解释而不改变识别逻辑。"""
        reasons: list[str] = []
        visible_text = f"{title}\n{content}".casefold()
        if "目录" in visible_text:
            reasons.append('标题或正文包含“目录”')
        if "contents" in visible_text:
            reasons.append('标题或正文包含“Contents”')

        raw_json = json.dumps(blocks, ensure_ascii=False)
        toc_anchor_count = len(
            re.findall(r"_toc(?:[\w-]+)?", raw_json, flags=re.IGNORECASE)
        )
        if toc_anchor_count >= 2:
            reasons.append(f"存在 {toc_anchor_count} 个 _Toc anchor")

        text_lines: list[str] = []
        for block in blocks:
            if block.get("type") == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    text_lines.extend(text.splitlines())
            elif block.get("type") == "list":
                text_lines.extend(
                    str(item).strip()
                    for item in block.get("list_items") or []
                    if str(item).strip()
                )

        toc_line_pattern = re.compile(r".+(?:\.{2,}|…{2,}|·{2,}|-{3,}|—{2,})\s*\d{1,4}\s*$")
        matched_lines = sum(
            bool(toc_line_pattern.match(line.strip())) for line in text_lines
        )
        if matched_lines >= 3:
            reasons.append(f"{matched_lines} 行标题引导符加页码")
        return reasons

    @staticmethod
    def preview_debug_zip(zip_path: str | Path, source_file: str | None = None) -> list[dict]:
        """只读本地 MinerU debug zip，生成拟入库页面预览数据。

        不构造 MinerU 客户端、不提取图片，也不触及 Chroma。图片仅按正式
        入库时的命名规则生成相对路径，方便开发时与既有素材目录对照。
        """
        zip_path = Path(zip_path)
        if source_file is None:
            source_file = zip_path.name.removesuffix(".zip")

        with zipfile.ZipFile(zip_path) as zf:
            blocks = MinerUParser._read_content_list(zf)

        pages: dict[int, list[dict]] = {}
        for block in blocks:
            pages.setdefault(block.get("page_idx", 0), []).append(block)

        preview_pages = []
        for page_idx in sorted(pages):
            slide_number = page_idx + 1

            def preview_image_path(img_path: str, image_idx: int) -> str:
                ext = Path(img_path).suffix or ".jpg"
                return (
                    Path("images")
                    / Path(source_file).name
                    / f"slide_{slide_number}_{image_idx}{ext}"
                ).as_posix()

            title, content, images = MinerUParser._render_page_with_image_resolver(
                pages[page_idx], preview_image_path
            )
            reasons = MinerUParser._toc_page_reasons(
                pages[page_idx], title, content
            )
            table_markdown = []
            for block in pages[page_idx]:
                if block.get("type") == "table":
                    _, table_content, _ = MinerUParser._render_page_with_image_resolver(
                        [block], preview_image_path
                    )
                    if table_content:
                        table_markdown.append(table_content)

            preview_pages.append({
                "slide_number": slide_number,
                "title": title,
                "content": content,
                "source_file": source_file,
                "images": images,
                "raw_blocks": pages[page_idx],
                "indexable": not reasons,
                "toc_reasons": reasons,
                "table_markdown": table_markdown,
            })
        return preview_pages

    @staticmethod
    def build_index_text(page: dict, include_image_captions: bool = True) -> str:
        """按当前向量层规则拼接不含 LLM context 的页面文本。

        首次裸存仍可关闭图片说明；完成图片 annotation 或离线预览时使用默认
        值，让每张图片 caption 只进入文本一次。
        """
        base_text = (
            f"{page['title']}\n{page['content']}"
            if page.get("title")
            else page.get("content", "")
        )
        if not include_image_captions:
            return base_text
        captions = [
            image["caption"]
            for image in page.get("images", [])
            if image.get("caption")
        ]
        if captions:
            return f"{base_text}\n" + "\n".join(
                f"[图片] {caption}" for caption in captions
            )
        return base_text

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
    def _split_banner_rows(grid: list[list[str]]) -> tuple[list[str], list[list[str]]]:
        """把网格最前面那些"只有 1 个非空格子、其余列全是空"的整行摘出来。

        这种行通常是源表格里用 colspan 横跨全部列的说明性文字（比如"电价编号:
        xxx"、"广东省两充两放策略"这类小标题/横幅）。我们不解析 colspan，
        所以这类行在网格里只有第一格有内容、其余格子是空的——如果照常把
        网格第一行当 Markdown 表头，就会把这种说明文字错当成列标题，后面
        真正的列标题（比如"开始时间 | 结束时间 | 峰谷属性 | 备注"）反而被
        当成了数据行。

        只在列数 >= 3 时做这个处理：只有 2 列的表里大量存在"属性名 | 属性值"
        这种正常的键值对表格（比如设备参数表），其中某一行也可能碰巧只有
        1 个非空格子（分类小标题，如"基本数据"占了整行的第一列），这种
        情况下没法可靠区分"这是横幅"还是"这就是正常数据"——贸然摘出来，
        下一行真正的数据行就会被误当成表头，风险比不处理更大，所以列数
        较少时直接跳过，保留原来的行为。

        Returns:
            (被摘出来的横幅文字列表, 去掉横幅行之后剩下的网格)
        """
        if not grid or len(grid[0]) < 3:
            return [], grid

        banners: list[str] = []
        idx = 0
        for row in grid:
            non_empty = [cell for cell in row if cell.strip()]
            if len(non_empty) == 1:
                banners.append(non_empty[0])
                idx += 1
            else:
                break
        return banners, grid[idx:]

    @staticmethod
    def _grid_to_markdown(grid: list[list[str]]) -> str:
        """把 get_grid() 返回的二维网格拼成文本：先摘掉开头的横幅说明行
        （见 _split_banner_rows），剩下的部分再拼成标准 Markdown 表格
        （表头行 + 分隔线 + 数据行），方便入库后无论是给人看还是喂给
        下游 LLM 生成方案草稿，都能识别出这是一张结构化的表格，而不是
        一堆没有对齐关系的纯文本行。

        - 整行全是空字符串的行（比如源数据里出现的空 <tr></tr>）会被过滤掉
        - 单元格里如果本身含有 "|" 字符会被转义，否则会被误判成新的分隔符
        - 摘出来的横幅行摆在表格前面，各自一行，不参与表格的列结构
        """
        banners, rest = MinerUParser._split_banner_rows(grid)

        rows = [row for row in rest if any(cell.strip() for cell in row)]
        table_md = ""
        if rows:
            col_count = max(len(row) for row in rows)
            rows = [row + [""] * (col_count - len(row)) for row in rows]

            def _escape(cell: str) -> str:
                return cell.replace("|", "\\|")

            lines = ["| " + " | ".join(_escape(c) for c in rows[0]) + " |"]
            lines.append("|" + "|".join([" --- "] * col_count) + "|")
            for row in rows[1:]:
                lines.append("| " + " | ".join(_escape(c) for c in row) + " |")
            table_md = "\n".join(lines)

        combined = list(banners)
        if table_md:
            combined.append(table_md)
        return "\n".join(combined)

    @staticmethod
    def _render_page(
        blocks: list[dict],
        zf: zipfile.ZipFile,
        source_file: str,
        slide_number: int,
    ) -> tuple[str, str, list[dict]]:
        """渲染正式入库页面，并将图片提取到本地素材目录。"""
        return MinerUParser._render_page_with_image_resolver(
            blocks,
            lambda img_path, image_idx: MinerUParser._extract_image(
                zf, img_path, source_file, slide_number, image_idx
            ),
        )

    @staticmethod
    def _render_page_with_image_resolver(
        blocks: list[dict], image_resolver
    ) -> tuple[str, str, list[dict]]:
        """把同一页的 block 列表渲染成 (title, content, images)。

        - type == "text" 且 text_level == 0：作为 slide 标题（取第一个出现的）
        - type == "text" 且 text_level 为其他数字：子标题，保留 Markdown 层级
        - type == "text" 且没有 text_level：正文
        - type == "list"：list_items 逐条拼接
        - type == "table"：table_caption + table_body 按行拼接
        - type == "image"：通过 image_resolver 获取正式图片路径或预览相对
          路径；caption 保留在 images metadata，稍后由 build_index_text()
          只写入最终索引文本一次。
        - type == "page_footnote"：忽略（页脚噪音，如公司名）
        """
        title = ""
        parts: list[str] = []
        images: list[dict] = []
        image_idx = 0
        unknown: dict[str, int] = {}

        def text_value(value) -> str:
            if isinstance(value, list):
                value = "".join(str(item) for item in value)
            return str(value or "").strip()

        def nested_text(value) -> str:
            if isinstance(value, dict):
                return " ".join(filter(None, (nested_text(item) for item in value.values())))
            if isinstance(value, list):
                return " ".join(filter(None, (nested_text(item) for item in value)))
            return text_value(value)

        seen_assets: set[str] = set()
        def add_image(block: dict, *, kind: str, caption: str = "") -> None:
            nonlocal image_idx
            raw_path = text_value(block.get("img_path") or block.get("image_path"))
            if not raw_path or raw_path in seen_assets:
                return
            seen_assets.add(raw_path)
            local_path = image_resolver(raw_path, image_idx)
            image_idx += 1
            if local_path is not None:
                item = {"path": str(local_path), "caption": caption}
                if kind != "image":
                    item.update({"block_type": kind,
                                 "footnote": text_value(block.get("chart_footnote") or block.get("footnote")),
                                 "bbox": block.get("bbox")})
                images.append(item)

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
                # table_caption 有时是空 list []，有时是空字符串 ""
                caption_raw = block.get("table_caption")
                if isinstance(caption_raw, list):
                    caption = " ".join(str(x).strip() for x in caption_raw if str(x).strip())
                else:
                    caption = (caption_raw or "").strip()
                if caption:
                    parts.append(f"[表格] {caption}")

                # MinerU 的 table_body 是逐字符的 HTML 列表，先 join 再解析。
                body = block.get("table_body") or []
                if isinstance(body, list):
                    table_html = "".join(str(c) for c in body)
                elif isinstance(body, str):
                    table_html = body
                else:
                    table_html = ""
                if table_html:
                    grid: list[list[str]] = []
                    try:
                        parser = _TableTextParser()
                        parser.feed(table_html)
                        grid = parser.get_grid()
                    except Exception:
                        # 解析失败时的兜底：不再把原始 HTML 重新塞回同一套
                        # <tr>/<td> 解析逻辑（原来的写法在 table_html 本身
                        # 就带有 <tr>/<td> 标签时，会被当成新的多行重新展开，
                        # 起不到"退化成单个单元格"的效果）。这里直接暴力
                        # 剥掉所有标签，保留原始文字，好歹不让整张表格的
                        # 内容彻底消失。
                        plain_text = re.sub(r"<[^>]+>", " ", table_html)
                        plain_text = html.unescape(" ".join(plain_text.split()))
                        if plain_text:
                            grid = [[plain_text]]

                    markdown_table = MinerUParser._grid_to_markdown(grid)
                    if markdown_table:
                        parts.append(markdown_table)

            elif block_type == "image":
                # image_caption 有时是 list（跟 table_caption 一样）
                caption_raw = block.get("image_caption")
                if isinstance(caption_raw, list):
                    caption = " ".join(str(x).strip() for x in caption_raw if str(x).strip())
                else:
                    caption = (caption_raw or "").strip()
                add_image(block, kind="image", caption=caption)

            elif block_type == "aside_text":
                text = text_value(block.get("text")) or nested_text(block.get("lines") or block.get("spans") or block.get("blocks"))
                if text and text not in parts:
                    parts.append(f"[附注] {text}")

            elif block_type == "equation":
                formula = text_value(block.get("latex") or block.get("equation_latex") or block.get("text"))
                formula = formula or nested_text(block.get("spans") or block.get("lines"))
                if formula:
                    parts.append(f"$$\n{formula}\n$$")
                else:
                    add_image(block, kind="equation", caption=text_value(block.get("caption") or block.get("equation_caption")))

            elif block_type == "chart":
                caption = text_value(block.get("chart_caption") or block.get("caption") or block.get("image_caption"))
                footnote = text_value(block.get("chart_footnote") or block.get("footnote"))
                nearby = text_value(block.get("content") or block.get("text")) or nested_text(block.get("lines") or block.get("spans"))
                if caption:
                    parts.append(f"[图表] {caption}")
                if footnote and footnote != caption:
                    parts.append(f"[图表注] {footnote}")
                if nearby and nearby not in {caption, footnote}:
                    parts.append(nearby)
                add_image(block, kind="chart", caption=caption or footnote)

            elif block_type == "index":
                value = text_value(block.get("text")) or nested_text(block.get("list_items") or block.get("items") or block.get("lines"))
                toc_like = "目录" in value or len(re.findall(r"(?:\.{2,}|…{2,})\s*\d+", value)) >= 2
                if value and not toc_like:
                    parts.append(f"[索引] {value}")

            else:
                unknown[str(block_type)] = unknown.get(str(block_type), 0) + 1

        if unknown:
            summary = ", ".join(f"{name}={count}" for name, count in sorted(unknown.items()))
            print(f"  警告: 未处理 block 类型（本页聚合）：{summary}")

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
        out_dir = _IMAGES_DIR / Path(source_file).name
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / f"slide_{slide_number}_{image_idx}{ext}"
        out_path.write_bytes(img_bytes)
        return out_path
