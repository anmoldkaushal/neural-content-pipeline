"""A sequence ready to send: plan items fix each email's topic and order, every email has its own
subject line and preheader, judges see the earlier emails and judge each email against its own job,
sequence_lint stops an email reusing an earlier one's opening, call to action or phrasing, and the
package carries the sign-off and a CSV export. The repeated lines below paraphrase a real drip that
passed every per-email gate and still read as one email sent five times."""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from pipeline import run_job
from pipeline.gates import sequence_lint, style_lint
from pipeline.kb import content_plan, rulebook
from pipeline.llm.transport import LLMResult
from pipeline.schemas import Brief, ClientProfile, Draft, PlanItem
from pipeline.stages import package, revise, tone_select
from tests.test_interactive_job import ScriptedTransport

REPO_CLIENTS = Path(__file__).resolve().parent.parent / "clients"


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    shutil.copytree(REPO_CLIENTS / "exemplar", clients / "exemplar")
    return clients, tmp_path / "output"


def _email(piece: int, body: str, subject: str = "") -> Draft:
    return Draft(job_id="j", body=body, word_count=style_lint.count_words(body), piece=piece,
                 subject_line=subject or None)


ONE = ("Hi {{first_name}},\n\nYou have built something real, and it has taken all of you.\n\n"
       "In that room nobody is there to be sold to, and nobody performs.\n\n"
       "If you would like to know more, you are warmly welcome to visit https://example.com/ to learn more.")


# --------------------------------------------------------------- sequence_lint


def test_sequence_lint_quotes_a_repeated_opening_cta_and_phrase():
    two = ("Hi {{first_name}},\n\nYou have built something real, and the next step is yours.\n\n"
           "A curated table, and nobody is there to be sold to.\n\n"
           "If you would like to know more, you are warmly welcome to visit https://example.com/ to learn more.")
    issues = sequence_lint.find_issues(_email(2, two), [_email(1, ONE)])
    assert any(i.startswith("the opening repeats email 1's opening") and "You have built something real" in i
               for i in issues)
    assert any(i.startswith("the call to action repeats email 1's wording") for i in issues)
    assert any("nobody is there to be sold to" in i for i in issues)


def test_sequence_lint_passes_a_distinct_email():
    two = ("Hi {{first_name}},\n\nThe week rarely leaves an hour to think past the next quarter.\n\n"
           "Three days with founders at your stage gives that hour back.\n\n"
           "The details sit at https://example.com/ whenever you want them.")
    assert sequence_lint.run(_email(2, two), [_email(1, ONE)]).status.value == "passed"


def test_sequence_lint_checks_anchor_words_and_subjects():
    two = _email(2, "Hi there,\n\nHeadroom is rare. Headroom matters. Reset later.", subject="The room")
    issues = sequence_lint.find_issues(two, [_email(1, ONE, subject="The room!")], anchor="headroom",
                                       other_anchors=["reset"])
    assert "anchor word 'headroom' must appear exactly once in this email; it appears 2 time(s)" in issues
    assert "'reset' is another email's anchor word; leave it to that email" in issues
    assert any(i.startswith("the subject line is the same as email 1's") for i in issues)


def test_sentences_skip_the_greeting_and_count_a_closing_link():
    body = "Hi {{first_name}},\n\nOne. Two? Three!\n\nFour, at https://www.example.in/ to learn more"
    assert style_lint.body_sentences(body) == ["One.", "Two?", "Three!", "Four, at https://www.example.in/ to learn more"]
    profile = ClientProfile(client_id="c", company_name="C", industry="i")
    result = style_lint.run(_email(1, body), profile, sentence_max=3)
    assert "too many sentences: 4, at most 3 (merge or cut 1)" in result.flagged_items


def test_the_writer_is_shown_the_sequence_rules():
    profile = ClientProfile(client_id="c", company_name="C", industry="i")
    rules = rulebook.render(profile, sentence_max=5, sequence=True)
    assert "At most 5 sentences" in rules and "Same link, different invitation" in rules
    assert "Across the sequence" not in rulebook.render(profile)


# --------------------------------------------------------------- a sequence through the pipeline


def _plan(clients: Path, anchors: tuple = (None, None, None)) -> list[str]:
    items = [PlanItem(id=f"plan-e{n}", title=f"Email {n} topic", format="email", priority=n, notes=f"covers part {n}",
                      anchor=anchors[n - 1], status="approved") for n in (1, 2, 3)]
    content_plan.save(clients / "exemplar", items)
    return [i.id for i in items]


