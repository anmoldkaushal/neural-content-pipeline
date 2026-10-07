"""Agent review of the knowledge base: duplicates merge under one wording with every source kept,
a fact is confirmed by the agent only when it is supported, matched to the source in code, low-risk
and uncontradicted; everything else either leaves the queue with a reason or waits for a person,
ranked. Undo puts it all back."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.kb import context
from pipeline.kb.provenance import verify_client
from pipeline.llm.transport import LLMResult
from pipeline.schemas import ContextDoc, ContextSection, KBEntry
from pipeline.stages import kb_triage

BRIEFING = ("The Contoso Open House runs March 3 to 5 in Springfield. The cohort is 150 engineers. "
            "Attendees are selected through a curated application process. Tickets cost 25,000.")
CALL = "Pat said they feel swamped and want a quieter quarter. The host mentioned the cohort is 150 engineers."


class Reviewer:
    """Answers the two review prompts from canned replies; records prompts."""

    def __init__(self, dupes: dict, assessments: dict[str, dict]) -> None:
        self.dupes, self.assessments, self.prompts = dupes, assessments, []

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.prompts.append(user_prompt)
        if user_prompt.startswith("You are cleaning up"):
            return LLMResult(available=True, text=json.dumps(self.dupes))
        ids = [line.split("]")[0][1:] for line in user_prompt.split("FACTS:\n")[1].split("\n\n")[0].splitlines()]
        return LLMResult(available=True, text=json.dumps([self.assessments[i] | {"id": i} for i in ids if i in self.assessments]))


def _yes(quote: str, **kw) -> dict:
    return {"client_fact": True, "supported": "yes", "quote": quote, "high_stakes": [], "informal": False,
            "copy_value": 2, "note": ""} | kw


@pytest.fixture
def client(tmp_path: Path) -> Path:
    c = tmp_path / "acme"
    (c / "knowledge_base").mkdir(parents=True)
    (c / "provenance.json").write_text("[]")
    context.save_doc(c, ContextDoc(name="briefing.pdf", role="strategy", summary="",
                                   sections=[ContextSection(heading="", text=BRIEFING)]))
    context.save_doc(c, ContextDoc(name="call.docx", role="notes", summary="",
                                   sections=[ContextSection(heading="", text=CALL)]))
    facts = [
        ("kb-curated", "Attendees are selected through a curated application process.", "briefing.pdf"),
        ("kb-cohort", "The cohort is 150 engineers.", "briefing.pdf"),
        ("kb-cohort2", "The open house hosts 150 engineers.", "call.docx"),
        ("kb-price", "Tickets cost 25,000.", "briefing.pdf"),
        ("kb-attendee", "Pat feels swamped.", "call.docx"),
        ("kb-made-up", "Contoso guarantees a 2x yield lift.", "briefing.pdf"),
        ("kb-wrongnum", "The open house runs for 4 days.", "briefing.pdf"),
    ]
    (c / "knowledge_base" / "kb_index.json").write_text(json.dumps(
        [KBEntry(id=i, claim=t, source_doc=d).model_dump(mode="json") for i, t, d in facts]))
    return c


def _reviewer() -> Reviewer:
    return Reviewer(
        {"duplicates": [{"keep": "kb-cohort", "same": ["kb-cohort2"], "claim": "The cohort is 150 engineers."}],
         "conflicts": []},
        {
            "kb-curated": _yes("selected through a curated application process"),
            "kb-cohort": _yes("The cohort is 150 engineers", high_stakes=["number"]),
            "kb-price": _yes("Tickets cost 25,000", high_stakes=["price"]),
            "kb-attendee": {"client_fact": False, "kind_if_not": "personal", "note": "about an attendee"},
            "kb-made-up": {"client_fact": True, "supported": "no", "quote": "", "note": "not in the briefing"},
            "kb-wrongnum": _yes("runs March 3 to 5", high_stakes=["number"]),
        },
    )


def _index(client: Path) -> dict[str, KBEntry]:
    return {e["id"]: KBEntry(**e) for e in json.loads((client / "knowledge_base" / "kb_index.json").read_text())}


def test_review_sorts_the_queue(client):
    summary = kb_triage.run(client, transport=_reviewer())
    idx = _index(client)
    assert summary | {"warnings": []} == {"reviewed": 7, "auto_verified": 2, "needs_you": 2, "duplicate": 1,
                                          "unsupported": 1, "not_client_fact": 1, "warnings": []}
    # plain, low-risk, matched: the agent confirms it, and provenance says so
    assert idx["kb-curated"].status == "verified" and idx["kb-curated"].confirmed_by == kb_triage.AGENT
    prov = json.loads((client / "provenance.json").read_text())
    assert {p["confirmed_by"] for p in prov} == {kb_triage.AGENT} and verify_client(client)[0]
    # duplicate merged, both sources kept
    assert idx["kb-cohort2"].status == "excluded" and idx["kb-cohort2"].superseded_by == "kb-cohort"
    assert idx["kb-cohort"].also_sources == ["call.docx"]
    # personal details about an attendee aren't kept at all; an unsupported fact is rejected
    assert "kb-attendee" not in idx
    assert idx["kb-made-up"].status == "rejected"
    # a price from one document waits for a person
    assert idx["kb-price"].triage == "needs_you" and "price" in idx["kb-price"].triage_reason


def test_a_figure_missing_from_the_source_is_caught_in_code(client):
    kb_triage.run(client, transport=_reviewer())
    e = _index(client)["kb-wrongnum"]
    assert e.status == "pending" and "figure(s) not in the source: 4" in e.triage_reason


def test_corroborated_number_from_a_formal_source_still_flags_only_what_it_should(client):
    kb_triage.run(client, transport=_reviewer())
    # 150 is stated in the briefing and the call: corroborated, so the number alone doesn't hold it
    assert _index(client)["kb-cohort"].status == "verified"


def test_needs_you_is_ranked_and_conflicts_come_first(client):
    r = _reviewer()
    r.dupes["conflicts"] = [{"ids": ["kb-price", "kb-curated"], "issue": "made-up clash"}]
    kb_triage.run(client, transport=r)
    queue = kb_triage.needs_you(client)
    assert queue[0].id in {"kb-price", "kb-curated"} and "conflicts" in queue[0].triage_reason


def test_quote_not_in_the_source_is_not_auto_confirmed(client):
    r = _reviewer()
    r.assessments["kb-curated"] = _yes("hand-picked by the founders themselves")
    kb_triage.run(client, transport=r)
    assert _index(client)["kb-curated"].triage == "needs_you"


def test_a_second_run_only_reviews_new_facts(client):
    kb_triage.run(client, transport=_reviewer())
    again = Reviewer({"duplicates": [], "conflicts": []}, {})
    assert kb_triage.run(client, transport=again)["reviewed"] == 0 and again.prompts == []


def test_transport_down_leaves_everything_for_a_person(client):
    class Down:
        def call(self, s, u):
            return LLMResult(available=False, error="offline")
    summary = kb_triage.run(client, transport=Down())
    assert summary["auto_verified"] == 0 and summary["needs_you"] == 7 and summary["warnings"]
    assert json.loads((client / "provenance.json").read_text()) == []


def test_edit_and_confirm_records_the_person(client):
    kb_triage.run(client, transport=_reviewer())
    kb_triage.edit_and_confirm(client, "kb-price", "Tickets cost INR 25,000.", "Ana")
    e = _index(client)["kb-price"]
    assert e.claim == "Tickets cost INR 25,000." and e.original_claim == "Tickets cost 25,000."
    assert e.status == "verified" and e.confirmed_by == "Ana"


def test_undo_restores_agent_decisions_but_keeps_a_persons(client):
    kb_triage.run(client, transport=_reviewer())
    kb_triage.edit_and_confirm(client, "kb-price", "Tickets cost INR 25,000.", "Ana")
    kb_triage.undo(client)
    idx = _index(client)
    assert all(idx[i].status == "pending" and idx[i].triage is None
               for i in ("kb-curated", "kb-cohort", "kb-cohort2", "kb-made-up", "kb-wrongnum"))
    assert idx["kb-cohort2"].superseded_by is None and idx["kb-cohort"].also_sources == []
    assert idx["kb-price"].confirmed_by == "Ana"  # a person's decision stands
    prov = json.loads((client / "provenance.json").read_text())
    assert [p["confirmed_by"] for p in prov] == ["Ana"]


def test_missing_figures_and_quote_matching():
    assert kb_triage.missing_figures("Tickets cost 25,000 for 3 days", "tickets: 25000, two days") == ["3"]
    assert kb_triage.quote_found("“Curated  application”", 'a "curated application" process')
    assert not kb_triage.quote_found("short", "short")


def test_call_json_keeps_a_value_followed_by_stray_text():
    from pipeline.llm.transport import call_json

    class Chatty:
        def call(self, s, u):
            return LLMResult(available=True, text='[{"id": "kb-1"}]\n\nNote: I checked every fact.')
    parsed, result = call_json(Chatty(), "", "x")
    assert parsed == [{"id": "kb-1"}] and result.ok
