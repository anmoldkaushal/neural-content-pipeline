"""Deleting a fact from the UI (kb_verify.reject) and scaffolding a client (client_setup)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pipeline import client_setup
from pipeline.kb.provenance import verify_client
from pipeline.schemas import KBEntry
from pipeline.stages import kb_verify


def _client(tmp_path: Path) -> Path:
    (tmp_path / "knowledge_base").mkdir()
    entries = [KBEntry(id=f"kb-{i}", claim=f"Fact {i}.", source_doc="doc.pdf") for i in range(3)]
    (tmp_path / "knowledge_base" / "kb_index.json").write_text(
        json.dumps([e.model_dump(mode="json") for e in entries]))
    (tmp_path / "provenance.json").write_text("[]")
    return tmp_path


def _statuses(client_dir: Path) -> dict[str, str]:
    raw = json.loads((client_dir / "knowledge_base" / "kb_index.json").read_text())
    return {e["id"]: e["status"] for e in raw}


def test_reject_keeps_the_entry_and_removes_it_from_provenance(tmp_path):
    client_dir = _client(tmp_path)
    kb_verify.approve(client_dir, ["kb-0", "kb-1"], "reviewer")
    kb_verify.reject(client_dir, ["kb-1", "kb-2"], "reviewer")

    assert _statuses(client_dir) == {"kb-0": "verified", "kb-1": "rejected", "kb-2": "rejected"}
    provenance = json.loads((client_dir / "provenance.json").read_text())
    assert [p["claim"] for p in provenance] == ["Fact 0."]
    assert verify_client(client_dir) == (True, [])


def test_init_client_validates_id_and_set_identity_fills_yaml(tmp_path):
    with pytest.raises(ValueError):
        client_setup.init_client("Bad Name", tmp_path)

    target = client_setup.init_client("acme", tmp_path)
    client_setup.set_identity(target, "Acme Ltd", "Widgets", "https://acme.example")
    data = yaml.safe_load((target / "client.yaml").read_text())
    assert (data["client_id"], data["company_name"], data["industry"]) == ("acme", "Acme Ltd", "Widgets")
    with pytest.raises(FileExistsError):
        client_setup.init_client("acme", tmp_path)


def test_numeric_looking_client_id_stays_a_string(tmp_path):
    from pipeline.run_job import _load_client_profile

    target = client_setup.init_client("001", tmp_path)
    client_setup.set_identity(target, "Numbered Co", "Retreats")
    assert yaml.safe_load((target / "client.yaml").read_text())["client_id"] == "001"
    assert _load_client_profile(target).client_id == "001"


def test_compile_queues_only_claims_and_never_stores_internal_or_personal(tmp_path):
    from pipeline.llm.transport import LLMResult
    from pipeline.stages import kb_compile

    client_dir = _client(tmp_path)
    (client_dir / "knowledge_base" / "documents").mkdir()
    (client_dir / "knowledge_base" / "documents" / "notes.txt").write_text("source text")
    reply = [
        {"claim": "The retreat runs ten days.", "kind": "claim"},
        {"claim": "Say 'breath work', not 'breathing'.", "kind": "style"},
        {"claim": "A competitor has 600+ clients.", "kind": "reference"},
        {"claim": "Next step: send the keyword list.", "kind": "internal"},
        {"claim": "A named founder's blood pressure is high.", "kind": "personal"},
        {"claim": "Unlabelled statement.", "kind": "something-else"},
    ]

    survey = {"role": "strategy", "summary": "A growth plan.", "plan_items": [
        {"title": "5-MeO-DMT vs psilocybin retreats", "priority": 1, "primary_keyword": "5-MeO-DMT vs psilocybin retreat"},
        {"title": "", "priority": 2},  # no title: dropped
    ]}

    class _T:
        def call(self, system_prompt, user_prompt):
            if user_prompt.startswith("You are cataloguing"):
                return LLMResult(available=True, text=json.dumps(survey))
            return LLMResult(available=True, text=json.dumps(reply))

    entries, warnings = kb_compile.compile_kb(client_dir, transport=_T())
    new = {e.claim: (e.status, e.kind) for e in entries if e.source_doc == "notes.txt"}
    assert new == {
        "The retreat runs ten days.": ("pending", "claim"),
        "Say 'breath work', not 'breathing'.": ("excluded", "style"),
        "A competitor has 600+ clients.": ("excluded", "reference"),
        "Unlabelled statement.": ("pending", "claim"),  # unknown label goes to a human
    }
    assert any("1 internal, 1 personal" in w for w in warnings)

    # the document is kept readable, with its role, and its planned pieces are proposed
    from pipeline.kb import content_plan, context

    [doc] = context.load_docs(client_dir)
    assert (doc.name, doc.role, doc.summary) == ("notes.txt", "strategy", "A growth plan.")
    assert doc.sections[0].text == "source text"
    [item] = content_plan.load(client_dir)
    assert (item.title, item.status, item.source_doc) == ("5-MeO-DMT vs psilocybin retreats", "proposed", "notes.txt")

    # a second compile re-proposes nothing
    kb_compile.compile_kb(client_dir, transport=_T())
    assert len(content_plan.load(client_dir)) == 1


def test_compile_reads_a_long_document_in_page_chunks(tmp_path):
    from pipeline.schemas import IngestedDocument
    from pipeline.stages import kb_compile

    pages = [f"page {n} " + "x" * 5000 for n in range(1, 8)]
    doc = IngestedDocument(doc_id="d", source_path="s.pdf", format="pdf", extraction_method="text_layer",
                           raw_text="\n\n".join(pages), pages=pages)
    labels = [label for label, _ in kb_compile.chunks(doc)]
    assert labels == ["pages 1-2", "pages 3-4", "pages 5-6", "page 7"]
    assert "[page 7]" in kb_compile.chunks(doc)[-1][1]


def test_exclude_restore_and_purge(tmp_path):
    client_dir = _client(tmp_path)
    kb_verify.approve(client_dir, ["kb-0"], "reviewer")

    changed = kb_verify.exclude(client_dir, {"kb-0": ("internal", "x"), "kb-1": ("reference", "y")})
    assert changed == ["kb-1"]  # kb-0 was already decided by a human
    assert _statuses(client_dir)["kb-1"] == "excluded"

    kb_verify.restore(client_dir, ["kb-1"])
    raw = {e["id"]: e for e in json.loads((client_dir / "knowledge_base" / "kb_index.json").read_text())}
    assert (raw["kb-1"]["status"], raw["kb-1"]["kind"], raw["kb-1"]["exclusion_reason"]) == ("pending", "claim", None)

    assert kb_verify.purge(client_dir, ["kb-0", "kb-2"]) == 2
    assert set(_statuses(client_dir)) == {"kb-1"}
    assert json.loads((client_dir / "provenance.json").read_text()) == []
