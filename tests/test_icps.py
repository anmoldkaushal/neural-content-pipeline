"""ICPs: cleaned on load and save, reviewed as their own profile section, drafted at onboarding
next to the tone presets (and later, for existing clients, without overwriting reviewed ones), and
a picked ICP reaches every prompt that shapes the piece, with its not-a-claim guard."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from pipeline import client_setup, icps, profile_review, run_job
from pipeline.llm.transport import LLMResult
from pipeline.schemas import Brief
from pipeline.stages import tone_select
from tests.test_interactive_job import ScriptedTransport

REPO_CLIENTS = Path(__file__).resolve().parent.parent / "clients"


@pytest.fixture
def client(tmp_path: Path) -> Path:
    shutil.copytree(REPO_CLIENTS / "exemplar", tmp_path / "clients" / "exemplar")
    return tmp_path / "clients" / "exemplar"


class Replies:
    """A transport that returns one canned JSON reply and keeps the prompt."""

    def __init__(self, reply) -> None:
        self.reply, self.prompts = reply, []

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.prompts.append(user_prompt)
        return LLMResult(available=True, text=json.dumps(self.reply))


def test_normalize_cleans_rows_and_refuses_repeated_names():
    cleaned = icps.normalize([
        {"name": " buyer ", "summary": " Buys kit ", "pains": "Lead time\n\nCost", "roles": [], "default_tone": ""},
        {"name": "", "summary": "nameless rows are dropped"},
    ])
    assert cleaned == [{"name": "buyer", "summary": "Buys kit", "pains": ["Lead time", "Cost"]}]
    with pytest.raises(ValueError, match="twice"):
        icps.normalize([{"name": "a"}, {"name": "a"}])


def test_a_client_without_icps_loads_with_none(client):
    (client / icps.FILE).unlink()
    assert run_job._load_client_profile(client).icps == []
    assert profile_review.section_content(client, "icps") == []


def test_a_hand_typed_duplicate_still_loads(client):
    (client / icps.FILE).write_text(yaml.safe_dump({"icps": [{"name": "a", "summary": "1"}, {"name": "a", "summary": "2"}]}))
    assert [i.summary for i in icps.load(client)] == ["1"]
    with pytest.raises(ValueError, match="twice"):
        profile_review.save_section(client, "icps", profile_review.section_content(client, "icps"))


def test_icps_are_a_reviewed_section_with_their_own_draft_marker(client):
    assert profile_review.status(client)["icps"]["status"] == "draft"
    assert "ICPs" in profile_review.not_final(client)
    profile_review.save_section(client, "icps", [{"name": "buyer", "summary": "Buys kit", "pains": ["Lead time"]}])
    assert (client / icps.FILE).read_text().startswith("# DRAFT")
    profile_review.set_status(client, "icps", "final", "Ana")
    assert not (client / icps.FILE).read_text().startswith("# DRAFT")
    with pytest.raises(ValueError, match="reopen"):
        profile_review.save_section(client, "icps", [])


def test_default_tone_must_name_a_preset(client):
    with pytest.raises(ValueError, match="not a tone preset"):
        profile_review.save_section(client, "icps", [{"name": "buyer", "summary": "x", "default_tone": "shouty"}])
    profile_review.save_section(client, "icps", [{"name": "buyer", "summary": "x", "default_tone": "case_study"}])
    assert icps.load(client)[0].default_tone == "case_study"


def test_onboarding_drafts_icps_next_to_the_tone_presets(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "brand.txt").write_text("We sell probes to QA managers at mid-size plants.")
    reply = {"style_guide_summary": "Plain.", "tone_presets": [{"name": "plain", "description": "d", "sample_line": "s"}],
             "icps": [{"name": "qa_manager", "summary": "Runs QA", "pains": ["Rework"], "default_tone": "plain"},
                      {"name": "qa_manager", "summary": "repeat, dropped"},
                      {"name": "buyer", "summary": "Buys", "default_tone": "not_a_preset"}]}
    monkeypatch.setattr(client_setup, "ClaudeTransport", lambda: Replies(reply))
    target = client_setup.init_client("acme", tmp_path / "clients", from_docs=docs)
    drafted = icps.load(target)
    assert [i.name for i in drafted] == ["qa_manager", "buyer"]
    assert drafted[0].default_tone == "plain" and drafted[1].default_tone is None
    assert (target / icps.FILE).read_text().startswith("# DRAFT")


def test_drafting_later_adds_only_new_icps_and_uses_audience_notes(client):
    (client / "knowledge_base" / "documents").mkdir(parents=True, exist_ok=True)
    index = client / "knowledge_base" / "kb_index.json"
    entries = json.loads(index.read_text()) if index.exists() else []
    entries.append({"id": "kb-aud1", "claim": "Sells mostly to aerospace suppliers", "source_doc": "deck.pdf",
                    "status": "excluded", "kind": "audience"})
    index.write_text(json.dumps(entries))
    transport = Replies({"icps": [{"name": "plant_quality_manager", "summary": "would overwrite"},
                                  {"name": "aerospace_supplier", "summary": "Tier-2 aerospace shop"}]})
    added = client_setup.draft_icps(client, client / "knowledge_base" / "documents", transport=transport)
    assert added == 1
    assert "Sells mostly to aerospace suppliers" in transport.prompts[0]
    by_name = {i.name: i for i in icps.load(client)}
    assert by_name["plant_quality_manager"].summary.startswith("Runs quality assurance")  # reviewed row kept
    assert "aerospace_supplier" in by_name


def test_a_picked_icp_reaches_every_prompt_with_its_guard(client):
    clients, output = client.parent, client.parent.parent / "output"
    transport = ScriptedTransport()
    brief = Brief(client_id="exemplar", goal="Introduce the TI-4200", audience="QA managers", format="email",
                  icp="plant_quality_manager")
    session = run_job.start("exemplar", brief, clients, output, transport=transport)
    assert session.brief.icp_profile.startswith(icps.GUARD)
    run_job.build_microcopy_menu(session.job_id, 0, tone_select.select_tone(run_job._load_client_profile(client),
                                 "technical_precise"), clients, output, transport=transport)
    run_job.execute(session.job_id, {}, clients, output, transport=transport)
    for template in ("You are proposing content angles", "Generate short-copy candidates",
                     "You are compiling a working spec", "Write the full draft", "You are an independent editorial critic"):
        prompt = transport.saw(template)[0]
        assert "Inspectors re-measure parts by hand" in prompt and "targeting context, not facts" in prompt, template
    record = json.loads((output / "jobs" / session.job_id / "job_record.json").read_text())
    assert record["icp"] == "plant_quality_manager"


def test_an_unknown_icp_is_refused_before_any_model_call(client):
    transport = ScriptedTransport()
    brief = Brief(client_id="exemplar", goal="g", audience="a", format="email", icp="nobody")
    with pytest.raises(ValueError, match="unknown ICP"):
        run_job.start("exemplar", brief, client.parent, client.parent.parent / "output", transport=transport)
    assert transport.prompts == []


def test_a_brief_without_an_icp_sends_no_reader_profile(client):
    transport = ScriptedTransport()
    brief = Brief(client_id="exemplar", goal="g", audience="quality engineers", format="email")
    run_job.start("exemplar", brief, client.parent, client.parent.parent / "output", transport=transport)
    assert "targeting context" not in transport.saw("You are proposing content angles")[0]
