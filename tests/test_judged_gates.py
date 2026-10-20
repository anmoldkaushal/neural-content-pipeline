"""The judged gates fail only on a blocking note, carry minor notes on a pass, and fail closed when
the judge's answer has no severity or no reason; the revise loop hands every failing gate and the
previous draft to the next pass."""
from __future__ import annotations

import json

from pipeline.gates import claims_critic, client_constraints_critic, judged, voice_critic
from pipeline.llm.transport import LLMResult
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus, ToneChoice
from pipeline.stages.revise import next_revision_instruction


class OneReply:
    def __init__(self, reply) -> None:
        self.reply, self.prompts = reply, []

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.prompts.append(user_prompt)
        return LLMResult(available=True, text=json.dumps(self.reply))


PROFILE = ClientProfile(client_id="c", company_name="Co", industry="M&A",
                        do_not_frame=["Do not name any client or deal."])
DRAFT = Draft(job_id="j", body="Hello there.", word_count=2)


def test_minor_notes_pass_and_are_kept():
    t = OneReply({"verdict": "on_voice", "notes": [{"severity": "minor", "note": "stock closing line"}]})
    r = voice_critic.run(DRAFT, PROFILE, transport=t)
    assert r.status == GateStatus.PASSED and r.notes == ["stock closing line"] and "1 minor" in r.detail


def test_a_blocking_note_fails_even_with_a_passing_verdict():
    t = OneReply({"verdict": "clean", "notes": [{"severity": "blocking", "note": "names the client"},
                                                {"severity": "minor", "note": "close call"}]})
    r = client_constraints_critic.run(DRAFT, PROFILE, transport=t)
    assert r.status == GateStatus.FAILED and r.flagged_items == ["names the client"] and r.notes == ["close call"]


def test_fails_closed_without_severity_or_reason():
    assert judged.verdict("g", {"verdict": "on_voice", "notes": ["no severity given"]}, "on_voice", "", "").status \
        == GateStatus.FAILED
    assert judged.verdict("g", {"verdict": "off_voice", "notes": []}, "on_voice", "", "").status == GateStatus.FAILED


def test_voice_judge_sees_the_limits_the_writer_had():
    t = OneReply({"verdict": "on_voice", "notes": []})
    voice_critic.run(DRAFT, PROFILE, transport=t, tone=ToneChoice(preset_name="Show"), must_follow="No P.S.")
    assert "Do not name any client or deal." in t.prompts[0] and "Brief must-follow: No P.S." in t.prompts[0]


def test_revision_instruction_carries_every_failing_gate():
    failed = [GateResult(gate_name=n, status=GateStatus.FAILED, detail="x", flagged_items=[f"{n} issue"])
              for n in ("client_constraints_critic", "voice_critic")]
    text = next_revision_instruction(failed)
    assert "client_constraints_critic issue" in text and "voice_critic issue" in text


def test_claims_judge_fails_only_on_blocking_findings():
    minor = OneReply({"verdict": "unsupported", "unsupported": [
        {"quote": "built on your data", "why": "close to kb-1", "severity": "minor"}]})
    r = claims_critic.run(DRAFT, [], PROFILE, transport=minor)
    assert r.status == GateStatus.PASSED and r.notes == ['"built on your data": close to kb-1'] and "1 minor" in r.detail

    blocking = OneReply({"verdict": "unsupported", "unsupported": [
        {"quote": "ready next week", "why": "no turnaround fact", "severity": "blocking"},
        {"quote": "built on your data", "why": "close to kb-1", "severity": "minor"}]})
    r = claims_critic.run(DRAFT, [], PROFILE, transport=blocking)
    assert r.status == GateStatus.FAILED and r.flagged_items == ['"ready next week": no turnaround fact']
    assert r.notes == ['"built on your data": close to kb-1']


def test_claims_judge_fails_closed_without_severity():
    t = OneReply({"verdict": "unsupported", "unsupported": [{"quote": "the best desk", "why": "no fact"}]})
    assert claims_critic.run(DRAFT, [], PROFILE, transport=t).status == GateStatus.FAILED
    t = OneReply({"verdict": "unsupported", "unsupported": []})
    assert claims_critic.run(DRAFT, [], PROFILE, transport=t).status == GateStatus.FAILED


def test_claims_judge_is_told_who_the_reader_is():
    t = OneReply({"verdict": "supported", "unsupported": []})
    claims_critic.run(DRAFT, [], PROFILE, transport=t, reader="The analyst at a boutique; searches by hand.")
    assert "READER (who the piece is for; context, not facts):\nThe analyst at a boutique" in t.prompts[0]
    assert "The reader's own situation is not a claim." in t.prompts[0]