def _start(roots, transport, **brief):
    clients, output = roots
    b = Brief(client_id="exemplar", goal="Introduce the TI-4200", audience="QA leads", format="email_sequence",
              **brief)
    session = run_job.start("exemplar", b, clients, output, transport=transport)
    run_job.choose_angle(session.job_id, 0, tone_select.custom_tone("Dry, exact"), output)
    return session


def test_plan_items_fix_the_emails_and_reach_every_prompt(roots):
    clients, output = roots
    transport = ScriptedTransport()
    ids = _plan(clients)
    session = _start(roots, transport, plan_item_ids=ids, sequence_length=5)
    assert session.brief.sequence_length == 3  # one email per plan item
    assert "Email 3 topic" in transport.saw("You are checking a content brief")[0]  # preflight checks each email
    assert "fixes the emails of this sequence" in transport.saw("You are proposing content angles")[0]
    # the scripted angle has two steps for three emails: the plan's steps replace them
    assert session.angles[0].structure == ["Email 1 topic: covers part 1", "Email 2 topic: covers part 2",
                                           "Email 3 topic: covers part 3"]
    run_job.execute(session.job_id, {}, clients, output, transport=transport)
    third = transport.saw("Write the full draft")[2]
    assert "Content plan item: Email 3 topic" in third
    assert all(i.status == "drafted" for i in content_plan.load(clients / "exemplar"))


def test_a_plan_item_that_is_not_approved_blocks_the_sequence(roots):
    clients, _ = roots
    ids = _plan(clients)
    content_plan.set_status(clients / "exemplar", [ids[1]], "rejected")
    with pytest.raises(run_job.JobBlocked, match="plan-e2"):
        _start(roots, ScriptedTransport(), plan_item_ids=ids)


def test_synthesis_sizes_each_email_to_the_full_range(roots):
    from pipeline.schemas import WordRange
    clients, output = roots
    transport = ScriptedTransport()
    session = _start(roots, transport, sequence_length=2, word_range=WordRange(min=50, max=110), sentence_max=5)
    with pytest.raises(revise.RetryBudgetExceeded):  # the scripted email has 12 sentences
        run_job.execute(session.job_id, {}, clients, output, transport=transport)
    synthesis = transport.saw("You are compiling a working spec")[0]
    assert "50-110 words PER EMAIL" in synthesis and "at most 5 sentences per email" in synthesis
    assert "At most 5 sentences" in transport.saw("Write the full draft")[0]
    assert "too many sentences: 12, at most 5" in transport.saw("You are revising")[0]


def test_invented_steps_are_checked_against_the_rules_before_drafting(roots):
    clients, output = roots
    transport = ScriptedTransport()
    session = _start(roots, transport, sequence_length=2)
    run_job.execute(session.job_id, {}, clients, output, transport=transport)
    checks = transport.saw("You are checking a content brief")
    assert len(checks) == 2 and "Planned emails" in checks[1] and "Email 1: spec" in checks[1]


def test_judges_see_this_emails_job_and_the_earlier_emails(roots):
    clients, output = roots
    constraints = clients / "exemplar" / "constraints.yaml"
    constraints.write_text(constraints.read_text() + 'do_not_frame:\n  - "Do not promise results."\n')
    transport = ScriptedTransport()
    session = _start(roots, transport, sequence_length=2, notes="Never mention pricing.",
                     sequence_notes="Open the first email on the reader's turning point.")
    run_job.execute(session.job_id, {}, clients, output, transport=transport)
    voice = transport.saw("You are an independent editorial critic")
    assert "SEQUENCE: this draft is email 2 of 2" in voice[1] and "--- Email 1: Hi" not in voice[1]
    assert "--- Email 1: Email1 probe1" in voice[1] and "is blocking" in voice[1]
    assert "Across the whole sequence" in voice[1] and "turning point" in voice[1]
    assert "Brief must-follow: Never mention pricing." in voice[1] and "turning point" not in voice[1].split("SEQUENCE")[0]
    framing = transport.saw("You are an independent compliance reviewer")
    assert "SEQUENCE: this draft is email 2 of 2" in framing[1] and "is blocking" not in framing[1]
    assert "subject_line: Bench note shift" in voice[1]  # judged with its own subject line on top


