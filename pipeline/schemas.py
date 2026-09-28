"""Typed contracts shared by every stage. Nothing in pipeline/ passes untyped dicts between
stages — every hand-off is a Pydantic model, so a malformed stage output fails loudly at the
boundary instead of silently corrupting the next stage's input."""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Brief(BaseModel):
    """The task: what the human is asking for."""

    client_id: str
    goal: str
    audience: str
    format: str  # e.g. "blog_post", "email", "social_post", "landing_page"
    target_word_count: Optional[int] = None
    angle_hint: Optional[str] = None
    deadline: Optional[dt.date] = None
    notes: Optional[str] = None


class ProvenanceEntry(BaseModel):
    """One verified (or pending) fact. Same shape as neural-pnotp-gtm's provenance.json."""

    claim: str
    type: str = "fact"  # "client" | "credential" | "metric" | "fact"
    source: str
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    date: Optional[dt.date] = None


class KBEntry(BaseModel):
    """One compiled fact in a client's knowledge base."""

    id: str
    claim: str
    source_doc: str
    location: Optional[str] = None  # e.g. page number, section heading
    verified_at: Optional[dt.date] = None
    confirmed_by: Optional[str] = None
    status: str = "pending"  # "pending" | "verified" | "stale" | "rejected" | "excluded"
    superseded_by: Optional[str] = None  # set on an entry once a newer kb-add replaces it
    # Only "claim" (something about the client's own offering that could appear in copy) is
    # queued for human review. "style", "audience" and "reference" are kept as excluded context
    # with a reason; "internal" and "personal" are never stored by kb_compile. See KB_KINDS.
    kind: str = "claim"
    exclusion_reason: Optional[str] = None


KB_KINDS = {
    "claim": "about the client's own offering; could appear in copy",
    "style": "a wording or framing rule: belongs in the style guide or constraints",
    "audience": "who the client targets: belongs in briefs, not stated as a claim",
    "reference": "competitor, market or general-knowledge context: never used in client copy",
    "internal": "project notes, meeting logistics, commercial terms or research metadata",
    "personal": "about a named private individual (health, finances, quotes): never stored",
}


class IngestedDocument(BaseModel):
    """Output of pipeline/ingest/: raw text plus how it was extracted."""

    doc_id: str
    source_path: str
    format: str  # "txt" | "md" | "docx" | "pdf" | "png" | "jpg" | ...
    extraction_method: str  # "native" | "text_layer" | "ocr"
    page_count: Optional[int] = None
    raw_text: str = ""
    warnings: list[str] = Field(default_factory=list)
    ingested_at: dt.datetime = Field(default_factory=dt.datetime.utcnow)


class TonePreset(BaseModel):
    name: str
    description: str
    sample_line: str


class ClientProfile(BaseModel):
    client_id: str
    company_name: str
    industry: str
    website: Optional[str] = None
    synthetic: bool = False
    tone_presets: list[TonePreset] = Field(default_factory=list)
    banned_words: list[str] = Field(default_factory=list)  # EXTRA_BANNED_WORDS, additive only
    banned_phrases: list[str] = Field(default_factory=list)  # EXTRA_BANNED_PHRASES, additive only
    do_not_say: list[str] = Field(default_factory=list)  # literal terms, substring-checked
    do_not_frame: list[str] = Field(default_factory=list)  # framing/positioning rules, judged


class Angle(BaseModel):
    headline: str
    pitch: str
    structure: list[str] = Field(default_factory=list)
    claims_used: list[str] = Field(default_factory=list)  # KBEntry ids


class ToneChoice(BaseModel):
    preset_name: str
    resolved_style_checklist: dict[str, Any] = Field(default_factory=dict)
    ad_hoc: bool = False  # True for a one-off tone typed in for this job, not a pre-approved preset


class MicrocopyField(str, Enum):
    """The generic field set, used when a format has no entry under `formats` in
    config/pipeline_config.yaml. Per-format fields (subject_line, opener, ...) are plain strings."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    HOOK = "hook"
    CTA = "cta"


class MicrocopyCandidate(BaseModel):
    field: str
    text: str
    strategy: str = ""  # e.g. "benefit-led", "urgency-led", "curiosity-led"
    score: Optional[float] = None
    flags: list[str] = Field(default_factory=list)  # deterministic lint hits, shown on the menu


class Draft(BaseModel):
    job_id: str
    body: str
    word_count: int
    outline: list[str] = Field(default_factory=list)
    claims_used: list[str] = Field(default_factory=list)  # KBEntry ids the body actually leans on
    revision: int = 0


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class GateResult(BaseModel):
    gate_name: str
    status: GateStatus
    detail: str
    flagged_items: list[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    job_id: str
    client_id: str
    brief_summary: str
    format: str = ""  # the brief's format (email, linkedin_message, ...); "" for pre-existing jobs
    stages_run: list[str] = Field(default_factory=list)
    gate_results: list[GateResult] = Field(default_factory=list)
    retry_count: dict[str, int] = Field(default_factory=dict)
    human_touchpoints: list[str] = Field(default_factory=list)
    created_at: dt.datetime = Field(default_factory=dt.datetime.utcnow)
    # "in_progress" | "awaiting_selection" | "awaiting_human" | "complete" | "failed"
    status: str = "in_progress"
    # What was chosen, so a package can be rebuilt; None/empty on jobs that predate this.
    angle: Optional[Angle] = None
    tone: Optional[ToneChoice] = None
    microcopy_selected: dict[str, str] = Field(default_factory=dict)


class JobSession(BaseModel):
    """Between-phase state for an interactive job (output/jobs/<id>/session.json): what the menus
    offered, so the selection phase and execute phase can run in separate calls."""

    job_id: str
    client_id: str
    brief: Brief
    angles: list[Angle] = Field(default_factory=list)
    angle_index: Optional[int] = None
    tone: Optional[ToneChoice] = None
    microcopy_menu: dict[str, list[MicrocopyCandidate]] = Field(default_factory=dict)
    microcopy_errors: dict[str, str] = Field(default_factory=dict)
