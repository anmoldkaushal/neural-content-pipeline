"""The judged gates fail only on a blocking note, carry minor notes on a pass, and fail closed when
the judge's answer has no severity or no reason; the revise loop hands every failing gate and the
previous draft to the next pass."""
from __future__ import annotations

import json

from pipeline.gates import claims_critic, client_constraints_critic, cold_reader, judged, voice_critic
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


def test_a_stranger_who_cant_tell_what_is_offered_fails_the_piece():
    clear = OneReply({"verdict": "clear", "who": "an M&A research firm", "offer": "a research desk that finds buyers",
                      "ask": "reply yes", "notes": []})
    assert cold_reader.run(DRAFT, transport=clear).status == GateStatus.PASSED

    # the judge says "clear" but can't name the offer: still a failure
    vague = OneReply({"verdict": "clear", "who": "an M&A firm", "offer": "can't tell: 'the desk' is never explained",
                      "ask": "reply yes", "notes": []})
    r = cold_reader.run(DRAFT, transport=vague)
    assert r.status == GateStatus.FAILED and r.flagged_items == ["a stranger can't tell what is offered from this piece alone"]


def test_the_stranger_sees_only_the_piece():
    t = OneReply({"verdict": "clear", "who": "x", "offer": "y", "ask": "z", "notes": []})
    cold_reader.run(Draft(job_id="j", body="The desk finds buyers.", word_count=4), transport=t,
                    audience="An M&A analyst at a boutique")
    prompt = t.prompts[0]
    assert "Who you are: An M&A analyst at a boutique. You know your own work" in prompt
    assert "--- PIECE ---\nThe desk finds buyers.\n--- END PIECE ---" in prompt
    assert "Verified facts" not in prompt and "Co" not in prompt.split("--- PIECE ---")[0].split("\n")[-3:]


def test_the_writer_is_told_to_introduce_the_offering_when_read_cold():
    from pipeline.kb import rulebook
    from pipeline.schemas import Brief
    from pipeline import run_job
    assert "Read cold: the reader of every email" in rulebook.render(PROFILE, sequence=True, standalone=True, read_cold=True)
    assert "Read cold" not in rulebook.render(PROFILE)
    drip = Brief(client_id="c", goal="g", audience="a", format="email_sequence", sequence_length=3)
    assert run_job._read_cold(drip, 1) and not run_job._read_cold(drip, 2)
    assert run_job._read_cold(drip.model_copy(update={"standalone": True}), 2)
    assert not run_job._read_cold(Brief(client_id="c", goal="g", audience="a", format="blog_post"), None)
