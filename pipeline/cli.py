"""CLI entrypoints: `python -m pipeline.cli <subcommand> ...`"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import click

from pipeline import client_setup, run_job
from pipeline.ledger import job_record as job_record_ledger
from pipeline.ledger import lessons
from pipeline.output import pdf_export
from pipeline.run_job import _load_client_profile
from pipeline.kb import approved_examples, content_plan
from pipeline.stages import kb_add as kb_add_stage
from pipeline.stages import kb_compile as kb_compile_stage
from pipeline.stages import kb_triage
from pipeline.stages import kb_verify as kb_verify_stage

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENTS_ROOT = REPO_ROOT / "clients"
OUTPUT_ROOT = REPO_ROOT / "output"


@click.group()
def cli() -> None:
    """neural-content-pipeline: brief + context + client + style -> gated draft."""


@cli.command("init-client")
@click.argument("client_id")
@click.option(
    "--from-docs",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Directory of source docs to draft a first-pass profile from.",
)
def init_client_cmd(client_id: str, from_docs: Optional[str]) -> None:
    try:
        target = client_setup.init_client(client_id, CLIENTS_ROOT, Path(from_docs) if from_docs else None)
    except (ValueError, FileExistsError) as exc:
        raise click.ClickException(str(exc))
    click.echo(f"Scaffolded client {client_id!r} at {target}")
    if from_docs:
        click.echo("Drafted a first-pass profile from the provided docs -- review before use (files marked DRAFT).")


@cli.command("kb-compile")
@click.option("--client", "client_id", required=True)
@click.option("--no-review", is_flag=True, help="Skip the agent review; every new fact waits for a person.")
def kb_compile_cmd(client_id: str, no_review: bool) -> None:
    client_dir = CLIENTS_ROOT / client_id
    entries, warnings = kb_compile_stage.compile_kb(client_dir)
    pending = [e for e in entries if e.status == "pending"]
    proposed = [i for i in content_plan.load(client_dir) if i.status == "proposed"]
    click.echo(f"Compiled {len(entries)} total KB entries ({len(pending)} pending review).")
    if proposed:
        click.echo(f"{len(proposed)} content plan item(s) proposed -- see `plan`, then `plan-approve`.")
    for w in warnings:
        click.echo(f"  warning: {w}")
    if not no_review:
        _echo_review(kb_triage.run(client_dir), client_dir)


def _echo_review(summary: dict, client_dir: Path) -> None:
    click.echo(
        f"Agent review of {summary['reviewed']} fact(s): {summary['auto_verified']} confirmed, "
        f"{summary['duplicate']} merged as duplicates, {summary['not_client_fact']} set aside (not about the "
        f"client), {summary['unsupported']} rejected (not in the source), {summary['needs_you']} need you."
    )
    for w in summary["warnings"]:
        click.echo(f"  warning: {w}")
    top = kb_triage.needs_you(client_dir)[:kb_triage.NEEDS_YOU_SHOWN]
    for e in top:
        click.echo(f"  [{e.id}] {e.claim}\n      why: {e.triage_reason}")


@cli.command("kb-review")
@click.option("--client", "client_id", required=True)
@click.option("--undo", is_flag=True, help="Put every agent decision back to pending.")
def kb_review_cmd(client_id: str, undo: bool) -> None:
    """Agent review of facts nobody has decided on yet (runs after kb-compile by default)."""
    client_dir = CLIENTS_ROOT / client_id
    if undo:
        click.echo(f"Put {kb_triage.undo(client_dir)} agent decision(s) back to pending.")
        return
    _echo_review(kb_triage.run(client_dir), client_dir)


@cli.command("kb-verify")
@click.option("--client", "client_id", required=True)
@click.option("--approve-all", is_flag=True, help="Approve every pending entry (non-interactive).")
@click.option("--confirmed-by", default="cli-user", help="Name recorded as the confirming human.")
def kb_verify_cmd(client_id: str, approve_all: bool, confirmed_by: str) -> None:
    client_dir = CLIENTS_ROOT / client_id
    pending = kb_verify_stage.pending_entries(client_dir)

    if not pending:
        click.echo("Nothing pending -- knowledge base is fully verified.")
        return

    click.echo(f"{len(pending)} pending entr{'y' if len(pending) == 1 else 'ies'}:")
    for e in pending:
        click.echo(f"  [{e.id}] {e.claim}  (source: {e.source_doc})")

    if approve_all:
        kb_verify_stage.approve(client_dir, [e.id for e in pending], confirmed_by)
        click.echo(f"Approved all {len(pending)} entries as {confirmed_by!r}.")
        return

    to_approve: list[str] = []
    for e in pending:
        if click.confirm(f"Approve [{e.id}] {e.claim!r}?", default=True):
            to_approve.append(e.id)
    if to_approve:
        kb_verify_stage.approve(client_dir, to_approve, confirmed_by)
        click.echo(f"Approved {len(to_approve)} of {len(pending)} entries.")
    else:
        click.echo("Nothing approved.")


@cli.command("kb-add")
@click.option("--client", "client_id", required=True)
@click.option("--claim", required=True, help="The fact itself, as a plain sentence.")
@click.option("--source", required=True, help="How this is known, e.g. 'confirmed by the client lead on a 2026-09-20 call'.")
@click.option("--confirmed-by", default="cli-user", help="Name recorded as the confirming human.")
@click.option("--verified", is_flag=True, help="Mark this verified immediately -- you are the source, so it skips the kb-verify round-trip an extracted claim needs.")
@click.option("--supersedes", default=None, help="An existing KB entry id this fact replaces; marks that entry stale.")
def kb_add_cmd(
    client_id: str, claim: str, source: str, confirmed_by: str, verified: bool, supersedes: Optional[str]
) -> None:
    client_dir = CLIENTS_ROOT / client_id
    try:
        entry = kb_add_stage.add_claim(
            client_dir, claim, source, confirmed_by, verified=verified, supersedes=supersedes
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))

    status_word = "verified" if verified else "pending (run kb-verify to confirm)"
    click.echo(f"Added {entry.id} ({status_word}): {entry.claim!r}")
    if supersedes:
        click.echo(f"Marked {supersedes} as stale, superseded by {entry.id}.")


@cli.command("run")
@click.option("--client", "client_id", required=True)
@click.option("--brief", "brief_path", required=True, type=click.Path(exists=True))
@click.option("--tone", "tone_preset", default=None,
              help="Tone preset name (defaults to the ICP's default tone, then the client's first preset).")
@click.option("--icp", default=None, help="ICP name from the client's icps.yaml (or set `icp:` in the brief).")
@click.option("--angle-index", default=0, help="Which angle candidate to use (0-indexed).")
@click.option("--accept-preflight", is_flag=True,
              help="Proceed past brief problems found before drafting; they are recorded and the client's rules win.")
def run_cmd(client_id: str, brief_path: str, tone_preset: Optional[str], icp: Optional[str], angle_index: int,
            accept_preflight: bool) -> None:
    try:
        output_dir = run_job.run(
            client_id=client_id,
            brief_path=Path(brief_path),
            clients_root=CLIENTS_ROOT,
            output_root=OUTPUT_ROOT,
            tone_preset=tone_preset,
            angle_index=angle_index,
            accept_preflight=accept_preflight,
            icp=icp,
        )
    except run_job.PreflightIssues as exc:
        lines = "\n".join(f"  - {i}" for i in exc.issues)
        raise click.ClickException(
            f"the brief can't pass as written:\n{lines}\nFix the brief, or rerun with --accept-preflight."
        )
    click.echo(f"Job package written to {output_dir / 'package.json'}")


@cli.command("plan")
@click.option("--client", "client_id", required=True)
def plan_cmd(client_id: str) -> None:
    """List the client's content plan, in plan order."""
    items = content_plan.in_order(content_plan.load(CLIENTS_ROOT / client_id))
    if not items:
        click.echo("No content plan yet -- kb-compile a strategy document, or add items to content_plan.yaml.")
    for i in items:
        keyword = f" [{i.primary_keyword}]" if i.primary_keyword else ""
        click.echo(f"  {i.id}  {i.status:<9} #{i.priority or '-'}  {i.format}  {i.title}{keyword}")


