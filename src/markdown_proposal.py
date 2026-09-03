"""Small, in-memory workflow for one evidence-grounded Markdown proposal."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from src.config import RAG_DB_PATH
from src.query_result import QueryResult, SearchHit


class ProposalLLM(Protocol):
    def generate(self, prompt: str, system_prompt: str = "") -> str: ...


class ProposalGenerationError(RuntimeError):
    """A safe, stage-labelled failure for the one-file proposal workflow."""

    def __init__(self, stage: str, cause: BaseException, *, detail: str = "") -> None:
        self.stage = stage
        self.cause = cause
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"proposal stage {stage} failed: {type(cause).__name__}: {cause}{suffix}")


class ProposalQualityError(ValueError):
    """A deterministic, user-visible proposal quality gate failure."""


@dataclass(frozen=True)
class SectionPlan:
    section_id: str
    heading: str
    level: int
    writing_goal: str
    retrieval_queries: list[str]


@dataclass(frozen=True)
class ProposalPlan:
    title: str
    sections: list[SectionPlan]


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    file_name: str
    page_number: int
    text: str
    images: list[dict]
    title: str = ""


@dataclass(frozen=True)
class ReviewIssue:
    section_id: str
    issue_type: str
    instruction: str


@dataclass(frozen=True)
class _SectionDraft:
    """Runtime-only raw body. IDs are deliberately never rendered in this object."""

    plan: SectionPlan
    raw_body: str
    evidence: list[EvidenceItem]
    images: dict[str, tuple[EvidenceItem, dict]]


@dataclass(frozen=True)
class _ImageAsset:
    source: Path
    relative_path: str
    caption: str
    evidence: EvidenceItem


_PLAN_SYSTEM = """你是技术方案规划助手。仅根据用户需求规划 3 至 8 个主要章节；不得套用固定模板。
只输出 JSON，格式为 {title, sections:[{section_id,heading,level,writing_goal,retrieval_queries}]}。
section_id 必须唯一；检索词必须具体、可直接检索。用户明确列出的交付项必须在最终标题结构中逐项可识别：可以作为主章节，或在合理归并的主章节中使用明确的 H3 标题，不能只隐含在正文。章节名称、写作目标和检索词必须面向当前项目范围；不得因为知识库可能命中而扩展用户未要求的物业、停车、租户收费、普通楼宇或非能源子系统。"""
_WRITE_SYSTEM = """你是技术方案撰写助手。只输出当前章节正文，不输出标题或整篇方案。
只能使用给出的证据；技术参数必须有证据 ID [S数字] 支持。只能引用允许的 [S数字]，不能输出文件名、页码、cache 路径或图片路径。图片只能用允许的 [IMG数字]，仅在确有帮助处插入。资料不足写【待确认】。
必须明确区分：用户输入条件、当前项目已知事实、地方政策参考、同类案例数据、产品能力资料、设计建议和待确认内容。不要生成“待确认项”独立章节、同名标题或加粗的“待确认内容/事项/明确内容”视觉标题；系统会在文末统一汇总，只在相关正文中标记具体待确认事实。异地政策只能称为政策参考，案例参数只能称为案例数据，产品能力只能称为可选能力；行业标准适用性须结合最终系统和并网条件确认。设计建议必须以“【设计建议】”“建议”“可考虑”或“是否采用需结合待确认参数论证”明确标记，不能把建议、资料能力或案例经验写成本项目既定参数或承诺。除非用户明确要求或与本项目能源计量、负荷调控有直接必要联系且有证据支持，不得扩展停车、车位、租户缴费、物业预付费、水表管理或普通智慧楼宇功能。"""
_REVIEW_SYSTEM = """审查完整技术方案。只输出 JSON：{issues:[{section_id,issue_type,instruction}]}。
检查遗漏、重复、无证据参数、无关引用、矛盾、空话、应标待确认的结论及无关图片；不要重写正文。
特别检查 policy_scope_leakage、case_as_project_fact、product_capability_as_committed、unsupported_project_assumption、standard_applicability_unclear：不得把异地政策、同类案例、产品手册能力或未确认标准适用性写成本项目事实、指标或承诺。还要检查 scope_drift（用户未要求的物业、停车、租户收费、水表、预付费或普通智慧楼宇功能应删除或收缩）、uncited_knowledge_fact（政策、案例、产品和技术事实缺少对应证据 ID）、unmarked_design_advice（建议未明确标记为建议）以及待确认视觉伪标题；发现时必须给出删除、收缩或改写为有证据且边界清晰内容的指令。"""
_REVISE_SYSTEM = """只输出修订后的当前章节正文。你收到的是带内部证据 ID 和图片 ID 的原始正文；必须保留或使用允许的 [S数字]/[IMG数字]，不能输出文件名、页码、cache 路径或图片路径。
只能使用给出的证据和图片 ID；不得编造参数，资料不足写【待确认】。不要生成“待确认项”独立章节、同名标题或加粗的“待确认内容/事项/明确内容”视觉标题；系统会在文末统一汇总。必须区分用户输入条件、政策参考、案例数据、产品能力和设计建议；不能把它们写成本项目已确定事实或承诺。任何政策、案例、产品或技术事实必须保留对应证据 ID；设计建议必须明确标记为建议。删除用户未要求且与能源计量或负荷调控没有直接必要联系的物业、停车、租户收费、预付费、水表或普通智慧楼宇功能，不要仅添加免责声明。行业标准适用性须结合最终系统和并网条件确认。"""
_ID_RE = re.compile(r"\[(S\d+|IMG\d+)\]")
_VISIBLE_CITATION_RE = re.compile(r"\[来源:\s*[^\]]+\]")
_RAW_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.M)
_BAD_BOLD_HEADING_RE = re.compile(r"^\s*\*\*#{1,6}\s+", re.M)
_BAD_IMAGE_ESCAPE_RE = re.compile(r"!\[[^\]]*\]\\\(")
_BAD_LIST_ESCAPE_RE = re.compile(r"^\s*\d+\\\.\s+", re.M)
_BAD_UNORDERED_LIST_ESCAPE_RE = re.compile(r"^\s*\\-\s+", re.M)
_BAD_DOUBLE_BOLD_RE = re.compile(r"\*\*\\\*\\\*.+?\\\*\\\*\*\*")
_CONFIRM_RE = re.compile(r"[^\n。！？；]*【待确认】[^\n。！？；]*[。！？；]?")
_CONFIRMATION_PSEUDO_LABEL_RE = re.compile(
    r"(?m)^\s*(?:[-*+]\s+)?\*\*(?:待确认|待明确)(?:内容|事项)\*\*\s*[:：]?\s*"
)


def _json(raw: str) -> dict:
    cleaned = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.I | re.S)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value, _end = json.JSONDecoder().raw_decode(cleaned[cleaned.index("{"):])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("LLM 未返回可解析的 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("LLM JSON 顶层必须是对象")
    return value


def _plan_from(raw: str) -> ProposalPlan:
    value = _json(raw)
    title, sections = value.get("title"), value.get("sections")
    if not isinstance(title, str) or not title.strip() or not isinstance(sections, list) or not 3 <= len(sections) <= 8:
        raise ValueError("大纲必须有标题和 3 至 8 个章节")
    planned: list[SectionPlan] = []
    seen: set[str] = set()
    for item in sections:
        if not isinstance(item, dict):
            raise ValueError("章节必须是对象")
        section_id = item.get("section_id")
        queries = item.get("retrieval_queries")
        if (not isinstance(section_id, str) or not section_id or section_id in seen
                or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("heading", "writing_goal"))
                or not isinstance(item.get("level"), int) or not isinstance(queries, list)
                or not queries or not all(isinstance(query, str) and query.strip() for query in queries)):
            raise ValueError("章节字段不符合要求")
        seen.add(section_id)
        planned.append(SectionPlan(section_id, item["heading"].strip(), item["level"], item["writing_goal"].strip(), list(dict.fromkeys(queries))))
    return ProposalPlan(title.strip(), planned)


def _review_from(raw: str, valid_sections: set[str]) -> list[ReviewIssue]:
    issues = _json(raw).get("issues")
    if not isinstance(issues, list):
        raise ValueError("审查结果必须包含 issues 列表")
    parsed = []
    for item in issues:
        if not isinstance(item, dict) or item.get("section_id") not in valid_sections or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("issue_type", "instruction")):
            raise ValueError("审查问题字段不符合要求")
        parsed.append(ReviewIssue(item["section_id"], item["issue_type"], item["instruction"]))
    return parsed


def _evidence(hits: list[SearchHit]) -> list[EvidenceItem]:
    result, seen = [], set()
    for hit in hits:
        identity = (hit.source_file, hit.page_number)
        if identity not in seen:
            seen.add(identity)
            result.append(EvidenceItem(f"S{len(result) + 1}", hit.source_file, hit.page_number, hit.content, hit.images, hit.title))
    return result


def _display_name(file_name: str) -> str:
    """Show only a portable source basename; identities retain the original key."""
    return Path(file_name.replace("\\", "/")).name


def _caption(item: EvidenceItem, image: dict) -> str:
    value = str(image.get("caption") or image.get("description") or item.title or "").strip()
    return value if value and value != "资料图片" else ""


def _image_catalog(evidence: list[EvidenceItem]) -> dict[str, tuple[EvidenceItem, dict]]:
    catalog = {}
    for item in evidence:
        for image in item.images:
            if image.get("path") and _caption(item, image):
                catalog[f"IMG{len(catalog) + 1}"] = (item, image)
    return catalog


def _evidence_prompt(evidence: list[EvidenceItem], images: dict[str, tuple[EvidenceItem, dict]]) -> str:
    lines = [f"[{item.evidence_id}] 文件：{_display_name(item.file_name)}；页码：{item.page_number}\n正文：{item.text}" for item in evidence]
    for image_id, (item, image) in images.items():
        lines.append(f"[{image_id}] 图注：{_caption(item, image)}；来源：{_display_name(item.file_name)}，第{item.page_number}页")
    return "\n\n".join(lines) or "（没有可用证据；必须使用【待确认】。）"


def _normalise_markdown(text: str) -> str:
    """Deterministically repair known structural escapes outside fenced code blocks."""
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        line = line[:-len(ending)] if ending else line
        # U+00A0 is not a stable indentation character in Markdown renderers.
        line = line.replace("\u00a0", " ")
        line = re.sub(r"^\s*\*\*(#{1,6}\s+.+?)\*\*\s*$", r"\1", line)
        line = re.sub(r"\*\*\\\*\\\*(.+?)\\\*\\\*\*\*", r"**\1**", line)
        line = re.sub(r"^([ \t]*\d+)\\\.\s+", r"\1. ", line)
        line = re.sub(r"^([ \t]*)\\-\s+", r"\1- ", line)
        # This only targets an otherwise complete Markdown image token.
        line = re.sub(r"(!\[[^\]]*\])\\\(([^)]*)\\\)", r"\1(\2)", line)
        lines.append(line + ending)
    return "".join(lines)


def _validate_ids(body: str, evidence: list[EvidenceItem], images: dict[str, tuple[EvidenceItem, dict]]) -> None:
    if _VISIBLE_CITATION_RE.search(body):
        raise ValueError("章节原文不得直接输出可见来源；必须使用 [S数字]")
    if _RAW_IMAGE_RE.search(body):
        raise ValueError("章节原文不得直接输出图片路径；必须使用 [IMG数字]")
    allowed = {item.evidence_id for item in evidence} | set(images)
    unknown = sorted({value for value in _ID_RE.findall(body) if value not in allowed})
    if unknown:
        raise ValueError(f"存在本章不可用的引用或图片 ID：{', '.join(unknown)}")


def _source_path(image_root: Path, stored_path: str) -> Path:
    root = image_root.resolve()
    path = Path(stored_path)
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ProposalQualityError(f"图片路径越出知识库根目录：{stored_path}")
    if not candidate.is_file():
        raise ProposalQualityError(f"图片文件不存在：{stored_path}")
    return candidate


def _asset_plan(sections: dict[str, _SectionDraft], image_root: Path) -> dict[tuple[str, str], _ImageAsset]:
    assets: dict[tuple[str, str], _ImageAsset] = {}
    number = 1
    for section_id, draft in sections.items():
        for image_id in _ID_RE.findall(draft.raw_body):
            if not image_id.startswith("IMG") or (section_id, image_id) in assets:
                continue
            item, image = draft.images[image_id]
            source = _source_path(image_root, str(image["path"]))
            suffix = source.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                raise ProposalQualityError(f"不支持的图片格式：{source.name}")
            assets[(section_id, image_id)] = _ImageAsset(source, f"assets/image-{number:03d}{suffix}", _caption(item, image), item)
            number += 1
    return assets


def _render_body(raw_body: str, draft: _SectionDraft, assets: dict[tuple[str, str], _ImageAsset], *, preview: bool) -> tuple[str, set[str]]:
    raw_body = _normalise_markdown(raw_body)
    _validate_ids(raw_body, draft.evidence, draft.images)
    evidence_by_id = {item.evidence_id: item for item in draft.evidence}
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        value = match.group(1)
        if value.startswith("S"):
            item = evidence_by_id[value]
            used.add(value)
            return f"[来源: {_display_name(item.file_name)}, 第 {item.page_number} 页]"
        asset = assets[(draft.plan.section_id, value)]
        used.add(asset.evidence.evidence_id)
        if preview:
            return f"*图：{asset.caption}。来源：{_display_name(asset.evidence.file_name)}，第{asset.evidence.page_number}页。*"
        return f"![{asset.caption}]({asset.relative_path})\n\n*图：{asset.caption}。来源：{_display_name(asset.evidence.file_name)}，第{asset.evidence.page_number}页。*"

    return _ID_RE.sub(replace, raw_body).strip(), used


def _confirmation_heading(line: str) -> bool:
    match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
    if not match:
        return False
    value = re.sub(r"^(?:\d+(?:\.\d+)*\.?|第?[一二三四五六七八九十百]+)[、.．]?\s*", "", match.group(1)).strip()
    return value in {"待确认项", "待确认内容", "待确认事项", "待明确内容", "待明确事项"}


def _extract_confirmation_section(body: str) -> tuple[str, str]:
    """Remove a model-owned confirmation section and retain its content for aggregation."""
    body = _CONFIRMATION_PSEUDO_LABEL_RE.sub("", body)
    kept: list[str] = []
    extracted: list[str] = []
    in_confirmation = False
    for line in body.splitlines():
        if _confirmation_heading(line):
            in_confirmation = True
            continue
        if in_confirmation and _HEADING_RE.match(line):
            in_confirmation = False
        (extracted if in_confirmation else kept).append(line)
    return "\n".join(kept).strip(), "\n".join(extracted).strip()


def _confirmation_items(body: str) -> list[str]:
    items: list[str] = []
    for match in _CONFIRM_RE.findall(body):
        item = re.sub(r"\*\*", "", match)
        item = re.sub(r"^(?:(?:待确认|待明确)(?:内容|事项)?|待确认项)\s*[:：]", "", item.strip())
        item = re.sub(r"\s+", " ", item).strip(" -；。")
        subject = item.replace("【待确认】", "").strip(" ：:，,。")
        if (not subject
                or re.fullmatch(r"(?:具体)?(?:参数|内容|事项)(?:待确认|需确认)?", subject)):
            continue
        key = re.sub(r"[【】待确认\s，,。：:；;]", "", item)
        duplicate = next((old for old in items if key in re.sub(r"[【】待确认\s，,。：:；;]", "", old)
                          or re.sub(r"[【】待确认\s，,。：:；;]", "", old) in key), None)
        if duplicate is None:
            items.append(item)
        elif len(key) > len(re.sub(r"[【】待确认\s，,。：:；;]", "", duplicate)):
            items[items.index(duplicate)] = item
    return items


def _confirmation_section_items(body: str) -> list[str]:
    """Treat non-empty list/prose lines beneath a removed confirmation heading as marked items."""
    marked = []
    for line in body.splitlines():
        value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.]\s+)?", "", line).strip()
        if value:
            marked.append(value if "【待确认】" in value else f"{value.rstrip('。！？；')}【待确认】")
    return _confirmation_items("\n".join(marked))


_REQUEST_CONFIRMATION_ITEMS = (
    ("园区面积", "园区面积"),
    ("负荷曲线", "负荷曲线"),
    ("变压器容量", "变压器容量"),
    ("配电电压", "配电电压"),
    ("光伏可安装面积", "光伏可安装面积"),
    ("设备容量", "设备容量"),
    ("投资预算", "投资预算"),
    ("电价", "电价"),
    ("并网条件", "并网条件"),
    ("建设周期", "建设周期"),
)


def _requested_confirmation_items(request: str) -> list[str]:
    """Use an explicit, complete user parameter list as the single final checklist."""
    items = [label for needle, label in _REQUEST_CONFIRMATION_ITEMS if needle in request]
    return [f"{item}【待确认】" for item in items] if len(items) == len(_REQUEST_CONFIRMATION_ITEMS) else []


def _render(plan: ProposalPlan, sections: dict[str, _SectionDraft], *, image_root: Path, request: str = "", preview: bool = False) -> tuple[str, dict[tuple[str, str], _ImageAsset]]:
    assets = _asset_plan(sections, image_root)
    lines = [f"# {plan.title}", ""]
    used_evidence: dict[tuple[str, int], EvidenceItem] = {}
    confirmations: list[str] = []
    for number, section in enumerate(plan.sections, 1):
        draft = sections.get(section.section_id)
        if not draft:
            continue
        body, used = _render_body(draft.raw_body, draft, assets, preview=preview)
        body, section_confirmations = _extract_confirmation_section(body)
        lines.extend([f"## {number}. {section.heading}", "", body, ""])
        for item in draft.evidence:
            if item.evidence_id in used:
                used_evidence[(item.file_name, item.page_number)] = item
        confirmations.extend(_confirmation_items(body))
        confirmations.extend(_confirmation_section_items(section_confirmations))
    confirmations = _requested_confirmation_items(request) or confirmations
    if confirmations:
        lines.extend(["## 待确认项", ""])
        lines.extend(f"- {item}" for item in _requested_confirmation_items(request) or _confirmation_items("\n".join(confirmations)))
        lines.append("")
    lines.extend(["## 来源与依据", ""])
    if used_evidence:
        grouped: dict[str, list[int]] = {}
        for item in used_evidence.values():
            grouped.setdefault(_display_name(item.file_name), []).append(item.page_number)
        lines.extend(f"- {name}：第 {', '.join(map(str, sorted(set(pages))))} 页" for name, pages in grouped.items())
    else:
        lines.append("（本方案没有使用可引用资料。）")
    return "\n".join(lines).rstrip() + "\n", assets


def _requested_headings(request: str) -> list[str]:
    """Extract explicit numbered requirements only; do not mistake ordinary prose for headings."""
    requirements = []
    for value in re.findall(r"(?m)^\s*\d+[.、]\s*([^\n；;]+)", request):
        value = value.strip().rstrip("。；;")
        if value and value not in requirements:
            requirements.append(value)
    return requirements


def _quality_gate(markdown: str, output_path: Path, assets: dict[tuple[str, str], _ImageAsset], request: str) -> None:
    errors = []
    headings = [line.lstrip("#").strip() for line in markdown.splitlines() if _HEADING_RE.match(line)]
    h1_lines = [line for line in markdown.splitlines() if re.match(r"^#\s+\S", line)]
    if not markdown.startswith("# ") or len(h1_lines) != 1 or not headings:
        errors.append("缺少合法 H1 标题")
    if (_BAD_BOLD_HEADING_RE.search(markdown) or _BAD_IMAGE_ESCAPE_RE.search(markdown)
            or _BAD_LIST_ESCAPE_RE.search(markdown) or _BAD_UNORDERED_LIST_ESCAPE_RE.search(markdown)
            or _BAD_DOUBLE_BOLD_RE.search(markdown)):
        errors.append("存在无效的结构化 Markdown 转义")
    if _ID_RE.search(markdown):
        errors.append("残留内部证据或图片 ID")
    if ("cache/" in markdown or "cache\\" in markdown or re.search(r"[A-Za-z]:\\", markdown)
            or re.search(r"(?:^|[\s(])/(?:home|tmp|Users|var|mnt)/", markdown)):
        errors.append("显示了内部缓存路径或机器绝对路径")
    visible = bool(_VISIBLE_CITATION_RE.search(markdown))
    source_part = markdown.split("## 来源与依据", 1)[-1]
    if visible and "（本方案没有使用可引用资料。）" in source_part:
        errors.append("正文存在引用但来源列表为空")
    confirmation_headings = [line for line in markdown.splitlines() if _confirmation_heading(line)]
    if len(confirmation_headings) > 1 or any(line != "## 待确认项" for line in confirmation_headings):
        errors.append("存在重复或非标准的待确认项标题")
    if any(line.strip() in {"- 【待确认】", "- 【待确认】。", "- 待确认内容：", "- 待确认事项："}
           for line in markdown.splitlines()):
        errors.append("存在空待确认项")
    if _CONFIRMATION_PSEUDO_LABEL_RE.search(markdown):
        errors.append("正文残留待确认视觉伪标题")
    required_confirmations = _requested_confirmation_items(request)
    if required_confirmations:
        confirmation_part = markdown.split("## 待确认项", 1)[-1].split("## 来源与依据", 1)[0]
        rendered_confirmations = [line.strip()[2:].strip() for line in confirmation_part.splitlines() if line.strip().startswith("- ")]
        if rendered_confirmations != required_confirmations:
            errors.append("待确认项未完整规范覆盖用户明确参数")
    for asset in assets.values():
        if not asset.source.is_file():
            errors.append(f"图片文件不存在：{asset.relative_path}")
    requested = _requested_headings(request)
    missing = [item for item in requested if not any(item in heading for heading in headings)]
    if missing:
        errors.append(f"用户明确要求的章节缺少标题覆盖：{', '.join(missing)}")
    if errors:
        raise ProposalQualityError("；".join(errors))


def _sync_assets(output_path: Path, assets: dict[tuple[str, str], _ImageAsset]) -> None:
    asset_dir = output_path.parent / "assets"
    expected = {Path(asset.relative_path).name for asset in assets.values()}
    if assets:
        asset_dir.mkdir(parents=True, exist_ok=True)
    for asset in assets.values():
        destination = output_path.parent / asset.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset.source, destination)
    if asset_dir.exists():
        for path in asset_dir.iterdir():
            if path.is_file() and re.fullmatch(r"image-\d{3}\.(?:jpg|jpeg|png|gif|webp)", path.name, re.I) and path.name not in expected:
                path.unlink()
    if asset_dir.exists() and not any(asset_dir.iterdir()):
        asset_dir.rmdir()


def _final_markdown(plan: ProposalPlan, sections: dict[str, _SectionDraft], *, image_root: Path, output_path: Path, request: str) -> tuple[str, dict[tuple[str, str], _ImageAsset]]:
    """Render and validate the complete review draft before anything is written."""
    markdown, assets = _render(plan, sections, image_root=image_root, request=request)
    markdown = _normalise_markdown(markdown)
    _quality_gate(markdown, output_path, assets, request)
    return markdown, assets


def _write(path: Path, markdown: str, assets: dict[tuple[str, str], _ImageAsset]) -> None:
    """Persist only a Markdown draft that has already passed the final quality gate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _sync_assets(path, assets)
    path.write_text(markdown, encoding="utf-8")


