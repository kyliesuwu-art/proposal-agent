"""Keep public module names from silently replacing earlier definitions."""

import ast
from collections import Counter
from pathlib import Path


def test_source_modules_have_no_duplicate_top_level_definitions() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    duplicates = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = Counter(
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        duplicates.extend(f"{path.relative_to(root)}:{name}" for name, count in names.items() if count > 1)
    assert not duplicates, f"Repeated module definitions: {', '.join(sorted(duplicates))}"
