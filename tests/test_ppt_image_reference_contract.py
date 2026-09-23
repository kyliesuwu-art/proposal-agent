from __future__ import annotations

import importlib.util

import pytest

import scripts.run_ppt_visual_calibration_v4_ark as v4


def scene(node_id: str, image_source: str) -> dict:
    return {
        "background": {"type": "solid", "color": "#FFFFFF"},
        "elements": [{
            "id": node_id, "type": "image", "x": 1, "y": 1,
            "w": 2, "h": 2, "z_order": 1, "image_source": image_source,
        }],
    }


@pytest.mark.parametrize("node_id", ["screenshot", "ai_platform_screenshot", "diagram_1"])
def test_node_id_is_not_an_image_reference(node_id: str) -> None:
    validation = v4.PAD.validate(scene(node_id, "assets/image-001.jpg"), {"assets/image-001.jpg"})
    assert validation["hard_errors"] == []


@pytest.mark.parametrize("source", [
    "assets/not-approved.jpg", "references/screenshot.jpg", "C:/outside/image.jpg",
    "file:///outside/image.jpg", "https://example.invalid/image.jpg", "../assets/image-001.jpg",
])
def test_only_manifest_image_source_is_accepted(source: str) -> None:
    validation = v4.PAD.validate(scene("ordinary_node", source), {"assets/image-001.jpg"})
    assert any("unapproved_or_missing_image_source" in issue for issue in validation["hard_errors"])



def test_manifest_asset_id_is_an_allowed_image_source() -> None:
    validation = v4.PAD.validate(scene("hero_image", "asset-1"), {"asset-1", "assets/image-001.jpg"})
    assert validation["hard_errors"] == []
def test_production_context_exposes_manifest_content_images_only(monkeypatch) -> None:
    monkeypatch.setattr(v4, "APPROVED_TEXT", "# approved")
    monkeypatch.setattr(v4, "CONTENT_ASSETS", (
        {"asset_id": "asset-1", "image_source": "assets/image-001.jpg", "width": 537, "height": 489, "usage": "content_asset"},
    ))
    ctx = v4.context_for(1)
    assert ctx["allowed_content_images"] == [{"asset_id": "asset-1", "image_source": "assets/image-001.jpg", "width": 537, "height": 489, "usage": "content_asset"}]
    assert ctx["allowed_image_sources"] == ["asset-1", "assets/image-001.jpg"]
    assert "screenshot" not in repr(ctx)


def test_page_prompt_carries_manifest_images_but_not_style_reference_names(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(v4, "OUT", tmp_path)
    monkeypatch.setattr(v4, "APPROVED_TEXT", "# approved")
    monkeypatch.setattr(v4, "CONTENT_ASSETS", (
        {"asset_id": "asset-1", "image_source": "assets/image-001.jpg", "width": 537, "height": 489, "usage": "content_asset"},
    ))
    monkeypatch.setattr(v4, "page_png", lambda *_args: tmp_path / "style-reference.png")
    monkeypatch.setattr(v4, "image_part", lambda *_args: [])
    captured = {}

    def fake_ask_json(**kwargs):
        captured["prompt"] = kwargs["content"][-1]["text"]
        value = scene("screenshot", "assets/image-001.jpg")
        kwargs["validator"](value)
        return value, {}

    monkeypatch.setattr(v4, "ask_json", fake_ask_json)
    v4.page_request(1, {"direction": "fake"})
    assert "asset-1" in captured["prompt"]
    assert "assets/image-001.jpg" in captured["prompt"]
    assert "STYLE_REFERENCE_ONLY" in captured["prompt"]
    assert "ai_platform_screenshot" not in captured["prompt"]

def test_retry_message_keeps_exact_image_source_feedback() -> None:
    error = v4.ModelJsonContractError(
        "invalid_image_sources=['references/contact_sheet.png']; allowed_image_sources=['assets/image-001.jpg']"
    )
    prompt = v4._retry_system_prompt("system", error)
    assert "references/contact_sheet.png" in prompt
    assert "assets/image-001.jpg" in prompt
    assert "remove the image node" in prompt


def test_renderer_uses_image_source_not_node_id() -> None:
    spec = importlib.util.spec_from_file_location("production_renderer", "scripts/run_ppt_production.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "image_source" in module._add_image.__code__.co_consts