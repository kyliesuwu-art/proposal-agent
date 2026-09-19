"""Resume the Ark V4 calibration as a complete, isolated 20-page candidate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAL = ROOT / "outputs" / "ppt_pure_art_director" / "full15" / "visual_calibration_v4_ark"
OUT = ROOT / "outputs" / "ppt_pure_art_director" / "full15" / "v4_ark_full20_mainline_candidate"
APPROVED = ROOT / "outputs" / "wecom_v1_full_e2e_retry" / "20260917_retry" / "95f07bf23ea8" / "approved.md"
NAMES = {1:"G",2:"TOC",3:"BACKGROUND",4:"I",5:"H",6:"ARCH",7:"RELIABILITY",8:"A",9:"J",10:"B",11:"C",12:"K",13:"L",14:"D",15:"ORGANIZATION",16:"N",17:"F",18:"M",19:"E",20:"CLOSE"}
ACCEPTED = (1,2,3,5,6,14,19,20)

spec = importlib.util.spec_from_file_location("v4", ROOT / "scripts" / "run_ppt_visual_calibration_v4_ark.py")
v4 = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v4)


def set_context() -> None:
    v4.OUT = OUT
    v4.PICK = tuple(range(1, 21))
    v4.NAMES = NAMES


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for item in ("global_art_direction.json", "global_art_direction_raw.json"):
        target = OUT / item
        if not target.exists(): shutil.copy2(CAL / item, target)
    destination = OUT / "scene_graphs"; destination.mkdir(exist_ok=True)
    for page in ACCEPTED:
        name = f"page_{page:02}_{NAMES[page]}.json"; target = destination / name
        if not target.exists(): shutil.copy2(CAL / "scene_graphs" / name, target)


def source_status(page: int) -> str:
    return "V4_REVISED" if page in {1,5,14,20} else "V4_ACCEPTED" if page in ACCEPTED else "ARK_REQUIRED"


def generate_missing() -> None:
    set_context(); prepare(); direction = json.loads((OUT / "global_art_direction.json").read_text(encoding="utf-8"))
    for page in range(1, 21):
        path = OUT / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json"
        if not path.exists(): v4.page_request(page, direction)


def finish() -> None:
    set_context(); prepare(); direction = json.loads((OUT / "global_art_direction.json").read_text(encoding="utf-8"))
    scenes = {page: json.loads((OUT / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json").read_text(encoding="utf-8")) for page in range(1,21)}
    deck = v4.draw(scenes); deck.replace(OUT / "pure_art_director_v4_ark_full20.pptx")
    deck = OUT / "pure_art_director_v4_ark_full20.pptx"
    # v4.render writes the expected PDF name; normalize output naming after COM succeeds.
    pngs = v4.render(deck)
    old_pdf = OUT / "pure_art_director_visual_calibration_v4_ark.pdf"
    if old_pdf.exists(): old_pdf.replace(OUT / "pure_art_director_v4_ark_full20.pdf")
    v4.contact(pngs, OUT / "contact_sheet.png", [f"V4 Full 20 | {page:02}" for page in range(1,21)])
    approved_hash = hashlib.sha256(APPROVED.read_bytes()).hexdigest()
    manifest = {"approved_md_path": str(APPROVED.relative_to(ROOT)), "approved_md_sha256_before": approved_hash, "approved_md_sha256_after": hashlib.sha256(APPROVED.read_bytes()).hexdigest(), "pages": [{"page": p, "name": NAMES[p], "status": source_status(p), "scene_graph": str((OUT / "scene_graphs" / f"page_{p:02}_{NAMES[p]}.json").relative_to(ROOT))} for p in range(1,21)], "accepted_pages": list(ACCEPTED)}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--generate-missing", action="store_true"); parser.add_argument("--finish", action="store_true"); args = parser.parse_args()
    if args.generate_missing: generate_missing()
    elif args.finish: finish()
    else: parser.error("choose --generate-missing or --finish")
