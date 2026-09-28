"""Proves the two things kb_compile.py can't do: a human asserting a fact directly (optionally
straight to verified, since they ARE the source), and superseding an old fact so entailment.py
stops treating it as usable once a newer confirmation replaces it."""
from __future__ import annotations

import json

import pytest

from pipeline.stages import kb_add
from pipeline.stages.kb_compile import _save_index
from pipeline.schemas import KBEntry


def _client_dir(tmp_path):
    d = tmp_path / "acme"
    (d / "knowledge_base").mkdir(parents=True)
    return d


def test_add_claim_defaults_to_pending(tmp_path):
    client_dir = _client_dir(tmp_path)
    entry = kb_add.add_claim(client_dir, "The office moved to Austin.", "confirmed by Jamie on a call", "cli-user")
    assert entry.status == "pending"
    assert entry.verified_at is None

    index_path = client_dir / "knowledge_base" / "kb_index.json"
    saved = json.loads(index_path.read_text())
    assert len(saved) == 1
    assert saved[0]["claim"] == "The office moved to Austin."


def test_add_claim_verified_stamps_provenance(tmp_path):
    client_dir = _client_dir(tmp_path)
    entry = kb_add.add_claim(
        client_dir, "The price is now $650.", "client confirmed by email", "pranav", verified=True
    )
    assert entry.status == "verified"
    assert entry.verified_at is not None
    assert entry.confirmed_by == "pranav"

    provenance = json.loads((client_dir / "provenance.json").read_text())
    assert len(provenance) == 1
    assert provenance[0]["claim"] == "The price is now $650."
    assert provenance[0]["confirmed"] is True


def test_supersedes_marks_old_entry_stale(tmp_path):
    client_dir = _client_dir(tmp_path)
    old = KBEntry(id="kb-old1", claim="The price is $100.", source_doc="july_brief.pdf", status="pending")
    _save_index(client_dir, [old])

    new = kb_add.add_claim(
        client_dir, "The price is $650.", "client confirmed", "pranav", verified=True, supersedes="kb-old1"
    )

    saved = {e["id"]: e for e in json.loads((client_dir / "knowledge_base" / "kb_index.json").read_text())}
    assert saved["kb-old1"]["status"] == "stale"
    assert saved["kb-old1"]["superseded_by"] == new.id
    assert saved[new.id]["status"] == "verified"


def test_supersedes_unknown_id_raises(tmp_path):
    client_dir = _client_dir(tmp_path)
    with pytest.raises(ValueError):
        kb_add.add_claim(client_dir, "Some fact.", "some source", "cli-user", supersedes="kb-doesnotexist")
