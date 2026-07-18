"""Pydantic contracts used by PDF parsing, structured analysis, and the UI.

The classes prefixed with ``Model`` or named ``*Output`` are deliberately
simple because they are used as Structured Output schemas.  Importantly, the
model never returns page numbers or excerpts: it can only return opaque source
IDs.  Page numbers and verbatim excerpts are hydrated locally afterwards.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects undeclared fields."""

    model_config = ConfigDict(extra="forbid")


class SourceKind(str, Enum):
    PARAGRAPH = "paragraph"
    FIGURE = "figure"
    TABLE = "table"
    METHOD = "method"
    STATISTICAL_RESULT = "statistical_result"


class ClaimType(str, Enum):
    DESCRIPTIVE = "descriptive"
    ASSOCIATION = "association"
    CAUSAL = "causal"
    MECHANISTIC = "mechanistic"
    PREDICTIVE = "predictive"
    THERAPEUTIC = "therapeutic"
    METHODOLOGICAL = "methodological"


class Importance(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class Relationship(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class IssueCategory(str, Enum):
    OVERCLAIMING = "overclaiming"
    MISSING_CONTROL = "missing_control"
    CAUSAL_OVERINTERPRETATION = "causal_overinterpretation"
    REPRODUCIBILITY = "reproducibility"
    STATISTICAL_REPORTING = "statistical_reporting"
    GENERALIZABILITY = "generalizability"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NearbyContext(StrictModel):
    """A neighboring local PDF span shown only for reading orientation."""

    source_id: str
    page_number: int
    kind: SourceKind
    section: str
    excerpt: str
    position: Literal["before", "after"]
    content_origin: Literal["paper"] = "paper"


class SourceUnit(StrictModel):
    """A locally extracted, immutable span of paper content."""

    source_id: str
    page_number: int
    block_index: int
    kind: SourceKind
    section: str
    label: str
    excerpt: str
    bbox: list[float]
    nearby_context: list[NearbyContext] = Field(default_factory=list)
    content_origin: Literal["paper"] = "paper"

    @field_validator("source_id", "excerpt")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_id and excerpt must not be empty")
        return value

    @field_validator("page_number")
    @classmethod
    def page_is_one_based(cls, value: int) -> int:
        if value < 1:
            raise ValueError("page_number must be one-based")
        return value

    @field_validator("bbox")
    @classmethod
    def bbox_has_four_values(cls, value: list[float]) -> list[float]:
        if len(value) != 4:
            raise ValueError("bbox must contain x0, y0, x1, y1")
        return value


class PaperDocument(StrictModel):
    """Parsed paper plus the source registry sent to the model."""

    filename: str
    sha256: str
    page_count: int
    sources: list[SourceUnit]

    @model_validator(mode="after")
    def source_registry_is_consistent(self) -> "PaperDocument":
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source IDs must be unique")
        if any(source.page_number > self.page_count for source in self.sources):
            raise ValueError("a source page exceeds the document page count")
        return self

    @property
    def character_count(self) -> int:
        return sum(len(source.excerpt) for source in self.sources)

    @property
    def source_index(self) -> dict[str, SourceUnit]:
        return {source.source_id: source for source in self.sources}


# ---------------------------------------------------------------------------
# Structured Output schemas
# ---------------------------------------------------------------------------


class ClaimCandidate(StrictModel):
    claim_id: str = Field(description="Stable ID such as C1, C2, C3")
    statement: str = Field(description="Faithful, concise restatement of a major paper claim")
    claim_type: ClaimType
    importance: Importance
    claim_source_ids: list[str] = Field(
        description="IDs of paper spans where the authors state this claim"
    )
    scope_qualifier: str = Field(
        description=(
            "Concrete tested context: population or model system, intervention or exposure, "
            "comparator, endpoint, timeframe, and material boundary when reported"
        )
    )

    @model_validator(mode="after")
    def claim_has_traceable_origin(self) -> "ClaimCandidate":
        if not re.fullmatch(r"C[1-9]\d*", self.claim_id.strip()):
            raise ValueError("claim_id must use the form C1, C2, ...")
        if not self.statement.strip():
            raise ValueError("claim statement must not be empty")
        if not self.claim_source_ids:
            raise ValueError("every extracted claim must cite at least one paper source")
        self.claim_source_ids = list(dict.fromkeys(self.claim_source_ids))
        return self


class ClaimExtractionOutput(StrictModel):
    paper_title: str
    paper_title_source_ids: list[str]
    claims: list[ClaimCandidate]
    extraction_notes: list[str]

    @model_validator(mode="after")
    def extracted_claim_ids_are_unique(self) -> "ClaimExtractionOutput":
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim IDs must be unique")
        expected = [f"C{index}" for index in range(1, len(ids) + 1)]
        if ids != expected:
            raise ValueError("claim IDs must be sequential and ordered from C1")
        self.paper_title_source_ids = list(dict.fromkeys(self.paper_title_source_ids))
        return self


class ModelEvidenceLink(StrictModel):
    source_id: str
    relationship: Relationship
    supports_or_limits: str = Field(
        description=(
            "Plain-language rationale naming the claim component, what this exact span "
            "establishes, and why the relationship label fits"
        )
    )
    caveat: str = Field(
        description=(
            "The most decision-relevant boundary on interpretation; use an empty string "
            "only when the linked span adds no material qualification"
        )
    )


class ModelIssue(StrictModel):
    category: IssueCategory
    severity: Severity
    description: str
    why_it_matters: str
    source_ids: list[str] = Field(
        description="Relevant paper source IDs; empty only when the issue is an absence"
    )
    recommendation: str


class ModelClaimAssessment(StrictModel):
    claim_id: str
    overall_relationship: Relationship
    confidence_score: float = Field(
        description="Calibration score from 0.0 to 1.0, not a statistical probability"
    )
    assessment_summary: str = Field(
        description=(
            "Two to four plain-language sentences explaining the rating from the tested "
            "context, comparator, endpoint, direction, and most important limitation"
        )
    )
    alternative_interpretation: str = Field(
        description=(
            "A specific plausible interpretation that would change how the result is read; "
            "use an empty string when none is supported by the analyzed paper"
        )
    )
    evidence: list[ModelEvidenceLink]
    issues: list[ModelIssue]

    @field_validator("confidence_score")
    @classmethod
    def confidence_is_bounded(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence_score must be between 0 and 1")
        return value


class ClaimAssessmentOutput(StrictModel):
    assessments: list[ModelClaimAssessment]
    paper_level_issues: list[ModelIssue]
    global_limitations: list[str]


# ---------------------------------------------------------------------------
# Hydrated report models: paper content and model inference stay distinct.
# ---------------------------------------------------------------------------


class EvidenceLink(StrictModel):
    source: SourceUnit
    relationship: Relationship
    model_inference: str
    caveat: str
    inference_origin: Literal["model_inference"] = "model_inference"


class IssueFlag(StrictModel):
    category: IssueCategory
    severity: Severity
    description: str
    why_it_matters: str
    sources: list[SourceUnit]
    recommendation: str
    inference_origin: Literal["model_inference"] = "model_inference"


class ClaimRecord(StrictModel):
    claim_id: str
    statement: str
    claim_type: ClaimType
    importance: Importance
    scope_qualifier: str
    claim_sources: list[SourceUnit]
    overall_relationship: Relationship
    confidence_score: float
    model_assessment: str
    alternative_interpretation: str
    evidence: list[EvidenceLink]
    issues: list[IssueFlag]
    inference_origin: Literal["model_inference"] = "model_inference"


class ClaimTraceReport(StrictModel):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    generated_at: datetime
    model: str
    document_name: str
    document_sha256: str
    paper_title: str
    paper_title_sources: list[SourceUnit]
    page_count: int
    source_unit_count: int
    claims: list[ClaimRecord]
    paper_level_issues: list[IssueFlag]
    global_limitations: list[str]
    analysis_warnings: list[str]
    provenance_statement: str
    sample_data: bool = False
