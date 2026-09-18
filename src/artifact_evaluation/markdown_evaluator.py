from __future__ import annotations
import re
from pathlib import Path
from .common import SAFE_IMAGE_EXTENSIONS, check, digest, text_counts
from .models import ArtifactReport, EvaluationStatus, Severity, aggregate
from .profiles import Profile

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)")

def evaluate_markdown(value: str | Path, profile: Profile) -> ArtifactReport:
    path = Path(value); report = ArtifactReport("markdown", str(path), None, None, profile.name)
    if not path.is_file():
        report.checks.append(check("MARKDOWN_FILE_MISSING", EvaluationStatus.FAIL, Severity.ERROR, "markdown", "Markdown file is missing", location=str(path))); report.status = EvaluationStatus.FAIL; return report
    report.file_size, report.sha256 = path.stat().st_size, digest(path)
    try: text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        report.checks.append(check("MARKDOWN_UTF8_INVALID", EvaluationStatus.FAIL, Severity.ERROR, "markdown", "Markdown is not UTF-8 readable")); report.status = EvaluationStatus.FAIL; return report
    if not text.strip(): report.checks.append(check("MARKDOWN_EMPTY", EvaluationStatus.FAIL, Severity.ERROR, "markdown", "Markdown is empty"))
    headings = [(len(m.group(1)), m.group(2).strip(), index) for index, line in enumerate(text.splitlines(), 1) if (m := HEADING.match(line))]
    # A document H1 is often only a container for H2 sections; do not call it
    # empty merely because its first child heading follows immediately.
    empty_sections = sum(1 for n, (level, _, line) in enumerate(headings) if level > 1 and not any(x.strip() and not x.lstrip().startswith("#") for x in text.splitlines()[line:(headings[n+1][2]-1 if n+1 < len(headings) else len(text.splitlines()))]))
    jumps = sum(1 for index in range(1, len(headings)) if headings[index][0] > headings[index-1][0] + 1)
    duplicates = len(headings) - len({title.casefold() for _, title, _ in headings})
    images = IMAGE.findall(text); broken_syntax = text.count("![") - len(images)
    broken = unsafe = empty_images = 0
    for _, raw in images:
        candidate = Path(raw.replace("\\", "/"))
        target = (path.parent / candidate).resolve()
        try: target.relative_to(path.parent.resolve()); safe = not candidate.is_absolute() and ".." not in candidate.parts
        except ValueError: safe = False
        if not safe: unsafe += 1; continue
        if candidate.suffix.lower() not in SAFE_IMAGE_EXTENSIONS: unsafe += 1; continue
        if not target.is_file(): broken += 1
        elif target.stat().st_size == 0: empty_images += 1
    internal, source, paths = text_counts(text)
    report.metrics = {"heading_count": len(headings), "section_count": sum(level == 2 for level, _, _ in headings), "empty_section_count": empty_sections, "image_reference_count": len(images), "broken_image_count": broken, "unsafe_image_path_count": unsafe, "internal_label_count": internal, "source_reference_count": source}
    for code, count, message in (("MARKDOWN_EMPTY_SECTION", empty_sections, "empty section"), ("MARKDOWN_HEADING_JUMP", jumps, "heading level jump"), ("MARKDOWN_DUPLICATE_HEADING", duplicates, "duplicate heading"), ("MARKDOWN_IMAGE_SYNTAX", broken_syntax, "broken image syntax"), ("MARKDOWN_IMAGE_BROKEN", broken + empty_images, "broken or empty image"), ("MARKDOWN_IMAGE_UNSAFE", unsafe, "unsafe image path")):
        if count: report.checks.append(check(code, EvaluationStatus.FAIL, Severity.ERROR, "markdown", message, metric_value=count))
    if profile.delivery and internal: report.checks.append(check("MARKDOWN_INTERNAL_LABEL", EvaluationStatus.FAIL, Severity.ERROR, "markdown", "internal delivery labels found", metric_value=internal))
    if profile.delivery and (source or paths): report.checks.append(check("MARKDOWN_SOURCE_LEAK", EvaluationStatus.FAIL, Severity.ERROR, "markdown", "source chain or local path found", metric_value=source + paths))
    report.checks.append(check("MARKDOWN_PARSED", EvaluationStatus.PASS, Severity.INFO, "markdown", "UTF-8 Markdown parsed"))
    report.status = aggregate(report.checks); return report
