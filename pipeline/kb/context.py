"""The readable layer of a client's knowledge base. kb_index.json holds the facts a draft may
STATE; this holds the documents themselves, split into sections, so the writer can take DIRECTION
from them: what the strategy says to write, which audiences and keywords matter, how the client's
past pieces sound. Before this, a drafter saw only one-sentence facts and never a document.

The two layers stay separate on purpose. Context is never a source of facts: prompts say so, and
the claims_critic gate fails a draft that states something about the client the verified facts
don't support, wherever the writer picked it up.

Selection is lexical (term overlap weighted by rarity) under a character budget: deterministic,
no extra model call or embedding dependency, and enough at a handful of documents per client."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from pipeline import config
from pipeline.schemas import ContextDoc, ContextSection, IngestedDocument

CONTEXT_DIR = Path("knowledge_base") / "context"
SECTION_CHARS = 2500  # target size when a document has no pages to split on
# Direction-bearing roles first when scores tie: a strategy says what to write.
_ROLE_WEIGHT = {"strategy": 1.3, "brand": 1.2, "notes": 1.0, "other": 1.0, "past_content": 0.9}
_STOP = set(
    "a an and are as at be but by for from has have how i in into is it its of on or our that the "
    "their them they this to was we what when which who why will with you your about not more".split()
)


def _dir(client_dir: Path) -> Path:
    return client_dir / CONTEXT_DIR


def _file_for(client_dir: Path, name: str) -> Path:
    return _dir(client_dir) / (re.sub(r"[^A-Za-z0-9._-]+", "_", name) + ".json")


def _first_line(text: str) -> str:
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return line[:90]


def split_sections(doc: IngestedDocument) -> list[ContextSection]:
    """One section per page when the document has pages; otherwise paragraph runs of about
    SECTION_CHARS, so no section is too large to include whole."""
    if doc.pages:
        return [
            ContextSection(heading=f"p{n} {_first_line(text)}".strip(), text=text.strip())
            for n, text in enumerate(doc.pages, start=1)
            if text.strip()
        ]
    sections: list[ContextSection] = []
    buf: list[str] = []
    for para in re.split(r"\n\s*\n", doc.raw_text):
        if buf and sum(len(p) for p in buf) + len(para) > SECTION_CHARS:
            text = "\n\n".join(buf)
            sections.append(ContextSection(heading=_first_line(text), text=text))
            buf = []
        if para.strip():
            buf.append(para.strip())
    if buf:
        text = "\n\n".join(buf)
        sections.append(ContextSection(heading=_first_line(text), text=text))
    return sections


def save_doc(client_dir: Path, doc: ContextDoc) -> Path:
    path = _file_for(client_dir, doc.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_docs(client_dir: Path) -> list[ContextDoc]:
    folder = _dir(client_dir)
    if not folder.exists():
        return []
    docs: list[ContextDoc] = []
    for path in sorted(folder.glob("*.json")):
        try:
            docs.append(ContextDoc(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001 - one unreadable file shouldn't hide the rest
            continue
    return docs


def _terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9-]+", text.lower()) if t not in _STOP]


def select(
    client_dir: Path,
    query: str,
    budget_chars: Optional[int] = None,
    roles: Optional[set[str]] = None,
) -> str:
    """The most relevant sections for `query`, best first, up to the budget, as a prompt block
    headed with the rule that context is direction, not fact. Empty when there is no context."""
    docs = [d for d in load_docs(client_dir) if roles is None or d.role in roles]
    if not docs:
        return ""
    budget = config.context_budget_chars() if budget_chars is None else budget_chars

    sections = [(d, s) for d in docs for s in d.sections]
    doc_freq: Counter[str] = Counter()
    for _, s in sections:
        doc_freq.update(set(_terms(s.heading + " " + s.text)))
    query_terms = Counter(_terms(query))

    def score(doc: ContextDoc, section: ContextSection) -> float:
        counts = Counter(_terms(section.heading + " " + section.text))
        raw = sum(
            min(counts[t], 3) * q * math.log(1 + len(sections) / doc_freq[t])
            for t, q in query_terms.items() if counts[t]
        )
        return raw * _ROLE_WEIGHT.get(doc.role, 1.0)

    ranked = sorted(sections, key=lambda ds: score(*ds), reverse=True)

    summaries = [f"- {d.name} ({d.role}): {d.summary}" for d in docs if d.summary]
    header = (
        "CLIENT CONTEXT: excerpts from the client's own documents. Use them for direction (what to "
        "write about, for whom, which keywords and positioning). Never state anything from them as "
        "fact unless the same thing is in the verified facts.\n"
    )
    parts = [header]
    if summaries:
        parts.append("Documents:\n" + "\n".join(summaries) + "\n")
    used = sum(len(p) for p in parts)
    for doc, section in ranked:
        if score(doc, section) <= 0 and used > len(header) + 200:
            break  # past the relevant sections; summaries already describe the rest
        chunk = f"\n--- {doc.name} · {section.heading} ---\n{section.text}\n"
        if used + len(chunk) > budget:
            continue
        parts.append(chunk)
        used += len(chunk)
    return "".join(parts)


def voice_reference(client_dir: Path, budget_chars: int = 4000) -> str:
    """Opening sections of the client's own past pieces, for voice, when no approved example of
    the format exists yet. Labelled as reference, not approved copy."""
    docs = [d for d in load_docs(client_dir) if d.role == "past_content"]
    if not docs:
        return ""
    parts = [
        "\nThe client's own past published writing (for voice and rhythm only; do not copy "
        "sentences, and do not take facts from it):\n"
    ]
    used = len(parts[0])
    for doc in docs:
        for section in doc.sections[:2]:
            chunk = f"\n--- {doc.name} ---\n{section.text[:budget_chars // 2]}\n"
            if used + len(chunk) > budget_chars:
                break
            parts.append(chunk)
            used += len(chunk)
    return "".join(parts) if len(parts) > 1 else ""
