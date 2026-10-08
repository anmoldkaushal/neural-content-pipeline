"""A client's content plan: the planned pieces a strategy document lays out (topics, keywords,
clusters, order), kept in clients/<id>/content_plan.yaml. Before this, a topic list in a strategy
had nowhere to live -- it isn't a fact about the client, so kb_compile set it aside and no prompt
ever saw it, which is why "write the first two blogs from the strategy" could not work.

Plan items are direction, not facts: nothing here is ever cited as a claim, so they need a lighter
review than KB entries -- a human approves which items are real plan items, not each sentence."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Iterable, Optional

import yaml

from pipeline.schemas import PlanItem

PLAN_FILE = "content_plan.yaml"
_HEADER = (
    "# Content plan: planned pieces from the client's strategy. Items extracted from documents\n"
    "# start 'proposed'; only 'approved' items are offered when writing a brief. Direction only:\n"
    "# nothing here is ever stated in copy as a fact.\n"
)


def _path(client_dir: Path) -> Path:
    return client_dir / PLAN_FILE


def load(client_dir: Path) -> list[PlanItem]:
    path = _path(client_dir)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [PlanItem(**item) for item in data.get("items") or []]


def save(client_dir: Path, items: list[PlanItem]) -> None:
    body = yaml.safe_dump(
        {"items": [i.model_dump(mode="json", exclude_none=True) for i in items]},
        sort_keys=False, allow_unicode=True,
    )
    _path(client_dir).write_text(_HEADER + body, encoding="utf-8")


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def merge_proposed(client_dir: Path, proposed: Iterable[dict], source_doc: str) -> list[PlanItem]:
    """Adds newly extracted items as 'proposed', skipping any whose title is already in the plan
    (in any status, so a rejected item isn't re-proposed on the next compile). Returns the new ones."""
    items = load(client_dir)
    seen = {_key(i.title) for i in items}
    added: list[PlanItem] = []
    for raw in proposed:
        title = str(raw.get("title", "")).strip()
        if not title or _key(title) in seen:
            continue
        try:
            priority = int(raw.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        item = PlanItem(
            id=f"plan-{uuid.uuid4().hex[:6]}",
            title=title,
            format=str(raw.get("format") or "blog_post").strip() or "blog_post",
            priority=priority,
            primary_keyword=(str(raw["primary_keyword"]).strip() or None) if raw.get("primary_keyword") else None,
            cluster=(str(raw["cluster"]).strip() or None) if raw.get("cluster") else None,
            audience=(str(raw["audience"]).strip() or None) if raw.get("audience") else None,
            notes=(str(raw["notes"]).strip() or None) if raw.get("notes") else None,
            source_doc=source_doc,
            location=(str(raw["location"]).strip() or None) if raw.get("location") else None,
        )
        items.append(item)
        added.append(item)
        seen.add(_key(title))
    if added:
        save(client_dir, items)
    return added


def set_status(client_dir: Path, item_ids: Iterable[str], status: str) -> int:
    if status not in {"proposed", "approved", "drafted", "rejected"}:
        raise ValueError(f"unknown plan item status {status!r}")
    wanted = set(item_ids)
    items = load(client_dir)
    changed = 0
    for i in items:
        if i.id in wanted:
            i.status = status
            changed += 1
    save(client_dir, items)
    return changed


def get(client_dir: Path, item_id: str) -> Optional[PlanItem]:
    return next((i for i in load(client_dir) if i.id == item_id), None)


def in_order(items: list[PlanItem]) -> list[PlanItem]:
    """Plan order: ranked items by priority (1 first), unranked ones after, in file order."""
    return sorted(items, key=lambda i: (i.priority <= 0, i.priority))


def writable(client_dir: Path) -> list[PlanItem]:
    """Items a brief may be written from: approved first in plan order, then already-drafted ones
    (a second piece on the same topic is legitimate, but it shouldn't crowd the next one out)."""
    items = load(client_dir)
    approved = in_order([i for i in items if i.status == "approved"])
    drafted = in_order([i for i in items if i.status == "drafted"])
    return approved + drafted


def mark_drafted(client_dir: Path, item_id: str, job_id: str) -> None:
    items = load(client_dir)
    for i in items:
        if i.id == item_id:
            i.status = "drafted"
            if job_id not in i.job_ids:
                i.job_ids.append(job_id)
    save(client_dir, items)


def brief_block(item: Optional[PlanItem]) -> str:
    """The plan item as prompt lines, for the angle menu, synthesis and drafter."""
    if item is None:
        return ""
    lines = [f"Content plan item: {item.title}"]
    if item.primary_keyword:
        lines.append(f"Primary search keyword: {item.primary_keyword} (use it naturally in the title and early in the body)")
    if item.cluster:
        lines.append(f"Plan cluster: {item.cluster}")
    if item.audience:
        lines.append(f"Plan audience: {item.audience}")
    if item.notes:
        lines.append(f"Plan notes: {item.notes}")
    if item.anchor:
        # A theme, not a feature: written as "That is the Reset", the fact checker failed it on
        # every round, since no verified fact names a Reset.
        lines.append(f"Anchor word: {item.anchor.lower()} (use it exactly once, as an ordinary word in a sentence about "
                     "the reader; it is a theme, never a name for the event or any part of it)")
    return "\n".join(lines) + "\n"


def sequence_block(items: list[PlanItem]) -> str:
    """A sequence's plan items as prompt lines: the emails the client's plan fixes, in send order."""
    if not items:
        return ""
    out = "The client's content plan fixes the emails of this sequence, in this order:\n"
    for n, item in enumerate(items, 1):
        out += f"Email {n}:\n" + "".join(f"  {line}\n" for line in brief_block(item).splitlines())
    return out
