"""Typed contracts shared by every stage. Nothing in pipeline/ passes untyped dicts between
stages — every hand-off is a Pydantic model, so a malformed stage output fails loudly at the
boundary instead of silently corrupting the next stage's input."""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class WordRange(BaseModel):
    """Inclusive length bounds for a body. A range, not a single target: the writer aims inside it
    and the style gate enforces it, which a lone number could never say."""

    min: int
    max: int

    @model_validator(mode="after")
    def _ordered(self) -> "WordRange":
        if self.min < 1 or self.max < self.min:
            raise ValueError(f"word range must satisfy 1 <= min <= max, got {self.min}-{self.max}")
        return self

    def label(self) -> str:
        return f"{self.min}-{self.max} words"


def legacy_word_range(target: int) -> WordRange:
    """A pre-range brief's single target, widened to the band a writer would reasonably read it
    as (15% either side)."""
    return WordRange(min=max(1, round(target * 0.85)), max=max(1, round(target * 1.15)))


class Brief(BaseModel):
    """The task: what the human is asking for."""

    client_id: str
    goal: str
    audience: str
    format: str  # e.g. "blog_post", "email", "social_post", "landing_page", or a client's custom type
    format_description: Optional[str] = None  # what a custom content type is, for the prompts
    word_range: Optional[WordRange] = None  # per piece, for a sequence
    # How many pieces a sequence format writes (email_sequence: Email 1..N, each gated on its own).
    # None for a single-piece format; resolved from config's `sequence` when the format has one.
    sequence_length: Optional[int] = None
    icp: Optional[str] = None  # name of the client ICP this piece is written for, if one was picked
    # The ICP rendered as prompt text (pipeline/icps.py), resolved once at start so a session is
    # self-contained even if the profile changes later. Targeting context, never a claim.
    icp_profile: Optional[str] = None
    angle_hint: Optional[str] = None
    deadline: Optional[dt.date] = None
    notes: Optional[str] = None
    # A content-plan item (clients/<id>/content_plan.yaml) this piece is written from, if any.
    plan_item_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_target(cls, data: Any) -> Any:
        # Briefs and job sessions written before word ranges carried a single target_word_count.
        if isinstance(data, dict) and "target_word_count" in data:
            data = dict(data)
            target = data.pop("target_word_count")
            if target and not data.get("word_range"):
                data["word_range"] = legacy_word_range(int(target))
        return data


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
    # Set by the agent review (pipeline/stages/kb_triage.py). also_sources: where merged duplicates
    # of this fact were found ("doc · location"). original_claim: the extracted wording before the
    # agent merged duplicates under one wording, kept so the review can be undone. evidence: the
    # passage the fact was checked against, quoted from the source.
    also_sources: list[str] = Field(default_factory=list)
    original_claim: Optional[str] = None
    evidence: Optional[str] = None
    # "auto_verified" | "needs_you" | "unsupported" | "duplicate" | "not_client_fact"; None until
    # reviewed. triage_priority orders the "needs you" queue (higher first).
    triage: Optional[str] = None
    triage_reason: Optional[str] = None
    triage_priority: Optional[int] = None


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
    pages: list[str] = Field(default_factory=list)  # per-page text, when the format has pages
    warnings: list[str] = Field(default_factory=list)
    ingested_at: dt.datetime = Field(default_factory=dt.datetime.utcnow)


class TonePreset(BaseModel):
    name: str
    description: str
    sample_line: str


class Icp(BaseModel):
    """An ideal customer profile: who a piece is written for. Targeting context only -- a pain point
    here can shape an angle, but it is not a verified fact and never appears in copy as a claim."""

    name: str
    summary: str
    roles: list[str] = Field(default_factory=list)  # job titles / buyer roles
    company: str = ""  # firmographics: size, sector, region, maturity
    pains: list[str] = Field(default_factory=list)
    cares_about: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    default_tone: Optional[str] = None  # a tone preset name, pre-selected when this ICP is picked


class ClientProfile(BaseModel):
    client_id: str
    company_name: str
    industry: str
    website: Optional[str] = None
    synthetic: bool = False
    tone_presets: list[TonePreset] = Field(default_factory=list)
    icps: list[Icp] = Field(default_factory=list)
    banned_words: list[str] = Field(default_factory=list)  # EXTRA_BANNED_WORDS, additive only
    banned_phrases: list[str] = Field(default_factory=list)  # EXTRA_BANNED_PHRASES, additive only
    do_not_say: list[str] = Field(default_factory=list)  # literal terms, substring-checked
    do_not_frame: list[str] = Field(default_factory=list)  # framing/positioning rules, judged
    style_guide: str = ""  # style_guide.md as written; the drafter and the voice judge both read it


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
    # What the writer says it changed on a revision pass; empty on a first draft.
    change_notes: list[str] = Field(default_factory=list)
    piece: Optional[int] = None  # 1-based position in a sequence; None for a single piece


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class GateResult(BaseModel):
    gate_name: str
    status: GateStatus
    detail: str
    flagged_items: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # a judged gate's minor, non-blocking notes


class RevisionRound(BaseModel):
    """One draft and what the gates said about it -- kept for every round, not just the last, so
    an escalation shows what was tried and whether the writer was converging."""

    revision: int
    word_count: int
    gate_results: list[GateResult] = Field(default_factory=list)
    change_notes: list[str] = Field(default_factory=list)
    piece: Optional[int] = None  # which email of a sequence this round drafted


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
    icp: Optional[str] = None
    microcopy_selected: dict[str, str] = Field(default_factory=dict)
    rounds: list[RevisionRound] = Field(default_factory=list)
    # Brief problems found before drafting that a human chose to proceed past.
    preflight_overridden: list[str] = Field(default_factory=list)
    plan_item_id: Optional[str] = None
    # Set when a human finished an escalated draft by hand instead of the pipeline.
    human_edited: bool = False


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
    preflight_overridden: list[str] = Field(default_factory=list)


class PlanItem(BaseModel):
    """One planned piece from a client's content strategy (clients/<id>/content_plan.yaml). This
    is direction -- what to write about, for whom, against which keyword -- never a fact a draft
    may state. Extracted items start 'proposed'; only 'approved' ones are offered to a brief."""

    id: str
    title: str
    format: str = "blog_post"
    priority: int = 0  # order within the plan; 1 comes first, 0 means unranked
    primary_keyword: Optional[str] = None
    cluster: Optional[str] = None  # e.g. "comparison", "location", "education"
    audience: Optional[str] = None
    notes: Optional[str] = None
    source_doc: Optional[str] = None
    location: Optional[str] = None
    status: str = "proposed"  # "proposed" | "approved" | "drafted" | "rejected"
    job_ids: list[str] = Field(default_factory=list)


CONTEXT_ROLES = {
    "strategy": "plans, positioning, audiences, keywords: direction for what to write",
    "past_content": "pieces the client already published: a reference for voice and coverage",
    "brand": "brand or style guidance",
    "notes": "meeting notes or transcripts",
    "other": "anything else",
}


class ContextSection(BaseModel):
    heading: str
    text: str


class ContextDoc(BaseModel):
    """A client document kept readable for the writer, beside the facts extracted from it. The
    writer may take direction from it; only verified KB facts may be stated as fact."""

    name: str  # the source file name under knowledge_base/documents/
    role: str = "other"  # a CONTEXT_ROLES key
    summary: str = ""
    sections: list[ContextSection] = Field(default_factory=list)
