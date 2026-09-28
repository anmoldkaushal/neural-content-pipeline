"""Proves approved-example memory is scoped per format, returns newest-first, and produces no
prompt text at all until something has actually been approved -- a client's first job must read
identically to how it did before this feature existed."""
from __future__ import annotations

import time
from pathlib import Path

from pipeline.kb import approved_examples


def _client_dir(tmp_path):
    return tmp_path / "acme"


def test_no_examples_yet_is_silent():
    assert approved_examples.load_examples(Path("/nonexistent"), "email") == []
    assert approved_examples.format_example_block([]) == ""


def test_record_and_load_round_trip(tmp_path):
    client_dir = _client_dir(tmp_path)
    package = {"draft": {"body": "Approved email body."}}
    approved_examples.record_approval(client_dir, "job1", "email", package, "pranav", "2026-09-18")

    examples = approved_examples.load_examples(client_dir, "email")
    assert examples == ["Approved email body."]


def test_scoped_per_format(tmp_path):
    client_dir = _client_dir(tmp_path)
    approved_examples.record_approval(
        client_dir, "job1", "email", {"draft": {"body": "An email."}}, "p", "2026-09-18"
    )
    approved_examples.record_approval(
        client_dir, "job2", "linkedin_message", {"draft": {"body": "A linkedin message."}}, "p", "2026-09-18"
    )

    assert approved_examples.load_examples(client_dir, "email") == ["An email."]
    assert approved_examples.load_examples(client_dir, "linkedin_message") == ["A linkedin message."]


def test_limit_and_newest_first(tmp_path):
    client_dir = _client_dir(tmp_path)
    for i in range(4):
        approved_examples.record_approval(
            client_dir, f"job{i}", "email", {"draft": {"body": f"Body {i}."}}, "p", "2026-09-18"
        )
        time.sleep(0.01)

    examples = approved_examples.load_examples(client_dir, "email", limit=2)
    assert examples == ["Body 3.", "Body 2."]


def test_format_example_block_mentions_voice_matching():
    block = approved_examples.format_example_block(["Example one."])
    assert "Example one." in block
    assert "voice" in block.lower()
