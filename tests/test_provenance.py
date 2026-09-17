"""Proves the provenance gate actually refuses on an unconfirmed/unsourced fact -- same intent as
GTM's test_provenance_gate.py."""
from __future__ import annotations

import json

import pytest

from pipeline.kb.provenance import ProvenanceError, ensure_verified, verify_client


def _write_provenance(tmp_path, entries):
    client_dir = tmp_path / "acme"
    client_dir.mkdir()
    (client_dir / "provenance.json").write_text(json.dumps(entries), encoding="utf-8")
    return client_dir


def test_missing_provenance_file_fails(tmp_path):
    client_dir = tmp_path / "acme"
    client_dir.mkdir()
    ok, problems = verify_client(client_dir)
    assert ok is False
    assert any("no provenance.json" in p for p in problems)


def test_fully_confirmed_entry_passes(tmp_path):
    client_dir = _write_provenance(
        tmp_path,
        [{"claim": "x", "type": "fact", "source": "doc.txt", "confirmed": True, "confirmed_by": "alice", "date": "2026-09-01"}],
    )
    ok, problems = verify_client(client_dir)
    assert ok is True
    assert problems == []


def test_unconfirmed_entry_fails(tmp_path):
    client_dir = _write_provenance(
        tmp_path, [{"claim": "x", "type": "fact", "source": "doc.txt", "confirmed": False}]
    )
    ok, problems = verify_client(client_dir)
    assert ok is False
    assert any("not confirmed" in p for p in problems)


def test_confirmed_but_missing_confirmed_by_fails(tmp_path):
    client_dir = _write_provenance(
        tmp_path,
        [{"claim": "x", "type": "fact", "source": "doc.txt", "confirmed": True, "date": "2026-09-01"}],
    )
    ok, problems = verify_client(client_dir)
    assert ok is False
    assert any("missing confirmed_by" in p for p in problems)


def test_missing_source_fails_even_if_confirmed(tmp_path):
    client_dir = _write_provenance(
        tmp_path,
        [{"claim": "x", "type": "fact", "confirmed": True, "confirmed_by": "alice", "date": "2026-09-01"}],
    )
    ok, problems = verify_client(client_dir)
    assert ok is False
    assert any("missing source" in p for p in problems)


def test_ensure_verified_raises_with_all_problems(tmp_path):
    client_dir = _write_provenance(tmp_path, [{"claim": "x", "confirmed": False}, {"claim": "y", "confirmed": False}])
    with pytest.raises(ProvenanceError) as exc_info:
        ensure_verified(client_dir)
    assert len(exc_info.value.problems) >= 2
