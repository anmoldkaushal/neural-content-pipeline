"""Public facts: what a piece needs to say about the world (history, culture, what a practice is),
sourced from reputable public references, so the writer isn't limited to what the client's own
documents happen to say. A guide to sacred healing in ancient Egypt needs Egyptian history, and
the client's documents had none: the writer could only pad around the topic.

Kept apart from client facts on purpose. A public fact supports a statement about the world, never
one about the client (its offering, people, places or results still need a client source), and it
lives in its own file, knowledge_base/public_facts.json, as KBEntry rows with kind "public": the
source URL in source_doc, the quoted passage in evidence, the date it was checked in verified_at.

1. Find (one call, the only one in the pipeline with web search and fetch): given the piece's
   outline, or the exact statements a judge flagged, list the outside-world claims it needs, each
   with source URLs and a passage quoted from each page.
2. Check (in code, never on the model's word): fetch every page (or, when the site refuses scripts
   or the quote isn't on the live page, the Internet Archive's copy of it) and require the quoted
   passage to be on it, word for word, with every figure the claim states. A citation the model
   made up, or a quote it paraphrased, fails here.
3. Decide (decide(), deterministic, the policy in one place): confirmed by the agent
   (confirmed_by AGENT) when a checked quote comes from a reputable source, or from two different
   sites, and the claim is low-stakes. Anything medical, legal, about a substance, or with a
   checked source on neither count, waits for a person; a claim no fetched page supports is kept
   as rejected, so it isn't proposed again. Agent confirmations stay visible (package, PDF) and
   undoable (reject())."""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from pipeline import config
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import KBEntry
from pipeline.stages.kb_triage import _norm, missing_figures, quote_found

AGENT = "agent:research"
TOOLS = ["WebSearch", "WebFetch"]
CALL_TIMEOUT = 900  # seconds; the call searches and reads several pages
FETCH_TIMEOUT = 20
PAGE_BYTES = 3_000_000
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "research.md"
# Never auto-confirmed: a wrong one could hurt a reader or the client, whatever the source.
_HIGH_STAKES = {"medical", "legal", "substance", "safety", "statistic", "contested"}
# Reputable by domain: universities, governments, museums and encyclopaedias, plus any the config
# adds. Anything else needs a second, independent site with the same passage checked.
_REPUTABLE_SUFFIXES = (".edu", ".gov", ".mil", ".int", ".ac.uk", ".edu.au", ".ac.jp", ".gov.uk", ".museum")
# A country's government, university and museum domains: egymonuments.gov.eg (Egypt's antiquities
# ministry) ends in .eg, so the suffix list never matched it, and its Luxor facts waited for a person.
_NATIONAL = re.compile(r"(^|\.)(gov|gob|gouv|govt|edu|ac|museum)\.[a-z]{2}$")
_REPUTABLE_DOMAINS = {
    "britannica.com", "metmuseum.org", "britishmuseum.org", "si.edu", "smithsonianmag.com", "louvre.fr",
    "worldhistory.org", "nationalgeographic.com", "unesco.org", "who.int", "nih.gov", "jstor.org",
    "oxfordreference.com", "cambridge.org", "nature.com", "science.org", "plato.stanford.edu", "iep.utm.edu",
    "penn.museum", "brooklynmuseum.org", "mfa.org", "getty.edu", "oi.uchicago.edu", "sciencedirect.com",
    "springer.com", "tandfonline.com", "wiley.com", "pubmed.ncbi.nlm.nih.gov",
}


def public_facts_path(client_dir: Path) -> Path:
    return client_dir / "knowledge_base" / "public_facts.json"


def load(client_dir: Path) -> list[KBEntry]:
    path = public_facts_path(client_dir)
    if not path.exists():
        return []
    return [KBEntry(**e) for e in json.loads(path.read_text(encoding="utf-8"))]


def _save(client_dir: Path, entries: list[KBEntry]) -> None:
    path = public_facts_path(client_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([e.model_dump(mode="json") for e in entries], indent=2), encoding="utf-8")


def verified(client_dir: Path) -> list[KBEntry]:
    return [e for e in load(client_dir) if e.status == "verified"]


# ------------------------------------------------------------------ checking a page


class _Text(HTMLParser):
    """Visible text as a reader sees it. Inline tags (a link, italics) add nothing between words, so
    "Thebes, <a>Egypt</a>." stays "Thebes, Egypt."; only block tags break words apart. Joining every
    text node with a space failed any quote that ran across a link."""
    _SKIP = {"script", "style", "noscript", "svg", "template"}
    _BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
              "table", "section", "article", "header", "footer", "nav", "aside", "blockquote", "figcaption",
              "figure", "dd", "dt", "hr", "main", "pre", "title"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self._BLOCK:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def page_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return "".join(parser.parts)


def fetch(url: str) -> Optional[str]:
    """The page's visible text, or None when it can't be fetched (blocked, missing, not HTML)."""
    if urlparse(url).scheme not in ("http", "https"):
        return None
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            if "html" not in (resp.headers.get("Content-Type") or "html"):
                return None
            raw = resp.read(PAGE_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)  # the Internet Archive sends gzip unasked
    except Exception:  # noqa: BLE001 - any failure means the quote can't be checked
        return None
    return page_text(raw.decode(charset, errors="replace"))


