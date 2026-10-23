"""Public facts (pipeline/stages/research.py): the agent confirms a sourced fact about the world only
when its quote is really on a reputable page (or two sites), never a risky one; nothing is fetched
from the real web here. In a job, the writer and the claims judge see public facts, and a statement
the judge flags as about the world is sourced and judged again before a revision is spent."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import run_job
from pipeline.gates import claims_critic
from pipeline.llm.transport import ClaudeTransport, LLMResult
from pipeline.schemas import Brief, ClientProfile, Draft, KBEntry
from pipeline.stages import research, tone_select
from tests.test_interactive_job import BODY, REPO_CLIENTS, ScriptedTransport

FACT = "The temple of Kom Ombo was dedicated to two gods, Sobek and Horus."
QUOTE = "The temple at Kom Ombo is unusual in being dedicated to two gods, Sobek and Horus."
PAGES = {
    "https://www.britannica.com/place/Kom-Ombo": f"<html><script>var x=1;</script><p>{QUOTE}</p></html>",
    "https://travel-blog.example.com/kom-ombo": f"<p>{QUOTE}</p>",
    "https://another-blog.example.org/egypt": f"<div>{QUOTE}</div>",
}


def fetcher(url: str):
    html = PAGES.get(url)
    return research.page_text(html) if html is not None else None


def _src(url, quote=QUOTE):
    return {"url": url, "quote": quote}


# ------------------------------------------------------------------ policy


def test_reputable_source_with_checked_quote_is_auto_confirmed():
    checked = research.check({"claim": FACT, "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]}, fetcher)
    assert research.decide(FACT, checked, [])[0] == "auto_verified"


def test_two_independent_sites_stand_in_for_a_reputable_one():
    one = research.check({"claim": FACT, "sources": [_src("https://travel-blog.example.com/kom-ombo")]}, fetcher)
    assert research.decide(FACT, one, [])[0] == "needs_you"
    two = research.check({"claim": FACT, "sources": [_src("https://travel-blog.example.com/kom-ombo"),
                                                      _src("https://another-blog.example.org/egypt")]}, fetcher)
    assert research.decide(FACT, two, [])[0] == "auto_verified"


def test_a_quote_not_on_the_page_is_rejected():
    checked = research.check({"claim": FACT, "sources": [
        _src("https://www.britannica.com/place/Kom-Ombo", "Kom Ombo was built by the Atlanteans.")]}, fetcher)
    triage, reason = research.decide(FACT, checked, [])
    assert triage == "unsupported" and "isn't on" in reason


def test_a_figure_missing_from_the_quote_is_rejected():
    claim = "The temple of Kom Ombo was dedicated to two gods in 180 BC."
    checked = research.check({"claim": claim, "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]}, fetcher)
    assert research.decide(claim, checked, [])[0] == "unsupported"


def test_risky_claims_always_wait_for_a_person():
    checked = research.check({"claim": FACT, "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]}, fetcher)
    for stakes in (["medical"], ["substance"], ["legal"], ["contested"]):
        assert research.decide(FACT, checked, stakes)[0] == "needs_you"


def test_an_unfetchable_page_waits_for_a_person():
    checked = research.check({"claim": FACT, "sources": [_src("https://www.britannica.com/missing")]}, fetcher)
    assert research.decide(FACT, checked, [])[0] == "needs_you"


def test_a_blocked_page_is_checked_on_its_archived_copy(tmp_path):
    url = "https://www.metmuseum.org/blocked"
    pages = {research.archived(url): f"<p>{QUOTE}</p>"}  # the live page refuses scripts

    def archive_only(u):
        return research.page_text(pages[u]) if u in pages else None

    claims = [{"claim": FACT, "stakes": [], "sources": [_src(url)]}]
    new = research.for_piece(tmp_path, "t", [], "", transport=_ResearchTransport(claims), fetcher=archive_only)
    assert new[0].status == "verified" and new[0].source_doc == url
    assert "Internet Archive" in new[0].triage_reason


def test_page_text_drops_scripts():
    assert "var x" not in research.page_text(PAGES["https://www.britannica.com/place/Kom-Ombo"])


# ------------------------------------------------------------------ the stage


class _ResearchTransport:
    """Answers the research prompt with fixed candidates; records the tools each call had."""

    def __init__(self, claims):
        self.claims, self.tools_seen, self.prompts = claims, [], []
        self.tools: list[str] = []
        self.timeout = 180

    def call(self, system_prompt, user_prompt):
        self.tools_seen.append(list(self.tools))
        self.prompts.append(user_prompt)
        return LLMResult(available=True, text=json.dumps({"claims": self.claims}))


def test_for_piece_stores_public_facts_and_leaves_the_jobs_transport_offline(tmp_path):
    transport = _ResearchTransport([
        {"claim": FACT, "stakes": [], "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]},
        {"claim": "Ancient Egyptians used 5-MeO-DMT.", "stakes": ["substance"],
         "sources": [_src("https://www.britannica.com/place/Kom-Ombo", "Ancient Egyptians used 5-MeO-DMT.")]},
    ])
    new = research.for_piece(tmp_path, "Egypt guide", ["temples"], "", transport=transport, fetcher=fetcher)

    assert transport.tools_seen == [research.TOOLS] and transport.tools == []  # a copy got the tools
    by_claim = {e.claim: e for e in new}
    good = by_claim[FACT]
    assert (good.status, good.kind, good.confirmed_by) == ("verified", "public", research.AGENT)
    assert good.source_doc == "https://www.britannica.com/place/Kom-Ombo" and good.evidence == QUOTE
    assert by_claim["Ancient Egyptians used 5-MeO-DMT."].status == "rejected"
    assert [e.claim for e in research.verified(tmp_path)] == [FACT]


def test_a_rejected_fact_is_not_proposed_or_checked_again(tmp_path):
    claims = [{"claim": FACT, "stakes": [], "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]}]
    research.for_piece(tmp_path, "t", [], "", transport=_ResearchTransport(claims), fetcher=fetcher)
    fact_id = research.load(tmp_path)[0].id
    assert research.reject(tmp_path, [fact_id], "Anmol") == 1

    again = _ResearchTransport(claims)
    assert research.for_piece(tmp_path, "t", [], "", transport=again, fetcher=fetcher) == []
    assert "Rejected before" in again.prompts[0] and FACT in again.prompts[0]
    assert research.verified(tmp_path) == []
    assert research.confirm(tmp_path, [fact_id], "Anmol") == 1
    assert research.verified(tmp_path)[0].confirmed_by == "Anmol"


def test_cli_transport_passes_tools_only_when_set(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr=""))
    t = ClaudeTransport()
    t.transport, t._cli_path = "cli", "/bin/claude"
    t.call("", "hi")
    t.tools = ["WebSearch", "WebFetch"]
    t.call("", "hi")
    assert "--allowedTools" not in calls[0]
    assert calls[1][-2:] == ["--allowedTools", "WebSearch,WebFetch"]


# ------------------------------------------------------------------ the claims judge


def test_claims_judge_sees_public_facts_and_returns_world_statements():
    seen = []

    class T:
        def call(self, s, u):
            seen.append(u)
            return LLMResult(available=True, text=json.dumps({"verdict": "unsupported", "unsupported": [
                {"quote": "Kom Ombo honours two gods.", "why": "no fact", "severity": "blocking", "about": "world"},
                {"quote": "Our group visits Kom Ombo.", "why": "no fact", "severity": "blocking", "about": "client"},
            ]}))

    public = KBEntry(id="pub-1", claim=FACT, source_doc="https://www.britannica.com/place/Kom-Ombo",
                     kind="public", status="verified")
    result = claims_critic.run(Draft(job_id="j", body="x", word_count=1, outline=[], claims_used=[]),
                               [public], ClientProfile(client_id="c", company_name="C", industry="i"), transport=T())
    assert "Public facts" in seen[0] and FACT in seen[0]
    assert result.world_statements == ["Kom Ombo honours two gods."]


# ------------------------------------------------------------------ in a job


class _JobTransport(ScriptedTransport):
    """The exemplar job, plus a research reply, and a claims judge that flags one statement about
    the world until a public fact backs it."""

    def __init__(self, research_claims):
        super().__init__()
        self.research_claims = research_claims
        self.tools: list[str] = []
        self.timeout = 180

    def call(self, system_prompt, user_prompt):
        if user_prompt.startswith("You are a researcher"):
            self.prompts.append(user_prompt)
            return LLMResult(available=True, text=json.dumps({"claims": self.research_claims(user_prompt)}))
        if user_prompt.startswith("You are an independent fact checker") and "Public facts (checked against" not in user_prompt:
            self.prompts.append(user_prompt)
            return LLMResult(available=True, text=json.dumps({"verdict": "unsupported", "unsupported": [
                {"quote": "Kom Ombo honours two gods.", "why": "no fact", "severity": "blocking", "about": "world"}]}))
        return super().call(system_prompt, user_prompt)


@pytest.fixture
def roots(tmp_path: Path, monkeypatch):
    clients = tmp_path / "clients"
    shutil.copytree(REPO_CLIENTS / "exemplar", clients / "exemplar")
    monkeypatch.setattr(research, "fetch", fetcher)
    return clients, tmp_path / "output"


def _run(roots, transport):
    clients, output = roots
    brief = Brief(client_id="exemplar", goal="Introduce the TI-4200", audience="quality engineers", format="email")
    session = run_job.start("exemplar", brief, clients, output, transport=transport)
    tone = tone_select.custom_tone("Dry, exact", "Bench Notes")
    session = run_job.build_microcopy_menu(session.job_id, 0, tone, clients, output, transport=transport)
    return run_job.execute(session.job_id, {}, clients, output, transport=transport)


def test_outline_research_reaches_the_writer_and_the_package(roots):
    good = [{"claim": FACT, "stakes": [], "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]}]
    transport = _JobTransport(lambda prompt: good)
    output_dir = _run(roots, transport)

    record = json.loads((output_dir / "job_record.json").read_text())
    assert record["status"] == "complete" and "research" in record["stages_run"]
    assert any("1 public fact(s) confirmed by the agent" in t for t in record["human_touchpoints"])
    writer = transport.saw("Write the full draft")[0]
    assert "PUBLIC FACTS" in writer and FACT in writer and "britannica.com" in writer
    assert len(transport.saw("Write the full draft")) == 1  # the judge had the fact: no revision


def test_a_flagged_world_statement_is_sourced_before_a_revision_is_spent(roots):
    def reply(prompt):  # nothing for the outline; the flagged statement is sourceable
        if "STATEMENTS A FACT CHECKER FLAGGED" in prompt:
            return [{"claim": FACT, "stakes": [], "sources": [_src("https://www.britannica.com/place/Kom-Ombo")]}]
        return []

    transport = _JobTransport(reply)
    output_dir = _run(roots, transport)

    record = json.loads((output_dir / "job_record.json").read_text())
    assert record["status"] == "complete"
    assert "research (flagged statements, revision 0)" in record["stages_run"]
    assert not transport.saw("You are revising")  # sourced and re-judged, no revision spent
    claims = [g for g in record["gate_results"] if g["gate_name"] == "claims_critic"][0]
    assert claims["status"] == "passed"
    clients, _ = roots
    assert [e.claim for e in research.verified(clients / "exemplar")] == [FACT]


def test_research_can_be_switched_off(roots, monkeypatch):
    monkeypatch.setattr(research, "settings", lambda: {"enabled": False, "max_claims": 12})
    transport = ScriptedTransport()
    output_dir = _run(roots, transport)
    assert not transport.saw("You are a researcher")
    assert "research" not in json.loads((output_dir / "job_record.json").read_text())["stages_run"]