class NoSubjectFirst(ScriptedTransport):
    """Writes email 1 without copy; the revision adds it."""

    def call(self, system_prompt, user_prompt):
        if user_prompt.startswith("You are revising"):
            self.prompts.append(user_prompt)
            return LLMResult(available=True, text=json.dumps({
                "body": __import__("tests.test_interactive_job", fromlist=["x"]).sequence_body(1),
                "claims_used": ["kb-demo0001"], "subject_line": "Bench note probe", "preheader": "What it shows"}))
        result = super().call(system_prompt, user_prompt)
        if user_prompt.startswith("Write the full draft") and "Write ONLY email 1 of" in user_prompt:
            reply = json.loads(result.text)
            reply.pop("subject_line"), reply.pop("preheader")
            return LLMResult(available=True, text=json.dumps(reply))
        return result


def test_missing_email_copy_goes_back_through_revise(roots):
    clients, output = roots
    transport = NoSubjectFirst()
    session = _start(roots, transport, sequence_length=2)
    output_dir = run_job.execute(session.job_id, {}, clients, output, transport=transport)
    revision = transport.saw("You are revising")[0]
    assert "[microcopy_lint]" in revision and "subject_line: missing" in revision
    first = json.loads((output_dir / "package.json").read_text())["sequence"][0]
    assert first["subject_line"] == "Bench note probe"


def test_the_package_carries_subjects_sign_off_and_a_csv(roots):
    clients, output = roots
    client_yaml = clients / "exemplar" / "client.yaml"
    client_yaml.write_text(client_yaml.read_text() + "sender:\n  name: '{{sender_name}}'\n  sign_off: Best,\n"
                           "utm: utm_source=email&utm_content=email-{n}\n")
    transport = ScriptedTransport()
    session = _start(roots, transport, sequence_length=2)
    output_dir = run_job.execute(session.job_id, {}, clients, output, transport=transport)
    pkg = json.loads((output_dir / "package.json").read_text())
    assert pkg["sign_off"] == "Best,\n{{sender_name}}"
    assert pkg["draft"]["body"].startswith("EMAIL 1 OF 2\n\nSubject: Bench note probe\n\nPreheader: What the probe shows")
    assert "Best,\n{{sender_name}}\n\nEMAIL 2 OF 2" in pkg["draft"]["body"]
    rows = list(csv.DictReader((output_dir / "sequence.csv").open()))
    assert [r["subject_line"] for r in rows] == ["Bench note probe", "Bench note shift"]
    assert rows[1]["body"].endswith("Best,\n{{sender_name}}")
    record = json.loads((output_dir / "job_record.json").read_text())
    assert any("the sign-off has placeholders" in n for n in record["human_touchpoints"])


def test_utm_goes_on_the_link_in_the_export_only(tmp_path):
    profile = ClientProfile(client_id="c", company_name="C", industry="i", website="https://example.com/",
                            utm="utm_content=email-{n}")
    path = package.write_sequence_csv(tmp_path, [_email(2, "Visit https://example.com/ today.")], profile)
    assert "https://example.com/?utm_content=email-2" in path.read_text()


def test_one_emails_copy_can_be_swapped_after_drafting(roots):
    clients, output = roots
    transport = ScriptedTransport()
    session = _start(roots, transport, sequence_length=2)
    output_dir = run_job.execute(session.job_id, {}, clients, output, transport=transport)
    menu = run_job.build_microcopy_menu_after(session.job_id, clients, output, transport=transport, piece=2)
    assert set(menu.microcopy_menu) == {"subject_line", "preheader"}
    assert "The other emails' subject lines (yours must differ): Bench note probe" in transport.saw("Generate short-copy")[0]

    clash = run_job.attach_microcopy(session.job_id, {"subject_line": "Bench note: probe"}, clients, output,
                                     transport=transport, piece=2)
    assert any(g.gate_name == "sequence_lint (email 2)" and g.status.value == "failed" for g in clash)
    results = run_job.attach_microcopy(session.job_id, {"subject_line": "Drift you can see"}, clients, output,
                                       transport=transport, piece=2)
    assert all(g.status.value != "failed" for g in results)
    pkg = json.loads((output_dir / "package.json").read_text())
    assert pkg["sequence"][1]["subject_line"] == "Drift you can see" and "Subject: Drift you can see" in pkg["draft"]["body"]
    assert "Drift you can see" in (output_dir / "sequence.csv").read_text()