def archived(url: str) -> str:
    """The Internet Archive's copy of the same page, as served then (id_: no archive toolbar).
    Britannica, the Met and UNESCO refuse scripted requests, and some pages load their text by
    script; without this, the most reputable sources were the ones that could never be checked."""
    return f"https://web.archive.org/web/2026id_/{url}"


def domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def reputable(url: str) -> bool:
    host = domain(url)
    extra = {str(d).lower() for d in (config.load().get("research") or {}).get("reputable_domains") or []}
    if any(host == d or host.endswith("." + d) for d in _REPUTABLE_DOMAINS | extra):
        return True
    return host.endswith(_REPUTABLE_SUFFIXES) or bool(_NATIONAL.search(host))


# ------------------------------------------------------------------ decision


def decide(claim: str, checked: list[dict[str, Any]], stakes: list[str]) -> tuple[str, str]:
    """(triage, reason) for one claim. `checked`: its sources, each {"url", "quote", "found",
    "fetched", "missing"}. triage is "auto_verified", "needs_you" or "unsupported". Pure."""
    good = [s for s in checked if s["found"] and not s["missing"]]
    if not good:
        if checked and not any(s["fetched"] for s in checked):
            return "needs_you", "couldn't fetch any source page to check the quote"
        if any(s["found"] for s in checked):
            missing = sorted({m for s in checked for m in s["missing"]})
            return "unsupported", f"figure(s) not on the source page: {', '.join(missing)}"
        return "unsupported", "the quoted passage isn't on any source page"
    risky = sorted({str(s).strip().lower() for s in stakes} & _HIGH_STAKES)
    if risky:
        return "needs_you", f"{', '.join(risky)} claim: a person confirms these"
    sites = {domain(s["url"]) for s in good}
    if any(reputable(s["url"]) for s in good):
        return "auto_verified", f"quote checked on {', '.join(sorted(sites))}"
    if len(sites) >= 2:
        return "auto_verified", f"quote checked on two independent sites: {', '.join(sorted(sites))}"
    return "needs_you", f"one checked source ({', '.join(sites)}) and not one on the reputable list"


def check(candidate: dict[str, Any], fetcher: Optional[Callable[[str], Optional[str]]] = None) -> list[dict[str, Any]]:
    fetcher = fetcher or fetch
    claim = str(candidate.get("claim") or "")
    out = []
    for src in candidate.get("sources") or []:
        if not isinstance(src, dict):
            continue
        url, quote = str(src.get("url") or "").strip(), str(src.get("quote") or "").strip()
        if not url:
            continue
        text, via = fetcher(url), url
        if not (text and quote_found(quote, text)):
            copy_text = fetcher(archived(url))
            if copy_text and (text is None or quote_found(quote, copy_text)):
                text, via = copy_text, archived(url)
        found = bool(text) and quote_found(quote, text)
        out.append({"url": url, "quote": quote, "fetched": text is not None, "found": found, "via": via,
                    # every figure the claim states must be on the page it was checked on
                    "missing": missing_figures(claim, text) if found else []})
    return out


def _id(claim: str) -> str:
    return "pub-" + hashlib.sha1(_norm(claim).encode("utf-8")).hexdigest()[:8]


def _entry(claim: str, checked: list[dict[str, Any]], triage: str, reason: str, today: dt.date) -> KBEntry:
    good = [s for s in checked if s["found"] and not s["missing"]]
    best = sorted(good, key=lambda s: not reputable(s["url"]))[0] if good else (checked[0] if checked else None)
    auto = triage == "auto_verified"
    return KBEntry(
        id=_id(claim), claim=claim, kind="public",
        source_doc=best["url"] if best else "(no source)",
        also_sources=[s["url"] for s in good if best and s["url"] != best["url"]],
        evidence=best["quote"] if best else None,
        status="verified" if auto else "pending" if triage == "needs_you" else "rejected",
        confirmed_by=AGENT if auto else None, verified_at=today if auto else None,
        triage=triage,
        triage_reason=reason + (" (on the Internet Archive's copy of the page)"
                                if best and best.get("via", best["url"]) != best["url"] and best["found"] else ""),
    )


# ------------------------------------------------------------------ the call


def _with_tools(transport: ClaudeTransport) -> ClaudeTransport:
    """A copy with web search and fetch, so the job's other calls stay offline."""
    out = copy.copy(transport)
    out.tools = list(TOOLS)
    if hasattr(out, "timeout"):
        out.timeout = max(int(out.timeout), CALL_TIMEOUT)
    return out


def _known_block(entries: list[KBEntry]) -> str:
    known = [e for e in entries if e.status in ("verified", "pending")]
    rejected = [e for e in entries if e.status == "rejected"]
    out = ""
    if known:
        out += "Already sourced (don't repeat these):\n" + "".join(f"- {e.claim}\n" for e in known)
    if rejected:
        out += "Rejected before (don't propose these again):\n" + "".join(f"- {e.claim}\n" for e in rejected)
    return out


