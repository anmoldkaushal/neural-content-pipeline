"""CLI entrypoints: `python -m pipeline.cli <subcommand> ...`"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import click

from pipeline import client_setup, run_job
from pipeline.ledger import job_record as job_record_ledger
from pipeline.output import pdf_export
from pipeline.run_job import _load_client_profile
from pipeline.kb import approved_examples
from pipeline.stages import kb_add as kb_add_stage
from pipeline.stages import kb_compile as kb_compile_stage
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
def kb_compile_cmd(client_id: str) -> None:
    client_dir = CLIENTS_ROOT / client_id
    entries, warnings = kb_compile_stage.compile_kb(client_dir)
    pending = [e for e in entries if e.status == "pending"]
    click.echo(f"Compiled {len(entries)} total KB entries ({len(pending)} pending review).")
    for w in warnings:
        click.echo(f"  warning: {w}")


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
@click.option("--tone", "tone_preset", default=None, help="Tone preset name (defaults to the client's first preset).")
@click.option("--angle-index", default=0, help="Which angle candidate to use (0-indexed).")
def run_cmd(client_id: str, brief_path: str, tone_preset: Optional[str], angle_index: int) -> None:
    output_dir = run_job.run(
        client_id=client_id,
        brief_path=Path(brief_path),
        clients_root=CLIENTS_ROOT,
        output_root=OUTPUT_ROOT,
        tone_preset=tone_preset,
        angle_index=angle_index,
    )
    click.echo(f"Job package written to {output_dir / 'package.json'}")


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
