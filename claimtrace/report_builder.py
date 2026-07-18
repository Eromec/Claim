"""Fail-closed provenance validation and local report hydration."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from .exceptions import TraceabilityError
from .models import (
    ClaimAssessmentOutput,
    ClaimExtractionOutput,
    ClaimRecord,
    ClaimTraceReport,
    EvidenceLink,
    IssueFlag,
    ModelClaimAssessment,
    ModelIssue,
    PaperDocument,
    Relationship,
    SourceUnit,
)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _format_ids(source_ids: Iterable[str]) -> str:
    return ", ".join(sorted(set(source_ids)))


def _require_known_sources(
    source_ids: Iterable[str],
    known_ids: set[str],
    *,
    context: str,
) -> None:
    unknown = sorted(set(source_ids) - known_ids)
    if unknown:
        raise TraceabilityError(
            f"{context} references unknown source ID(s) absent from the paper registry: "
            f"{_format_ids(unknown)}. The complete report was rejected; retry the analysis."
        )


def _validate_assessment_claim_ids(
    extraction: ClaimExtractionOutput,
    assessment: ClaimAssessmentOutput,
) -> None:
    expected = [claim.claim_id for claim in extraction.claims]
    actual = [item.claim_id for item in assessment.assessments]
    duplicates = sorted(
        claim_id for claim_id, count in Counter(actual).items() if count > 1
    )
    if duplicates:
        raise TraceabilityError(
            f"Assessment contains duplicated claim ID(s): {_format_ids(duplicates)}. "
            "The complete report was rejected; retry the analysis."
        )

    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    if missing or unexpected:
        problems: list[str] = []
        if missing:
            problems.append(f"missing claim ID(s): {_format_ids(missing)}")
        if unexpected:
            problems.append(f"unexpected claim ID(s): {_format_ids(unexpected)}")
        raise TraceabilityError(
            "Assessment claim registry mismatch (" + "; ".join(problems) + "). "
            "The complete report was rejected; retry the analysis."
        )


def _validate_claim_evidence_semantics(item: ModelClaimAssessment) -> None:
    claim_id = item.claim_id
    evidence_source_ids = [link.source_id for link in item.evidence]
    duplicates = sorted(
        source_id
        for source_id, count in Counter(evidence_source_ids).items()
        if count > 1
    )
    if duplicates:
        raise TraceabilityError(
            f"Claim {claim_id} links the same evidence source more than once: "
            f"{_format_ids(duplicates)}. The complete report was rejected; retry the analysis."
        )

    if item.overall_relationship is Relationship.UNSUPPORTED:
        if item.evidence:
            raise TraceabilityError(
                f"Claim {claim_id} is classified as unsupported but contains evidence links. "
                "The complete report was rejected; retry the analysis."
            )
        return

    supported_relationships = {
        Relationship.DIRECT,
        Relationship.INDIRECT,
        Relationship.PARTIAL,
    }
    if item.overall_relationship in supported_relationships and not item.evidence:
        raise TraceabilityError(
            f"Claim {claim_id} is classified as {item.overall_relationship.value} but has no "
            "evidence links. The complete report was rejected; retry the analysis."
        )

    if item.overall_relationship is Relationship.DIRECT and not any(
        link.relationship is Relationship.DIRECT for link in item.evidence
    ):
        raise TraceabilityError(
            f"Claim {claim_id} is classified overall as direct but has no direct evidence "
            "link. The complete report was rejected; retry the analysis."
        )


def validate_model_references(
    document: PaperDocument,
    extraction: ClaimExtractionOutput,
    assessment: ClaimAssessmentOutput | None = None,
) -> None:
    """Reject unknown references and cross-field semantic inconsistencies."""

    known_ids = set(document.source_index)
    _require_known_sources(
        extraction.paper_title_source_ids,
        known_ids,
        context="Paper title",
    )
    for claim in extraction.claims:
        _require_known_sources(
            claim.claim_source_ids,
            known_ids,
            context=f"Claim {claim.claim_id} statement",
        )

    if assessment is not None:
        _validate_assessment_claim_ids(extraction, assessment)
        for item in assessment.assessments:
            _require_known_sources(
                (link.source_id for link in item.evidence),
                known_ids,
                context=f"Claim {item.claim_id} evidence",
            )
            _validate_claim_evidence_semantics(item)
            for issue_index, issue in enumerate(item.issues, start=1):
                _require_known_sources(
                    issue.source_ids,
                    known_ids,
                    context=f"Claim {item.claim_id} audit flag {issue_index}",
                )
        for issue_index, issue in enumerate(assessment.paper_level_issues, start=1):
            _require_known_sources(
                issue.source_ids,
                known_ids,
                context=f"Paper-level audit flag {issue_index}",
            )


def _hydrate_sources(source_ids: Iterable[str], index: dict[str, SourceUnit]) -> list[SourceUnit]:
    return [index[source_id] for source_id in _unique(source_ids)]


def _hydrate_issue(issue: ModelIssue, index: dict[str, SourceUnit]) -> IssueFlag:
    return IssueFlag(
        category=issue.category,
        severity=issue.severity,
        description=issue.description,
        why_it_matters=issue.why_it_matters,
        sources=_hydrate_sources(issue.source_ids, index),
        recommendation=issue.recommendation,
    )


def build_report(
    document: PaperDocument,
    extraction: ClaimExtractionOutput,
    assessment: ClaimAssessmentOutput,
    model: str,
    *,
    sample_data: bool = False,
) -> ClaimTraceReport:
    """Validate IDs, then hydrate page/excerpt data from local parser output."""

    validate_model_references(document, extraction, assessment)
    index = document.source_index
    assessment_by_id = {item.claim_id: item for item in assessment.assessments}
    claims: list[ClaimRecord] = []

    for claim in extraction.claims:
        item = assessment_by_id[claim.claim_id]
        evidence = [
            EvidenceLink(
                source=index[link.source_id],
                relationship=link.relationship,
                model_inference=link.supports_or_limits,
                caveat=link.caveat,
            )
            for link in item.evidence
        ]
        claims.append(
            ClaimRecord(
                claim_id=claim.claim_id,
                statement=claim.statement,
                claim_type=claim.claim_type,
                importance=claim.importance,
                scope_qualifier=claim.scope_qualifier,
                claim_sources=_hydrate_sources(claim.claim_source_ids, index),
                overall_relationship=item.overall_relationship,
                confidence_score=item.confidence_score,
                model_assessment=item.assessment_summary,
                alternative_interpretation=item.alternative_interpretation,
                evidence=evidence,
                issues=[_hydrate_issue(issue, index) for issue in item.issues],
            )
        )

    warnings = list(extraction.extraction_notes)
    if not extraction.claims:
        warnings.append("No major scientific claim could be anchored to the parsed paper text.")

    return ClaimTraceReport(
        generated_at=datetime.now(timezone.utc),
        model=model,
        document_name=document.filename,
        document_sha256=document.sha256,
        paper_title=extraction.paper_title,
        paper_title_sources=_hydrate_sources(extraction.paper_title_source_ids, index),
        page_count=document.page_count,
        source_unit_count=len(document.sources),
        claims=claims,
        paper_level_issues=[
            _hydrate_issue(issue, index) for issue in assessment.paper_level_issues
        ],
        global_limitations=assessment.global_limitations,
        analysis_warnings=warnings,
        provenance_statement=(
            "Page numbers and excerpts were extracted locally from the uploaded PDF. "
            "GPT-5.6 returned only source IDs plus analytical judgments; every ID was "
            "validated before local hydration. Relationship labels, issue flags, "
            "summaries, confidence scores, and alternative interpretations are model inference."
        ),
        sample_data=sample_data,
    )
