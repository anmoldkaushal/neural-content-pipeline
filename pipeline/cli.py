"""CLI entrypoints: `python -m pipeline.cli <subcommand> ...`"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from pipeline import client_setup, run_job
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
    target = client_setup.init_client(client_id, CLIENTS_ROOT, Path(from_docs) if from_docs else None)
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


if __name__ == "__main__":
    cli()
