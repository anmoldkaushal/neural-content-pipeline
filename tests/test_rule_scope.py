"""Conditional must-follow rules reach only the pieces they're about (pipeline/stages/rule_scope.py),
and the check fails closed: no verdict, or an unclear one, keeps the rule."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from pipeline import run_job
from pipeline.llm.transport import LLMResult
from pipeline.schemas import Brief
from pipeline.stages import rule_scope
from tests.test_interactive_job import REPO_CLIENTS, ScriptedTransport

CTA = "The call to action is 'Apply for a discovery call'."
MEDICINE = "Medicine articles need a safety note, a risks section, a legal note and a newsletter call to action."
NOTES = f"{CTA}\n\n{MEDICINE}\nNever say 'bufo'."


class _Verdicts:
    def __init__(self, verdicts, ok=True):
        self.verdicts, self.ok, self.prompts = verdicts, ok, []

    def call(self, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        if not self.ok:
            return LLMResult(available=False, error="offline")
        return LLMResult(available=True, text=json.dumps({"rules": self.verdicts}))


def _brief(notes=NOTES):
    return Brief(client_id="c", goal="A guide to the Egypt journey", audience="seekers", format="blog_post",
                 notes=notes)


def test_a_conditional_rule_that_does_not_apply_is_left_out():
    t = _Verdicts([{"n": 1, "scope": "always", "applies": True},
                   {"n": 2, "scope": "conditional", "applies": False, "why": "a journey guide, not a medicine article"},
                   {"n": 3, "scope": "always", "applies": True}])
    notes, left_out, note = rule_scope.scope(_brief(), "Content plan item: Egypt journey guide", transport=t)
    assert notes == f"{CTA}\nNever say 'bufo'."
    assert left_out == [f"{MEDICINE} (a journey guide, not a medicine article)"]
    assert note is None
    assert "Content plan item: Egypt journey guide" in t.prompts[0] and f"2. {MEDICINE}" in t.prompts[0]


def test_a_conditional_rule_that_applies_is_kept():
    t = _Verdicts([{"n": 2, "scope": "conditional", "applies": True}])
    notes, left_out, _ = rule_scope.scope(_brief(), transport=t)
    assert rule_scope.lines(notes) == rule_scope.lines(NOTES) and left_out == []


def test_unclear_verdicts_keep_the_rule():
    t = _Verdicts([{"n": 2, "scope": "always", "applies": False},      # "always" is never left out
                   {"n": 3, "scope": "conditional", "applies": "no"},  # not a clear false
                   {"n": 9, "scope": "conditional", "applies": False}])  # no such rule
    notes, left_out, _ = rule_scope.scope(_brief(), transport=t)
    assert rule_scope.lines(notes) == rule_scope.lines(NOTES) and left_out == []


def test_an_unavailable_check_applies_every_rule_and_says_so():
    notes, left_out, note = rule_scope.scope(_brief(), transport=_Verdicts([], ok=False))
    assert notes == NOTES and left_out == [] and "every must-follow rule applied" in note


def test_no_notes_needs_no_call():
    t = _Verdicts([])
    assert rule_scope.scope(_brief(notes=None), transport=t) == (None, [], None) and t.prompts == []


class _JobTransport(ScriptedTransport):
    def call(self, system_prompt, user_prompt):
        if user_prompt.startswith("You are deciding which of a client's standing writing rules"):
            self.prompts.append(user_prompt)
            return LLMResult(available=True, text=json.dumps({"rules": [
                {"n": 1, "scope": "conditional", "applies": False, "why": "not a medicine article"}]}))
        return super().call(system_prompt, user_prompt)


def test_a_left_out_rule_never_reaches_the_angle_menu_and_is_recorded(tmp_path: Path):
    clients = tmp_path / "clients"
    shutil.copytree(REPO_CLIENTS / "exemplar", clients / "exemplar")
    transport = _JobTransport()
    brief = Brief(client_id="exemplar", goal="Introduce the TI-4200", audience="quality engineers", format="email",
                  notes=f"{MEDICINE}\nNever mention pricing.")
    session = run_job.start("exemplar", brief, clients, tmp_path / "output", transport=transport)

    assert session.brief.notes == "Never mention pricing."
    angle_prompt = transport.saw("You are proposing content angles")[0]
    assert "Never mention pricing." in angle_prompt and MEDICINE not in angle_prompt
    assert MEDICINE not in transport.saw("You are checking a content brief")[0]  # preflight, too
    record = json.loads((tmp_path / "output" / "jobs" / session.job_id / "job_record.json").read_text())
    assert any(MEDICINE in t and "not a medicine article" in t for t in record["human_touchpoints"])
