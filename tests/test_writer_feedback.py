"""The writer gets what it is judged on, and a revision edits the failed draft with every gate's
findings: drives full jobs on a copy of the exemplar client with a scripted transport whose judges
fail on cue, and inspects the prompts the writer actually received."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline import run_job
from pipeline.kb import content_plan, context
from pipeline.ledger import lessons
from pipeline.llm.transport import LLMResult
from pipeline.schemas import Brief, ContextDoc, ContextSection, WordRange
from pipeline.stages import revise, tone_select

REPO_CLIENTS = Path(__file__).resolve().parent.parent / "clients"
FIRST = " ".join(["The TI-4200 probe repeats to spec on every shift, and it is the best in the industry."] * 6)
FIXED = " ".join(["The TI-4200 probe repeats to plus or minus 0.02 millimeters on every shift."] * 6)


class Transport:
    """Scripted replies by prompt template. `judges` maps a judge's prompt opening to the list of
    replies it gives on successive calls (the last one repeats)."""

    def __init__(self, judges: dict[str, list[dict]] | None = None, conflicts: list | None = None) -> None:
        self.prompts: list[str] = []
        self.judges = judges or {}
        self.calls: dict[str, int] = {}
        self.conflicts = conflicts or []

    def _judge(self, key: str, default: dict) -> dict:
        replies = self.judges.get(key)
        if not replies:
            return default
        n = self.calls.get(key, 0)
        self.calls[key] = n + 1
        return replies[min(n, len(replies) - 1)]

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.prompts.append(user_prompt)
        p = user_prompt
        if p.startswith("You are checking a content brief"):
            reply = {"conflicts": self.conflicts}
        elif p.startswith("You are proposing content angles"):
            reply = [{"headline": "Repeatability first", "pitch": "Lead with the spec",
                      "structure": ["spec", "lead time"], "claims_used": ["kb-demo0001"]}]
        elif p.startswith("Generate short-copy candidates"):
            reply = [{"text": "Probe data you can repeat", "strategy": "curiosity-led"}]
        elif p.startswith("You are compiling a working spec"):
            reply = {"outline": ["spec"], "claims_to_use": ["kb-demo0001"]}
        elif p.startswith("Write the full draft"):
            reply = {"body": FIRST, "claims_used": ["kb-demo0001"]}
        elif p.startswith("You are revising a draft"):
            reply = {"body": FIXED, "claims_used": ["kb-demo0001"], "change_notes": ["removed the superlative"]}
        elif p.startswith("You are an independent editorial critic"):
            reply = self._judge("voice", {"verdict": "on_voice", "notes": []})
        elif p.startswith("You are an independent compliance reviewer"):
            reply = {"verdict": "clean", "notes": []}
        elif p.startswith("You are an independent fact checker"):
            reply = self._judge("claims", {"verdict": "supported", "unsupported": []})
        else:
            reply = {}
        return LLMResult(available=True, text=json.dumps(reply))

    def saw(self, start: str) -> list[str]:
        return [p for p in self.prompts if p.startswith(start)]


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    shutil.copytree(REPO_CLIENTS / "exemplar", clients / "exemplar")
    return clients, tmp_path / "output"


def _brief(**extra) -> Brief:
    fields = {"client_id": "exemplar", "goal": "Introduce the TI-4200", "audience": "quality engineers",
              "format": "email", **extra}
    return Brief(**fields)


def _job(roots, transport, brief=None, ack=None, budget=2):
    clients, output = roots
    session = run_job.start("exemplar", brief or _brief(), clients, output, transport=transport, preflight_ack=ack)
    tone = tone_select.custom_tone("Dry, exact, engineer-to-engineer", "Bench Notes")
    run_job.build_microcopy_menu(session.job_id, 0, tone, clients, output, transport=transport)
    return session.job_id, run_job.execute(session.job_id, {}, clients, output, transport=transport, retry_budget=budget)


def test_writer_sees_the_rules_and_the_fact_text_before_drafting(roots):
    transport = Transport()
    _job(roots, transport, brief=_brief(word_range=WordRange(min=50, max=150)))

    prompt = transport.saw("Write the full draft")[0]
    assert "Lead with the specific number or standard" in prompt  # style_guide.md
    assert "the best in the industry" in prompt  # constraints.yaml do_not_say
    assert "delve" in prompt and "em dash" in prompt  # house list
    assert "50-150 words" in prompt
    assert "plus or minus 0.02 millimeters" in prompt  # the planned fact's TEXT, not only its id
    assert "[kb-demo0002]" in prompt  # other verified facts are offered too


def test_revision_edits_the_failed_draft_with_every_failing_gate(roots):
    unsupported = {"verdict": "unsupported", "unsupported": [{"quote": "every shift", "why": "no fact says this"}]}
    off_voice = {"verdict": "off_voice", "notes": ["stacked 'not X but Y' sentences"]}
    transport = Transport(judges={"claims": [unsupported, {"verdict": "supported", "unsupported": []}],
                                  "voice": [off_voice, {"verdict": "on_voice", "notes": []}]})
    job_id, output_dir = _job(roots, transport)

    [revision_prompt] = transport.saw("You are revising a draft")
    assert FIRST in revision_prompt  # the previous draft itself
    # one round names every failing gate: style_lint (do-not-say hit), claims_critic and voice_critic
    for expected in ("[client_constraints]", "the best in the industry", "[claims_critic]", "every shift",
                     "[voice_critic]", "not X but Y"):
        assert expected in revision_prompt
    assert "Lead with the specific number" in revision_prompt  # rules travel with the revision too

    record = json.loads((output_dir / "job_record.json").read_text())
    assert record["status"] == "complete"
    assert [r["revision"] for r in record["rounds"]] == [0, 1]
    assert record["rounds"][1]["change_notes"] == ["removed the superlative"]
    assert (output_dir / "drafts" / "rev-0.json").exists() and (output_dir / "drafts" / "rev-1.json").exists()
    assert json.loads((output_dir / "package.json").read_text())["draft"]["body"] == FIXED


def test_later_rounds_are_told_not_to_reintroduce_earlier_findings(roots):
    off = {"verdict": "off_voice", "notes": ["teaser transitions"]}
    transport = Transport(judges={"voice": [off, {"verdict": "off_voice", "notes": ["throat-clearing"]},
                                            {"verdict": "on_voice", "notes": []}]})
    _job(roots, transport, budget=3)

    second, third = transport.saw("You are revising a draft")
    assert "do not reintroduce" not in second
    assert "do not reintroduce" in third and "teaser transitions" in third


def test_escalation_keeps_every_round_and_logs_judge_notes(roots):
    clients, output = roots
    off = {"verdict": "off_voice", "notes": ["reads as generic wellness copy"]}
    transport = Transport(judges={"voice": [off]})

    with pytest.raises(revise.RetryBudgetExceeded) as exc:
        _job(roots, transport)
    assert exc.value.gate_name in {"voice_critic", "client_constraints"}

    [job_dir] = (output / "jobs").iterdir()
    record = json.loads((job_dir / "job_record.json").read_text())
    assert record["status"] == "awaiting_human"
    assert len(record["rounds"]) == 2
    assert (job_dir / "last_failed_draft.json").exists()
    notes = lessons.load(output, "exemplar")
    assert {n["note"] for n in notes} == {"reads as generic wellness copy"}


def test_finish_by_hand_packages_a_clean_edit_and_refuses_a_dirty_one(roots):
    clients, output = roots
    transport = Transport(judges={"voice": [{"verdict": "off_voice", "notes": ["flat"]}]})
    with pytest.raises(revise.RetryBudgetExceeded):
        _job(roots, transport)
    [job_dir] = (output / "jobs").iterdir()

    ok, results = run_job.finish_by_hand(job_dir.name, FIRST, clients, output, "editor")
    assert not ok
    assert any("the best in the industry" in i for g in results for i in g.flagged_items)

    ok, results = run_job.finish_by_hand(job_dir.name, FIXED, clients, output, "editor")
    assert ok
    record = json.loads((job_dir / "job_record.json").read_text())
    assert (record["status"], record["human_edited"]) == ("complete", True)
    package = json.loads((job_dir / "package.json").read_text())
    assert package["human_edited"] is True and package["draft"]["body"] == FIXED


def test_preflight_blocks_a_mismatched_brief_before_any_job_exists(roots):
    clients, output = roots
    brief = _brief(goal="Write the first two blog posts from the strategy", word_range=WordRange(min=60, max=90))
    transport = Transport()

    with pytest.raises(run_job.PreflightIssues) as exc:
        run_job.start("exemplar", brief, clients, output, transport=transport)
    assert any("blog post" in i and "email" in i for i in exc.value.issues)
    assert any("far below" in i for i in exc.value.issues)
    assert not (output / "jobs").exists()
    assert not transport.saw("You are proposing content angles")


def test_proceeding_past_a_rule_conflict_records_it_and_tells_the_writer(roots):
    conflict = {"brief_says": "skip the lead time", "rule": "always state lead time", "resolution": "state it once"}
    transport = Transport(conflicts=[conflict])
    clients, output = roots
    brief = _brief(notes="skip the lead time")

    with pytest.raises(run_job.PreflightIssues) as exc:
        run_job.start("exemplar", brief, clients, output, transport=transport)
    job_id, output_dir = _job(roots, transport, brief=brief, ack=exc.value.issues)

    record = json.loads((output_dir / "job_record.json").read_text())
    assert record["preflight_overridden"] == exc.value.issues
    assert "known_conflicts" in transport.saw("Write the full draft")[0]
    assert "always state lead time" in transport.saw("Write the full draft")[0]


def test_a_plan_item_steers_angles_and_draft_and_is_marked_drafted(roots):
    clients, output = roots
    transport = Transport()
    job_id, _ = _job(roots, transport, brief=_brief(plan_item_id="plan-ex0001"))

    for start in ("You are proposing content angles", "You are compiling a working spec", "Write the full draft"):
        assert "probe repeatability spec" in transport.saw(start)[0]
    item = content_plan.get(clients / "exemplar", "plan-ex0001")
    assert (item.status, item.job_ids) == ("drafted", [job_id])


def test_an_unapproved_plan_item_blocks_at_intake(roots):
    clients, output = roots
    with pytest.raises(run_job.JobBlocked, match="not approved"):
        run_job.start("exemplar", _brief(plan_item_id="plan-ex0003"), clients, output, transport=Transport())


def test_relevant_client_context_reaches_the_writer_as_direction(roots):
    clients, output = roots
    context.save_doc(clients / "exemplar", ContextDoc(
        name="strategy.pdf", role="strategy", summary="Growth plan for Contoso.",
        sections=[ContextSection(heading="p1 Pricing", text="Discount tiers for distributors."),
                  ContextSection(heading="p2 Audience", text="Quality engineers want repeatability evidence.")],
    ))
    transport = Transport()
    _job(roots, transport)

    prompt = transport.saw("Write the full draft")[0]
    assert "CLIENT CONTEXT" in prompt and "Never state anything from them as fact" in prompt
    assert "Quality engineers want repeatability evidence." in prompt
    assert "Growth plan for Contoso." in prompt
