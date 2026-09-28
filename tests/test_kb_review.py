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
