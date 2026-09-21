from __future__ import annotations

from pathlib import Path

import pytest

import scripts.run_ppt_v4_ark_full20 as producer


def prepare(output_dir: Path, scene_dir: Path | None = None) -> Path:
    return producer.prepare_fresh_job_scene_output_directory(
        output_dir,
        scene_dir or output_dir / "generated_scene_graph",
    )


def test_fresh_job_scene_directory_is_created_when_absent(tmp_path):
    output = tmp_path / "job"; scene = prepare(output)
    assert scene == output / "generated_scene_graph" and scene.is_dir()


def test_upstream_precreated_empty_scene_directory_is_allowed(tmp_path):
    output = tmp_path / "job"; scene = output / "generated_scene_graph"
    scene.mkdir(parents=True)
    assert prepare(output, scene) == scene


def test_precreated_empty_topology_accepts_fake_producer_output(tmp_path):
    output = tmp_path / "job"; scene = output / "generated_scene_graph"
    scene.mkdir(parents=True)
    ark_calls: list[str] = []
    prepare(output, scene)
    ark_calls.append("page_01")
    (scene / "page_01.json").write_text("{}", encoding="utf-8")
    assert ark_calls == ["page_01"] and (scene / "page_01.json").is_file()


@pytest.mark.parametrize("entry", ["page_01.json", ".hidden", "child"])
def test_any_existing_scene_entry_is_rejected_before_fake_ark(entry, tmp_path):
    output = tmp_path / "job"; scene = output / "generated_scene_graph"; scene.mkdir(parents=True)
    target = scene / entry
    if entry == "child": target.mkdir()
    else: target.write_text("old", encoding="utf-8")
    ark_calls: list[str] = []
    with pytest.raises(RuntimeError, match="not empty"):
        prepare(output, scene)
    assert not ark_calls and target.exists()


def test_old_producer_manifest_rejects_an_empty_scene_directory(tmp_path):
    output = tmp_path / "job"; scene = output / "generated_scene_graph"; scene.mkdir(parents=True)
    (output / "producer_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="completed producer"):
        prepare(output, scene)


def test_old_internal_scene_graph_directory_rejects_an_empty_scene_directory(tmp_path):
    output = tmp_path / "job"; scene = output / "generated_scene_graph"; scene.mkdir(parents=True)
    (output / "scene_graphs").mkdir()
    with pytest.raises(RuntimeError, match="already contains"):
        prepare(output, scene)


def test_scene_path_must_be_a_directory_inside_its_job_root(tmp_path):
    output = tmp_path / "job"; output.mkdir()
    file_target = output / "generated_scene_graph"; file_target.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="directory"):
        prepare(output, file_target)
    outside = tmp_path / "outside" / "generated_scene_graph"
    with pytest.raises(RuntimeError, match="inside the current Job output root"):
        prepare(output, outside)


def test_same_job_cannot_start_again_after_fake_producer_writes_scene(tmp_path):
    output = tmp_path / "job"; scene = prepare(output)
    (scene / "page_01.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not empty"):
        prepare(output, scene)
    new_job = tmp_path / "new-job"
    assert prepare(new_job).is_dir()


def test_scene_symlink_is_rejected_without_following_it(tmp_path):
    output = tmp_path / "job"; output.mkdir(); outside = tmp_path / "outside"; outside.mkdir()
    scene = output / "generated_scene_graph"
    try:
        scene.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows test host")
    with pytest.raises(RuntimeError, match="symlink or reparse"):
        prepare(output, scene)
