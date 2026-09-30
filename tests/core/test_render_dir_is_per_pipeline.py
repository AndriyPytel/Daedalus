"""
Level 1 — each pipeline renders into a directory no other pipeline shares.

Two runs at once, in two processes or two Documents, would otherwise number
their renders from 1 into the same system temp directory and review each
other's pictures.
"""
import os

from core.freecad_apply import ApplyPipeline


def test_two_pipelines_never_share_a_render_directory():
    first, second = ApplyPipeline(), ApplyPipeline()

    assert os.path.dirname(first._render_path()) != os.path.dirname(second._render_path())


def test_one_pipeline_keeps_its_render_directory_across_renders():
    pipeline = ApplyPipeline()

    assert os.path.dirname(pipeline._render_path()) == os.path.dirname(pipeline._render_path())


def test_a_given_render_directory_is_used_as_is(tmp_path):
    assert os.path.dirname(ApplyPipeline(render_dir=str(tmp_path))._render_path()) == str(tmp_path)
