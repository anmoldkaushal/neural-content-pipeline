"""The read-side helpers behind the UI: job stats, brief pre-fill, and saving a tone preset."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline import brief_defaults
from pipeline.ledger import stats
from pipeline.schemas import GateResult, GateStatus, JobRecord, TonePreset
from pipeline.stages import tone_select


def _rec(job_id, status, retries=None, failed_gate=None):
    gates = [GateResult(gate_name=failed_gate, status=GateStatus.FAILED, detail="x")] if failed_gate else []
    return JobRecord(job_id=job_id, client_id="c", brief_summary="b", status=status,
                     retry_count=retries or {}, gate_results=gates)


def test_summary_rates_exclude_jobs_still_awaiting_selection():
    records = [
        _rec("a", "complete"),
        _rec("b", "complete", retries={"style_lint": 1}),
        _rec("c", "awaiting_human", retries={"voice_critic": 2}, failed_gate="voice_critic"),
        _rec("d", "awaiting_selection"),
    ]
    s = stats.summarize(records)
    assert s["attempted"] == 3
    assert s["completion_rate"] == pytest.approx(2 / 3)
    assert s["first_pass_rate"] == pytest.approx(1 / 3)
    assert s["retries_by_gate"] == {"style_lint": 1, "voice_critic": 2}
    assert s["failures_by_gate"] == {"voice_critic": 1}


def test_load_records_filters_by_client_and_reports_unreadable(tmp_path: Path):
    for rec in (_rec("a", "complete"), JobRecord(job_id="z", client_id="other", brief_summary="b")):
        (tmp_path / "jobs" / rec.job_id).mkdir(parents=True)
        (tmp_path / "jobs" / rec.job_id / "job_record.json").write_text(rec.model_dump_json())
    (tmp_path / "jobs" / "bad").mkdir()
    (tmp_path / "jobs" / "bad" / "job_record.json").write_text("{not json")

    records, unreadable = stats.load_records(tmp_path, "c")
    assert [r.job_id for r in records] == ["a"]
    assert unreadable == ["bad"]


def test_defaults_prefer_same_format_brief_then_config(tmp_path: Path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "e.yaml").write_text(yaml.safe_dump(
        {"format": "email", "goal": "g", "audience": "founders", "notes": "n"}))

    email = brief_defaults.defaults_for(tmp_path, "acme", "email")
    assert (email["goal"], email["audience"], email["notes"]) == ("g", "founders", "n")
    assert email["target_word_count"] == 150  # from config/pipeline_config.yaml

    blog = brief_defaults.defaults_for(tmp_path, "acme", "blog_post")
    assert blog["goal"] == "" and blog["audience"] == "founders"


def test_save_preset_keeps_draft_header_and_refuses_duplicates(tmp_path: Path):
    (tmp_path / "tone_presets.yaml").write_text(
        "# DRAFT — review before use\npresets:\n  - name: A\n    description: d\n    sample_line: s\n")
    tone_select.save_preset(tmp_path, TonePreset(name="B", description="d2", sample_line="s2"))

    text = (tmp_path / "tone_presets.yaml").read_text()
    assert text.startswith("# DRAFT")
    assert [p["name"] for p in yaml.safe_load(text)["presets"]] == ["A", "B"]
    with pytest.raises(ValueError):
        tone_select.save_preset(tmp_path, TonePreset(name="A", description="x", sample_line="y"))


def test_custom_tone_is_marked_ad_hoc_and_needs_a_description():
    assert tone_select.custom_tone("dry and exact").ad_hoc is True
    with pytest.raises(ValueError):
        tone_select.custom_tone("  ")
