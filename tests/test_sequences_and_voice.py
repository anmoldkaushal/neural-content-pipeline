"""Email sequences are written one email at a time, each knowing the ones before it and gated on
its own; preflight sends a multi-piece goal to a sequence format; the voice judge sees the facts the
writer may state and its own earlier notes, so it can't fail a confirmed fact or reverse itself."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline import icps, run_job
from pipeline.llm.transport import LLMResult
from pipeline.schemas import Brief
from pipeline.stages import preflight, revise, tone_select
from tests.test_interactive_job import ScriptedTransport

REPO_CLIENTS = Path(__file__).resolve().parent.parent / "clients"


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    shutil.copytree(REPO_CLIENTS / "exemplar", clients / "exemplar")
    return clients, tmp_path / "output"


def _sequence(roots, transport, n=3):
    clients, output = roots
    brief = Brief(client_id="exemplar", goal="Introduce the TI-4200 to quality engineers", audience="QA leads",
                  format="email_sequence", sequence_length=n)
    session = run_job.start("exemplar", brief, clients, output, transport=transport)
    run_job.choose_angle(session.job_id, 0, tone_select.custom_tone("Dry, exact"), output)
    return session.job_id


def test_a_sequence_writes_and_gates_each_email(roots):
    clients, output = roots
    transport = ScriptedTransport()
    job_id = _sequence(roots, transport)
    output_dir = run_job.execute(job_id, {}, clients, output, transport=transport)

    drafts = transport.saw("Write the full draft")
    assert len(drafts) == 3
    assert "Write ONLY email 1 of 3" in drafts[0] and "Write ONLY email 3 of 3" in drafts[2]
    assert "Email 1: The TI-4200" in drafts[1]  # email 2 is written knowing email 1
    package = json.loads((output_dir / "package.json").read_text())
    assert package["sequence_length"] == 3 and len(package["sequence"]) == 3
    assert package["draft"]["body"].startswith("EMAIL 1 OF 3")
    gate_names = [g["gate_name"] for g in package["compliance_report"]["gates"]]
    assert "voice_critic (email 2)" in gate_names and len(transport.saw("You are an independent editorial critic")) == 3
    assert (output_dir / "drafts" / "email-3-rev-0.json").exists()


class VoiceRejectsEmailTwo(ScriptedTransport):
    def call(self, system_prompt, user_prompt):
        if user_prompt.startswith("You are an independent editorial critic") and "EMAIL" not in user_prompt:
            spec_two = any("Write ONLY email 2 of" in p for p in self.saw("Write the full draft"))
            if spec_two:
                self.prompts.append(user_prompt)
                return LLMResult(available=True, text=json.dumps(
                    {"verdict": "off_voice", "notes": [{"severity": "blocking", "note": "opens with a hedge"}]}))
        return super().call(system_prompt, user_prompt)


def test_a_failing_email_escalates_with_the_emails_so_far(roots):
    clients, output = roots
    transport = VoiceRejectsEmailTwo()
    job_id = _sequence(roots, transport)
    with pytest.raises(revise.RetryBudgetExceeded):
        run_job.execute(job_id, {}, clients, output, transport=transport)
    output_dir = output / "jobs" / job_id
    record = json.loads((output_dir / "job_record.json").read_text())
    assert record["status"] == "awaiting_human"
    assert any(n.startswith("email 2 of 3: gate 'voice_critic'") for n in record["human_touchpoints"])
    failing = json.loads((output_dir / "last_failed_draft.json").read_text())["body"]
    assert "EMAIL 1 OF 3" in failing and "EMAIL 2 OF 3" in failing and "EMAIL 3" not in failing
    assert len(transport.saw("Write the full draft")) == 2  # email 3 was never started


class VoiceFailsOnceThenRecords(ScriptedTransport):
    def __init__(self):
        super().__init__()
        self.voice = 0

    def call(self, system_prompt, user_prompt):
        if user_prompt.startswith("You are an independent editorial critic"):
            self.voice += 1
            if self.voice == 1:
                self.prompts.append(user_prompt)
                return LLMResult(available=True, text=json.dumps(
                    {"verdict": "off_voice", "notes": [{"severity": "blocking", "note": "stop describing the reader"}]}))
        return super().call(system_prompt, user_prompt)


def test_the_voice_judge_sees_the_facts_and_its_earlier_notes(roots):
    clients, output = roots
    transport = VoiceFailsOnceThenRecords()
    session = run_job.start("exemplar", Brief(client_id="exemplar", goal="Introduce the TI-4200",
                                               audience="QA leads", format="email"), clients, output, transport=transport)
    run_job.choose_angle(session.job_id, 0, tone_select.custom_tone("Dry"), output)
    run_job.execute(session.job_id, {}, clients, output, transport=transport)
    first, second = transport.saw("You are an independent editorial critic")[:2]
    assert "Verified facts the writer may state" in first and "kb-demo0001" in first
    assert "Your notes on earlier drafts" not in first
    assert "Your notes on earlier drafts" in second and "stop describing the reader" in second


def test_preflight_sends_several_emails_to_a_sequence():
    brief = Brief(client_id="c", goal="Create the whole series of drip emails (Email 1 to 5)", audience="a",
                  format="email")
    assert 'Pick "email sequence"' in preflight.format_issues(brief)[0]
    assert preflight.format_issues(brief.model_copy(update={"format": "email_sequence"})) == []
    assert preflight.format_issues(brief.model_copy(update={"goal": "Invite founders to apply"})) == []


def test_start_takes_the_sequence_length_from_the_format(roots):
    clients, output = roots
    brief = Brief(client_id="exemplar", goal="Introduce the TI-4200", audience="QA leads", format="email_sequence")
    session = run_job.start("exemplar", brief, clients, output, transport=ScriptedTransport())
    assert session.brief.sequence_length == 5


def test_the_angle_prompt_asks_for_one_step_per_email(roots):
    clients, output = roots
    transport = ScriptedTransport()
    _sequence(roots, transport, n=4)
    assert "give its structure as exactly 4 steps" in transport.saw("You are proposing content angles")[0]


def test_the_icp_guard_allows_speaking_to_the_reader():
    assert "second person" in icps.GUARD and "is fine" in icps.GUARD
    assert "targeting context, not facts" in icps.GUARD


def test_every_judge_gets_the_banned_terms_and_facts(roots):
    from pipeline.gates import judged
    clients, output = roots
    constraints = clients / "exemplar" / "constraints.yaml"
    constraints.write_text(constraints.read_text() + 'do_not_frame:\n  - "Do not promise results."\n')
    transport = VoiceFailsOnceThenRecords()
    session = run_job.start("exemplar", Brief(client_id="exemplar", goal="Introduce the TI-4200",
                                               audience="QA leads", format="email"), clients, output, transport=transport)
    run_job.choose_angle(session.job_id, 0, tone_select.custom_tone("Dry"), output)
    run_job.execute(session.job_id, {}, clients, output, transport=transport)
    profile = run_job._load_client_profile(clients / "exemplar")
    term = judged.banned_terms(profile)[0]
    for start in ("You are an independent editorial critic", "You are an independent compliance reviewer"):
        prompt = transport.saw(start)[0]
        assert f"Never suggest a fix that uses one" in prompt and term in prompt, start
        assert "Verified facts the writer may state" in prompt and "kb-demo0001" in prompt, start


def test_a_revision_is_told_banned_terms_win_over_a_suggested_fix():
    from pipeline.schemas import GateResult, GateStatus
    failed = GateResult(gate_name="voice_critic", status=GateStatus.FAILED, detail="x",
                        flagged_items=["say it is not a conference"])
    text = revise.next_revision_instruction([failed], [], banned=["conference", "networking event"])
    assert "Never use these terms, even where a finding above suggests" in text and "conference; networking event" in text
    assert "Never use these terms" not in revise.next_revision_instruction([failed], [])


def test_the_framing_judge_reads_dont_address_narrowly():
    from pathlib import Path as P
    prompt = (P(__file__).resolve().parent.parent / "pipeline" / "llm" / "prompts" / "client_constraints_critic.md").read_text()
    assert "never requires" in prompt and "say who it is not for" in prompt


def test_each_gate_gets_two_revisions():
    from pipeline import config
    assert config.retry_budget() == 3