def _validated_raw(llm: ProposalLLM, raw: str, draft: _SectionDraft, *, stage: str, repair_prompt: str, system_prompt: str) -> str:
    try:
        _validate_ids(_normalise_markdown(raw), draft.evidence, draft.images)
        return _normalise_markdown(raw)
    except ValueError as exc:
        try:
            repaired = llm.generate(f"{repair_prompt}：{exc}。只修复 ID 后返回章节正文。\n原文：\n{raw}", system_prompt=system_prompt)
            _validate_ids(_normalise_markdown(repaired), draft.evidence, draft.images)
            return _normalise_markdown(repaired)
        except Exception as repair_error:  # noqa: BLE001
            raise ProposalGenerationError(stage, repair_error) from repair_error


def generate_markdown_proposal(
    request: str, output_path: Path, *, llm: ProposalLLM,
    retriever: Callable[[list[str]], QueryResult], image_root: Path | None = None,
) -> Path:
    """Generate, review once, selectively revise, then render one Markdown proposal."""
    image_root = Path(image_root or RAG_DB_PATH)
    try:
        plan = _plan_from(llm.generate(f"用户需求：\n{request}", system_prompt=_PLAN_SYSTEM))
    except (ValueError, json.JSONDecodeError) as initial_error:
        try:
            repaired = llm.generate(f"以下大纲 JSON 不符合格式：{initial_error}。请仅修复 JSON。\n用户需求：{request}", system_prompt=_PLAN_SYSTEM)
            plan = _plan_from(repaired)
        except Exception as repair_error:  # noqa: BLE001
            raise ProposalGenerationError("planning", repair_error, detail=f"initial format error: {initial_error}") from repair_error
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("planning", exc) from exc

    sections: dict[str, _SectionDraft] = {}
    for section in plan.sections:
        try:
            evidence = _evidence(retriever(section.retrieval_queries).hits)
        except Exception as exc:  # noqa: BLE001
            raise ProposalGenerationError(f"retrieval:{section.section_id}", exc) from exc
        draft = _SectionDraft(section, "", evidence, _image_catalog(evidence))
        prompt = f"用户需求：{request}\n方案标题：{plan.title}\n完整大纲：{[(s.section_id, s.heading) for s in plan.sections]}\n当前章节：{section.heading}\n写作目标：{section.writing_goal}\n允许证据：\n{_evidence_prompt(evidence, draft.images)}"
        try:
            raw = llm.generate(prompt, system_prompt=_WRITE_SYSTEM)
        except Exception as exc:  # noqa: BLE001
            raise ProposalGenerationError(f"writing:{section.section_id}", exc) from exc
        raw = _validated_raw(llm, raw, draft, stage=f"citation_validation:{section.section_id}", repair_prompt="你的章节包含无效 ID 或直接路径", system_prompt=_WRITE_SYSTEM)
        draft = _SectionDraft(section, raw, evidence, draft.images)
        sections[section.section_id] = draft

    try:
        readable, _assets = _render(plan, sections, image_root=image_root, request=request, preview=True)
        issues = _review_from(llm.generate(f"章节 ID：{[s.section_id for s in plan.sections]}\n完整方案（仅供审查，不得重写）：\n{readable}", system_prompt=_REVIEW_SYSTEM), set(sections))
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("review", exc) from exc
    for section_id in dict.fromkeys(issue.section_id for issue in issues):
        old = sections[section_id]
        instructions = [issue.instruction for issue in issues if issue.section_id == section_id]
        try:
            raw = llm.generate(f"当前章节：{old.plan.heading}\n当前原始正文（保留内部 ID）：\n{old.raw_body}\n审查意见：{instructions}\n允许证据：\n{_evidence_prompt(old.evidence, old.images)}", system_prompt=_REVISE_SYSTEM)
        except Exception as exc:  # noqa: BLE001
            raise ProposalGenerationError(f"revision:{old.plan.section_id}", exc) from exc
        raw = _validated_raw(llm, raw, old, stage=f"citation_validation:{old.plan.section_id}", repair_prompt="修订稿包含无效 ID 或直接路径", system_prompt=_REVISE_SYSTEM)
        sections[section_id] = _SectionDraft(old.plan, raw, old.evidence, old.images)
    try:
        markdown, assets = _final_markdown(plan, sections, image_root=image_root, output_path=output_path, request=request)
    except ProposalQualityError as exc:
        raise ProposalGenerationError("quality_gate", exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("markdown_write", exc) from exc
    try:
        _write(output_path, markdown, assets)
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("markdown_write", exc) from exc
    return output_path