def find(task: str, rules: str, known: list[KBEntry], transport: ClaudeTransport, max_claims: int) -> list[dict]:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (f"{template}\nAt most {max_claims} claims.\n\n{rules}\n{_known_block(known)}\n{task}")
    parsed, result = call_json(_with_tools(transport), system_prompt="", user_prompt=prompt)
    if not result.ok:
        return []
    items = parsed.get("claims") if isinstance(parsed, dict) else parsed
    return [c for c in items or [] if isinstance(c, dict) and str(c.get("claim") or "").strip()][:max_claims]


def _merge(client_dir: Path, candidates: list[dict],
           fetcher: Optional[Callable[[str], Optional[str]]]) -> list[KBEntry]:
    """Checks and decides each candidate and adds it to public_facts.json. Returns the new rows. A
    claim already on file (any status: a person's rejection stands) isn't checked again."""
    entries = load(client_dir)
    seen = {_norm(e.claim) for e in entries} | {e.id for e in entries}
    today = dt.date.today()
    new: list[KBEntry] = []
    for cand in candidates:
        claim = str(cand["claim"]).strip()
        if _norm(claim) in seen or _id(claim) in seen:
            continue
        seen.add(_norm(claim))
        checked = check(cand, fetcher)
        triage, reason = decide(claim, checked, [str(s) for s in cand.get("stakes") or []])
        new.append(_entry(claim, checked, triage, reason, today))
    if new:
        _save(client_dir, entries + new)
    return new


def redecide(client_dir: Path) -> list[KBEntry]:
    """Re-applies the domain rule to facts that wait for a person only because their one checked
    source wasn't on the reputable list then. A fact a person has decided, or that waits for any
    other reason (stakes, an unfetched page), stays as it is. Returns the facts it confirmed."""
    entries, today, out = load(client_dir), dt.date.today(), []
    for e in entries:
        if (e.status == "pending" and e.confirmed_by is None and e.evidence
                and (e.triage_reason or "").startswith("one checked source") and reputable(e.source_doc)):
            e.status, e.confirmed_by, e.verified_at, e.triage = "verified", AGENT, today, "auto_verified"
            e.triage_reason = f"quote checked on {domain(e.source_doc)}" + (
                " (on the Internet Archive's copy of the page)" if "Internet Archive" in (e.triage_reason or "") else "")
            out.append(e)
    if out:
        _save(client_dir, entries)
    return out


def settings() -> dict[str, Any]:
    cfg = config.load().get("research") or {}
    return {"enabled": bool(cfg.get("enabled", True)), "max_claims": int(cfg.get("max_claims", 12))}


def for_piece(
    client_dir: Path,
    topic: str,
    outline: list[str],
    rules: str,
    transport: Optional[ClaudeTransport] = None,
    fetcher: Optional[Callable[[str], Optional[str]]] = None,
) -> list[KBEntry]:
    """Sources the outside-world facts a piece's outline needs. Returns the rows it added."""
    task = ("THE PIECE:\n" + topic.strip() + "\nOutline:\n" + "".join(f"- {s}\n" for s in outline)
            + "\nList the public facts this piece needs to cover its topic well.")
    confirmed = redecide(client_dir)
    candidates = find(task, rules, load(client_dir), transport or ClaudeTransport(), settings()["max_claims"])
    return confirmed + _merge(client_dir, candidates, fetcher)


def for_statements(
    client_dir: Path,
    statements: list[str],
    rules: str,
    transport: Optional[ClaudeTransport] = None,
    fetcher: Optional[Callable[[str], Optional[str]]] = None,
) -> list[KBEntry]:
    """Tries to source statements a judge flagged as unbacked claims about the world, so a revision
    can keep a true sentence instead of cutting it (and padding the gap with something invented)."""
    task = ("STATEMENTS A FACT CHECKER FLAGGED (source each one exactly as stated, or a corrected "
            "version if the sources say something different; skip any that are about the client):\n"
            + "".join(f"- {s}\n" for s in statements))
    candidates = find(task, rules, load(client_dir), transport or ClaudeTransport(), len(statements) + 2)
    return _merge(client_dir, candidates, fetcher)


def reject(client_dir: Path, ids: list[str], by: str) -> int:
    """Undoes the agent's confirmation (or a person's): the fact stops reaching drafts and isn't
    proposed again."""
    entries, n = load(client_dir), 0
    for e in entries:
        if e.id in ids and e.status != "rejected":
            e.status, e.triage_reason, e.confirmed_by, n = "rejected", f"rejected by {by}", None, n + 1
    _save(client_dir, entries)
    return n


def confirm(client_dir: Path, ids: list[str], by: str) -> int:
    entries, n = load(client_dir), 0
    for e in entries:
        if e.id in ids and e.status != "verified":
            e.status, e.confirmed_by, e.verified_at, n = "verified", by, dt.date.today(), n + 1
    _save(client_dir, entries)
    return n
