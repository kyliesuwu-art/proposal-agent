from src.artifact_assets.markdown_parser import extract_markdown_images


def test_extracts_standard_images_and_ignores_code_fences():
    text = "![封面](assets/中文 图.png \"title\")\n```md\n![fake](no.png)\n```\n![two](a/b.jpg)\n"
    images = extract_markdown_images(text)
    assert [(item.alt_text, item.reference, item.line_number) for item in images] == [
        ("封面", "assets/中文 图.png", 1), ("two", "a/b.jpg", 5)
    ]


def test_normalizes_windows_and_url_encoded_safe_paths():
    images = extract_markdown_images("![x](assets\\%E4%B8%AD%E6%96%87.png)\n")
    assert images[0].normalized_reference == "assets/中文.png"
