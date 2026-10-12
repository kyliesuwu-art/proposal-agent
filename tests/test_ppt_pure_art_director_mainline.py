import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_ppt_pure_art_director_editorial.py"


def _module():
    spec = importlib.util.spec_from_file_location("editorial_chrome", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_mainline_chrome_uses_one_image_logo_and_sequential_page_format():
    module = _module()
    assert module.PROJECT_FOOTER == "医院园区智慧配电改造方案"
    assert [f"{page:02} / 15" for page in range(1, 16)] == [
        f"{page:02} / {len(module.ORDER):02}" for page in range(1, 16)
    ]
    # The analysed company template is an intentionally untracked private
    # input.  Unit tests verify the portable contract, while the renderer
    # performs the availability check at the point it consumes that input.
    assert module.FORMAL_LOGO_DECK.name.lower().endswith(".pptx")
    assert "logo_source_shape" in module.chrome.__code__.co_varnames


def test_mainline_title_fixes_do_not_change_title_copy():
    module = _module()
    h = {"elements": [{"id": "txt_title", "text": "保持标题"}, {"id": "txt_lead", "text": "保持导语"}]}
    b = {"elements": [{"id": "page_title_prefix", "text": "部署"}, {"id": "page_title_highlight", "text": "AI"}, {"id": "page_title_suffix", "text": "保持标题"}]}
    h_fixed, _ = module.mainline_scene("H", h)
    b_fixed, _ = module.mainline_scene("B", b)
    assert [item["text"] for item in h_fixed["elements"]] == ["保持标题", "保持导语"]
    assert [item["text"] for item in b_fixed["elements"]] == ["部署", "AI", "保持标题"]
    assert h_fixed["elements"][0]["w"] == 4.45
    assert b_fixed["elements"][0]["w"] == 1.08