@cli.command("plan-approve")
@click.option("--client", "client_id", required=True)
@click.option("--all", "approve_all", is_flag=True, help="Approve every proposed item.")
@click.argument("item_ids", nargs=-1)
def plan_approve_cmd(client_id: str, approve_all: bool, item_ids: tuple[str, ...]) -> None:
    """Approve plan items so briefs can be written from them (brief field: plan_item_id)."""
    client_dir = CLIENTS_ROOT / client_id
    ids = [i.id for i in content_plan.load(client_dir) if i.status == "proposed"] if approve_all else list(item_ids)
    if not ids:
        raise click.ClickException("name item ids, or pass --all")
    click.echo(f"Approved {content_plan.set_status(client_dir, ids, 'approved')} plan item(s).")


@cli.command("finish")
@click.option("--job", "job_id", required=True, help="An escalated job under output/jobs/.")
@click.option("--body-file", required=True, type=click.Path(exists=True), help="Your edited body text.")
@click.option("--by", "edited_by", default="cli-user", help="Name recorded as the editor.")
def finish_cmd(job_id: str, body_file: str, edited_by: str) -> None:
    """Package your own edit of an escalated draft (house style and do-not-say still apply)."""
    ok, results = run_job.finish_by_hand(job_id, Path(body_file).read_text(encoding="utf-8"),
                                         CLIENTS_ROOT, OUTPUT_ROOT, edited_by)
    if not ok:
        items = [f"  - {g.gate_name}: {item}" for g in results if g.status.value == "failed" for item in g.flagged_items]
        raise click.ClickException("the edit still fails:\n" + "\n".join(items))
    click.echo(f"Packaged {OUTPUT_ROOT / 'jobs' / job_id / 'package.json'}")


