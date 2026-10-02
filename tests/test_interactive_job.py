"""Drives the three interactive phases (start -> build_microcopy_menu -> execute) end to end on a
copy of the synthetic exemplar client with a scripted transport, proving: micro-copy is generated
per content type BEFORE any gate, the picked micro-copy is gated (and blocks, not retries), brief
notes and an ad-hoc tone actually reach the prompts, and the chosen angle/tone/copy are recorded."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline import run_job
from pipeline.llm.transport import LLMResult
from pipeline.schemas import Brief
from pipeline.stages import tone_select

REPO_CLIENTS = Path(__file__).resolve().parent.parent / "clients"
BODY = " ".join(["The TI-4200 probe repeats to spec on every shift."] * 8)


class ScriptedTransport:
    """Answers by which prompt template it's looking at; records every prompt it saw."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.prompts.append(user_prompt)
        p = user_prompt
        if p.startswith("You are proposing content angles"):
            reply = [{"headline": "Repeatability first", "pitch": "Lead with the spec",
                      "structure": ["spec", "lead time"], "claims_used": ["kb-demo0001"]}]
        elif p.startswith("Generate short-copy candidates"):
            reply = [{"text": "Leverage the TI-4200", "strategy": "benefit-led"},
                     {"text": "Probe data you can repeat", "strategy": "curiosity-led"}]
        elif p.startswith("You are compiling a working spec"):
            reply = {"outline": ["spec"], "claims_to_use": ["kb-demo0001"]}
        elif p.startswith("Write the full draft"):
            reply = {"body": BODY, "claims_used": ["kb-demo0001"]}
        elif p.startswith("You are an independent editorial critic"):
            reply = {"verdict": "on_voice", "notes": []}
        elif p.startswith("You are an independent compliance reviewer"):
            reply = {"verdict": "clean", "notes": []}
        elif p.startswith("You are an independent fact checker"):
            reply = {"verdict": "supported", "unsupported": []}
        elif p.startswith("You are checking a content brief"):
            reply = {"conflicts": []}
        else:
            reply = {}
        return LLMResult(available=True, text=json.dumps(reply))

    def saw(self, template_start: str) -> list[str]:
        return [p for p in self.prompts if p.startswith(template_start)]


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    shutil.copytree(REPO_CLIENTS / "exemplar", clients / "exemplar")
    return clients, tmp_path / "output"


def _brief() -> Brief:
    return Brief(client_id="exemplar", goal="Introduce the TI-4200", audience="quality engineers",
                 format="email", notes="Never mention pricing.")


def _through_menu(roots, transport, tone=None):
    clients, output = roots
    session = run_job.start("exemplar", _brief(), clients, output, transport=transport)
    tone = tone or tone_select.custom_tone("Dry, exact, engineer-to-engineer", "Bench Notes")
    return run_job.build_microcopy_menu(session.job_id, 0, tone, clients, output, transport=transport)


def test_menu_uses_the_formats_fields_and_flags_before_any_gate(roots):
    transport = ScriptedTransport()
    session = _through_menu(roots, transport)

    assert set(session.microcopy_menu) == {"subject_line", "preheader", "cta"}
    first, second = session.microcopy_menu["subject_line"]
    assert any("leverage" in f for f in first.flags)
    assert second.flags == []
    assert not transport.saw("You are an independent")  # no judged gate has run yet
    assert all("Content type: email" in p for p in transport.saw("Generate short-copy candidates"))


def test_execute_records_choices_and_gates_the_picked_copy(roots):
    clients, output = roots
    transport = ScriptedTransport()
    session = _through_menu(roots, transport)
    picks = {"subject_line": "Probe data you can repeat", "cta": "Book a bench demo"}

    output_dir = run_job.execute(session.job_id, picks, clients, output, transport=transport)

    record = json.loads((output_dir / "job_record.json").read_text())
    assert record["status"] == "complete"
    assert record["angle"]["headline"] == "Repeatability first"
    assert record["tone"]["ad_hoc"] is True
    assert record["microcopy_selected"] == picks
    assert record["gate_results"][0]["gate_name"] == "microcopy_lint"

    package = json.loads((output_dir / "package.json").read_text())
    assert package["microcopy_selected"] == picks
    assert (output_dir / "package.pdf").exists()

    # the judged gates read the copy as a reader meets it, and voice judges the ad-hoc tone
    voice_prompt = transport.saw("You are an independent editorial critic")[0]
    assert "subject_line: Probe data you can repeat" in voice_prompt
    assert "Bench Notes" in voice_prompt and "one-off tone" in voice_prompt


def test_flagged_pick_blocks_before_drafting(roots):
    clients, output = roots
    transport = ScriptedTransport()
    session = _through_menu(roots, transport)

    with pytest.raises(run_job.JobBlocked, match="leverage"):
        run_job.execute(session.job_id, {"subject_line": "Leverage the TI-4200"}, clients, output, transport=transport)

    assert not transport.saw("Write the full draft")
    record = json.loads((output / "jobs" / session.job_id / "job_record.json").read_text())
    assert record["status"] == "awaiting_human"


def test_brief_notes_reach_angle_and_synthesis_prompts(roots):
    clients, output = roots
    transport = ScriptedTransport()
    session = _through_menu(roots, transport)
    run_job.execute(session.job_id, {}, clients, output, transport=transport)

    assert "Must follow: Never mention pricing." in transport.saw("You are proposing content angles")[0]
    assert "Must follow: Never mention pricing." in transport.saw("You are compiling a working spec")[0]
