"""Unit tests for the pieces under the writer: text repair, the word-range lint, context
selection, plan ordering, and adopting a learned rule."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from pipeline.gates import style_lint
from pipeline.ingest.normalize import normalize
from pipeline.kb import content_plan, context, rulebook
from pipeline.ledger import lessons
from pipeline.llm.transport import LLMResult
from pipeline.schemas import (
    Brief,
    ClientProfile,
    ContextDoc,
    ContextSection,
    Draft,
    GateResult,
    GateStatus,
    RevisionRound,
    WordRange,
)
from pipeline.stages import preflight


def test_normalize_joins_letter_spaced_headings_and_leaves_prose_alone():
    text = ("T H E  S I T U A T I O N\nYou have a world-class product.\nP R I O R I T Y  3  —  S E O  B L O G\n"
            "Plan A is a b-side  of the deck")
    assert normalize(text).splitlines() == [
        "THE SITUATION", "You have a world-class product.", "PRIORITY 3 — SEO BLOG", "Plan A is a b-side  of the deck",
    ]


def test_normalize_rejoins_one_word_per_line_exports():
    words = "\n \n".join(f"w{n}" for n in range(30))
    text = f"#1\n \n{words}\n \n \n \nNext\n \nparagraph."
    assert normalize(text) == "#1 " + " ".join(f"w{n}" for n in range(30)) + "\n\nNext paragraph."
    assert normalize("line one\nline two\n \nline three") == "line one\nline two\n \nline three"


def _draft(words: int) -> Draft:
    body = " ".join(["word"] * words)
    return Draft(job_id="j", body=body, word_count=words)


def test_style_lint_enforces_the_brief_range_with_tolerance():
    profile = ClientProfile(client_id="c", company_name="C", industry="i")
    rng = WordRange(min=1200, max=2000)
    assert style_lint.run(_draft(1100), profile, word_range=rng, tolerance=0.1).status == GateStatus.PASSED
    short = style_lint.run(_draft(1000), profile, word_range=rng, tolerance=0.1)
    assert short.status == GateStatus.FAILED and "add about 200" in short.flagged_items[0]
    long = style_lint.run(_draft(2300), profile, word_range=rng, tolerance=0.1)
    assert "cut about 300" in long.flagged_items[0]
    assert style_lint.run(_draft(60), profile).status == GateStatus.PASSED  # no range: house bounds only


def test_rulebook_carries_client_additions_and_framing_rules():
    profile = ClientProfile(client_id="c", company_name="C", industry="i", banned_words=["bespoke"],
                            do_not_frame=["Do not use urgency."], style_guide="Short sentences.")
    text = rulebook.render(profile, word_range=WordRange(min=10, max=20))
    for expected in ("Short sentences.", "bespoke", "delve", "- Do not use urgency.", "10-20 words"):
        assert expected in text


def test_context_select_ranks_relevant_sections_and_respects_the_budget(tmp_path: Path):
    context.save_doc(tmp_path, ContextDoc(name="strategy.pdf", role="strategy", summary="Plan.", sections=[
        ContextSection(heading="p1 Competitors", text="Tandava ranks first for most keywords."),
        ContextSection(heading="p2 SEO blog", text="Comparison keyword: 5-MeO-DMT vs psilocybin retreat."),
    ]))
    context.save_doc(tmp_path, ContextDoc(name="blogs.pdf", role="past_content", sections=[
        ContextSection(heading="#1", text="Success was supposed to fix this. " * 20),
    ]))

    block = context.select(tmp_path, "blog comparing psilocybin retreat options", budget_chars=2000)
    assert block.index("p2 SEO blog") < block.index("Plan.") + 10_000  # present
    assert "5-MeO-DMT vs psilocybin" in block
    assert len(block) <= 2000
    assert context.select(tmp_path, "anything", budget_chars=2000, roles={"brand"}) == ""
    assert "Success was supposed to fix this." in context.voice_reference(tmp_path)


def test_plan_merge_skips_known_titles_and_orders_ranked_first(tmp_path: Path):
    content_plan.merge_proposed(tmp_path, [{"title": "B", "priority": 2}, {"title": "Unranked"},
                                           {"title": "A", "priority": 1}], "s.pdf")
    assert content_plan.merge_proposed(tmp_path, [{"title": "a"}], "s.pdf") == []  # same title, any case
    items = content_plan.load(tmp_path)
    content_plan.set_status(tmp_path, [i.id for i in items], "approved")
    assert [i.title for i in content_plan.writable(tmp_path)] == ["A", "B", "Unranked"]
    assert (tmp_path / "content_plan.yaml").read_text().startswith("# Content plan")


def test_format_check_needs_a_format_word_in_the_goal():
    blog = Brief(client_id="c", goal="Introduce the probe", audience="a", format="email")
    assert preflight.format_issues(blog) == []
    mismatch = blog.model_copy(update={"goal": "Two articles on calibration"})
    assert "blog post" in preflight.format_issues(mismatch)[0]


def test_rule_conflict_check_reports_an_outage_instead_of_passing():
    class Down:
        def call(self, system_prompt, user_prompt):
            return LLMResult(available=False, error="offline")

    profile = ClientProfile(client_id="c", company_name="C", industry="i", do_not_frame=["Do not X."])
    brief = Brief(client_id="c", goal="g", audience="a", format="email", notes="do X")
    conflicts, skipped = preflight.rule_conflicts(brief, profile, transport=Down())
    assert conflicts == [] and "unavailable" in skipped


def test_lessons_need_two_jobs_and_adopted_rules_keep_file_headers(tmp_path: Path):
    failed = GateResult(gate_name="voice_critic", status=GateStatus.FAILED, detail="x", flagged_items=["teasers"])
    lessons.record(tmp_path / "out", "c", "job1", [RevisionRound(revision=0, word_count=1, gate_results=[failed])])
    profile = ClientProfile(client_id="c", company_name="C", industry="i")
    assert "at least 2" in lessons.suggest_rules(tmp_path / "out", profile)[1]

    client_dir = tmp_path / "c"
    client_dir.mkdir()
    (client_dir / "constraints.yaml").write_text("# DRAFT header\ndo_not_say: [x]\n")
    lessons.apply_suggestion(client_dir, "do_not_frame", "Do not use teaser transitions.")
    lessons.apply_suggestion(client_dir, "do_not_frame", "Do not use teaser transitions.")  # no duplicate
    text = (client_dir / "constraints.yaml").read_text()
    assert text.startswith("# DRAFT header")
    assert yaml.safe_load(text) == {"do_not_say": ["x"], "do_not_frame": ["Do not use teaser transitions."]}

    lessons.apply_suggestion(client_dir, "style_guide", "Vary sentence openings.")
    assert "## Learned from review\n- Vary sentence openings." in (client_dir / "style_guide.md").read_text()