@cli.command("suggest-rules")
@click.option("--client", "client_id", required=True)
def suggest_rules_cmd(client_id: str) -> None:
    """Propose style-guide / framing / banned-phrase rules from recurring judge notes."""
    profile = _load_client_profile(CLIENTS_ROOT / client_id)
    suggestions, reason = lessons.suggest_rules(OUTPUT_ROOT, profile)
    if reason:
        click.echo(reason)
    for s in suggestions:
        click.echo(f"  [{s['target']}] {s['rule']}\n      why: {s['why']} ({s['evidence']})")
    if suggestions:
        click.echo("Adopt one with: add-rule --client ID --target TARGET --rule \"...\"")


@cli.command("add-rule")
@click.option("--client", "client_id", required=True)
@click.option("--target", type=click.Choice(sorted(lessons.TARGETS)), required=True)
@click.option("--rule", required=True)
def add_rule_cmd(client_id: str, target: str, rule: str) -> None:
    """Add one rule to the client's style guide, framing rules or banned phrases (additive only)."""
    path = lessons.apply_suggestion(CLIENTS_ROOT / client_id, target, rule)
    click.echo(f"Added to {path}")


@cli.command("draft-icps")
@click.option("--client", "client_id", required=True)
def draft_icps_cmd(client_id: str) -> None:
    """Drafts ICPs for an existing client from its documents and set-aside audience notes."""
    client_dir = CLIENTS_ROOT / client_id
    added = client_setup.draft_icps(client_dir, client_dir / "knowledge_base" / "documents")
    click.echo(f"Added {added} draft ICP(s) to {client_dir / 'icps.yaml'}; review them before use.")


@cli.command("export")
@click.option("--job", "job_id", required=True, help="Job id under output/jobs/.")
def export_cmd(job_id: str) -> None:
    """Regenerate package.pdf from an existing job's package.json, without rerunning the model.

    Useful after hand-editing package.json (e.g. picking a final title out of the options)."""
    output_dir = OUTPUT_ROOT / "jobs" / job_id
    package_path = output_dir / "package.json"
    if not package_path.exists():
        raise click.ClickException(f"no package.json at {package_path}")

    package = json.loads(package_path.read_text(encoding="utf-8"))
    record = job_record_ledger.load(output_dir)
    profile = _load_client_profile(CLIENTS_ROOT / record.client_id)

    pdf_path = pdf_export.render_package_pdf(package, record, profile.company_name, output_dir / "package.pdf")
    click.echo(f"Rendered {pdf_path}")


@cli.command("approve")
@click.option("--job", "job_id", required=True, help="Job id under output/jobs/.")
@click.option("--approved-by", default="cli-user", help="Name recorded as having approved this piece.")
def approve_cmd(job_id: str, approved_by: str) -> None:
    """Marks a completed job's draft as a confirmed example of the client's voice for its exact
    format. Future angle/draft/microcopy generation for the same client+format sees it as a
    reference. Only a job that reached status=complete can be approved."""
    output_dir = OUTPUT_ROOT / "jobs" / job_id
    record = job_record_ledger.load(output_dir)
    if record.status != "complete":
        raise click.ClickException(
            f"job {job_id!r} has status {record.status!r}, not 'complete' -- only a finished, "
            "gate-passed job can be approved"
        )
    if not record.format:
        raise click.ClickException(
            f"job {job_id!r} has no recorded format (it predates format tracking) -- cannot scope "
            "an approved example to a format"
        )

    package_path = output_dir / "package.json"
    if not package_path.exists():
        raise click.ClickException(f"no package.json at {package_path}")
    package = json.loads(package_path.read_text(encoding="utf-8"))

    path = approved_examples.record_approval(
        CLIENTS_ROOT / record.client_id,
        job_id,
        record.format,
        package,
        approved_by,
        dt.date.today().isoformat(),
    )
    click.echo(f"Recorded approved example at {path}")


if __name__ == "__main__":
    cli()
