"""Profile review: statuses step down when content changes behind them, Final locks edits, the
DRAFT markers follow the statuses, brief defaults pre-fill a brief, and a non-final profile is
noted on the job."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from pipeline import brief_defaults, profile_review

REPO_CLIENTS = Path(__file__).resolve().parent.parent / "clients"


@pytest.fixture
def client(tmp_path: Path) -> Path:
    shutil.copytree(REPO_CLIENTS / "exemplar", tmp_path / "exemplar")
    return tmp_path / "exemplar"


def test_new_profile_is_all_draft(client):
    assert {v["status"] for v in profile_review.status(client).values()} == {"draft"}
    assert len(profile_review.not_final(client)) == len(profile_review.SECTIONS)


def test_final_locks_and_reopen_unlocks(client):
    profile_review.set_status(client, "framing_rules", "final", "Ana")
    with pytest.raises(ValueError, match="reopen"):
        profile_review.save_section(client, "framing_rules", ["new rule"])
    profile_review.set_status(client, "framing_rules", "reviewed", "Ana")
    profile_review.save_section(client, "framing_rules", ["Do not promise results."])
    assert profile_review.section_content(client, "framing_rules") == ["Do not promise results."]
    assert profile_review.status(client)["framing_rules"]["status"] == "reviewed"  # an in-app edit keeps it


def test_an_edit_outside_the_app_steps_the_status_down(client):
    profile_review.set_status(client, "tone_presets", "final", "Ana")
    path = client / "tone_presets.yaml"
    data = yaml.safe_load(path.read_text())
    data["presets"][0]["name"] = "Renamed"
    path.write_text(yaml.safe_dump(data))
    info = profile_review.status(client)["tone_presets"]
    assert info["status"] == "reviewed" and "final" in info["changed"]


def test_draft_markers_follow_the_status(client):
    profile_review.save_section(client, "style_guide", "Plain and exact.")
    assert "DRAFT" in (client / "style_guide.md").read_text().splitlines()[0]
    profile_review.set_status(client, "style_guide", "final", "Ana")
    assert (client / "style_guide.md").read_text().splitlines()[0] == "# Style guide"
    profile_review.set_status(client, "banned_words", "final", "Ana")
    assert (client / "constraints.yaml").read_text().startswith("# DRAFT")  # framing rules still draft
    profile_review.set_status(client, "framing_rules", "final", "Ana")
    assert not (client / "constraints.yaml").read_text().startswith("# DRAFT")
    assert profile_review.status(client)["banned_words"]["status"] == "final"  # header edits aren't content


def test_banned_words_keep_the_other_client_settings(client):
    profile_review.save_section(client, "banned_words", {"do_not_say": ["Acme"], "extra_banned_words": ["synergy"],
                                                         "extra_banned_phrases": []})
    client_yaml = yaml.safe_load((client / "client.yaml").read_text())
    assert client_yaml["extra_banned_words"] == ["synergy"] and client_yaml["company_name"]
    assert yaml.safe_load((client / "constraints.yaml").read_text())["do_not_say"] == ["Acme"]


def test_flags_are_read_from_the_style_guide_and_resolvable(client):
    profile_review.save_section(client, "style_guide", "Plain. Note: the deck conflicts with the site; "
                                "the human reviewer should resolve this.")
    flags = profile_review.flags(client)
    assert len(flags) == 1 and "reviewer should resolve" in flags[0]["text"]
    profile_review.resolve_flag(client, 0, "Ana")
    assert profile_review.flags(client)[0]["resolved_by"] == "Ana"


def test_brief_defaults_prefill_type_first_then_client_wide(client, tmp_path):
    profile_review.save_brief_defaults(client, {"audience": "Partners at boutique firms", "must_follow": "No P.S.",
                                                "content_types": {"email": {"must_follow": "Under 120 words."}}})
    d = brief_defaults.defaults_for(tmp_path / "briefs", "exemplar", "email", client)
    assert d["audience"] == "Partners at boutique firms" and d["notes"] == "Under 120 words."
    d = brief_defaults.defaults_for(tmp_path / "briefs", "exemplar", "blog_post", client)
    assert d["notes"] == "No P.S."


def test_custom_content_type_is_remembered(client, tmp_path):
    key = profile_review.add_content_type(client, "Partner follow-up", "a threaded reply to an unanswered email", 110)
    assert key == "partner_follow_up"
    assert key in brief_defaults.known_formats(tmp_path / "briefs", "exemplar", client)
    d = brief_defaults.defaults_for(tmp_path / "briefs", "exemplar", key, client)
    assert d["target_word_count"] == 110 and "threaded reply" in d["description"]
