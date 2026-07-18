from __future__ import annotations

import unittest

from pydantic import ValidationError

from claimtrace.exceptions import TraceabilityError
from claimtrace.models import (
    ClaimAssessmentOutput,
    ClaimCandidate,
    ClaimExtractionOutput,
    ClaimType,
    Importance,
    ModelClaimAssessment,
    ModelEvidenceLink,
    PaperDocument,
    Relationship,
    SourceKind,
    SourceUnit,
)
from claimtrace.report_builder import build_report, validate_model_references
from claimtrace.sample import load_sample_report


def _source() -> SourceUnit:
    return SourceUnit(
        source_id="p001-b001",
        page_number=1,
        block_index=1,
        kind=SourceKind.STATISTICAL_RESULT,
        section="Results",
        label="Statistical result",
        excerpt="The local parser preserved this exact sentence (n = 10; p = 0.02).",
        bbox=[72.0, 100.0, 500.0, 140.0],
    )


def _document() -> PaperDocument:
    return PaperDocument(
        filename="paper.pdf",
        sha256="a" * 64,
        page_count=1,
        sources=[_source()],
    )


def _extraction(source_id: str = "p001-b001") -> ClaimExtractionOutput:
    return ClaimExtractionOutput(
        paper_title="Synthetic title",
        paper_title_source_ids=[source_id],
        claims=[
            ClaimCandidate(
                claim_id="C1",
                statement="Treatment is associated with the measured endpoint.",
                claim_type=ClaimType.ASSOCIATION,
                importance=Importance.PRIMARY,
                claim_source_ids=[source_id],
                scope_qualifier="The reported experimental system.",
            )
        ],
        extraction_notes=[],
    )


def _assessment(
    source_id: str = "p001-b001",
    *,
    overall_relationship: Relationship = Relationship.DIRECT,
    evidence_relationships: list[Relationship] | None = None,
) -> ClaimAssessmentOutput:
    relationships = (
        [Relationship.DIRECT]
        if evidence_relationships is None
        else evidence_relationships
    )
    return ClaimAssessmentOutput(
        assessments=[
            ModelClaimAssessment(
                claim_id="C1",
                overall_relationship=overall_relationship,
                confidence_score=0.9,
                assessment_summary="The reported comparison bears directly on the claim.",
                alternative_interpretation="The result may be system-specific.",
                evidence=[
                    ModelEvidenceLink(
                        source_id=source_id,
                        relationship=relationship,
                        supports_or_limits="Directly reports the comparison.",
                        caveat="Single experiment.",
                    )
                    for relationship in relationships
                ],
                issues=[],
            )
        ],
        paper_level_issues=[],
        global_limitations=[],
    )


class StructuredOutputTests(unittest.TestCase):
    def test_validates_structured_output_models(self) -> None:
        extraction = _extraction()
        assessment = _assessment()

        self.assertEqual(extraction.claims[0].claim_id, "C1")
        self.assertEqual(assessment.assessments[0].overall_relationship, Relationship.DIRECT)

    def test_rejects_out_of_range_confidence(self) -> None:
        with self.assertRaises(ValidationError):
            ModelClaimAssessment(
                claim_id="C1",
                overall_relationship=Relationship.DIRECT,
                confidence_score=1.5,
                assessment_summary="Summary",
                alternative_interpretation="Alternative",
                evidence=[],
                issues=[],
            )

    def test_rejects_duplicate_claim_ids(self) -> None:
        claim = _extraction().claims[0]
        with self.assertRaises(ValidationError):
            ClaimExtractionOutput(
                paper_title="Title",
                paper_title_source_ids=[],
                claims=[claim, claim.model_copy(deep=True)],
                extraction_notes=[],
            )

    def test_rejects_model_source_ids_absent_from_pdf(self) -> None:
        document = _document()
        extraction = _extraction()
        assessment = _assessment("p999-b999")

        with self.assertRaisesRegex(TraceabilityError, "absent from the paper"):
            validate_model_references(document, extraction, assessment)

    def test_rejects_unsupported_claim_with_evidence(self) -> None:
        assessment = _assessment(overall_relationship=Relationship.UNSUPPORTED)

        with self.assertRaisesRegex(
            TraceabilityError,
            r"Claim C1 is classified as unsupported but contains evidence links",
        ):
            validate_model_references(_document(), _extraction(), assessment)

    def test_rejects_supported_claim_without_evidence(self) -> None:
        assessment = _assessment(
            overall_relationship=Relationship.PARTIAL,
            evidence_relationships=[],
        )

        with self.assertRaisesRegex(
            TraceabilityError,
            r"Claim C1 is classified as partial but has no evidence links",
        ):
            validate_model_references(_document(), _extraction(), assessment)

    def test_rejects_direct_claim_without_direct_link(self) -> None:
        assessment = _assessment(
            overall_relationship=Relationship.DIRECT,
            evidence_relationships=[Relationship.INDIRECT],
        )

        with self.assertRaisesRegex(
            TraceabilityError,
            r"Claim C1 is classified overall as direct but has no direct evidence link",
        ):
            validate_model_references(_document(), _extraction(), assessment)

    def test_rejects_duplicate_evidence_links(self) -> None:
        assessment = _assessment(
            evidence_relationships=[Relationship.DIRECT, Relationship.DIRECT]
        )

        with self.assertRaisesRegex(
            TraceabilityError,
            r"Claim C1 links the same evidence source more than once: p001-b001",
        ):
            validate_model_references(_document(), _extraction(), assessment)

    def test_rejects_missing_assessment_claim_id(self) -> None:
        assessment = ClaimAssessmentOutput(
            assessments=[],
            paper_level_issues=[],
            global_limitations=[],
        )

        with self.assertRaisesRegex(TraceabilityError, r"missing claim ID\(s\): C1"):
            validate_model_references(_document(), _extraction(), assessment)

    def test_rejects_duplicated_assessment_claim_id(self) -> None:
        assessment = _assessment()
        item = assessment.assessments[0]
        duplicate = ClaimAssessmentOutput(
            assessments=[item, item.model_copy(deep=True)],
            paper_level_issues=[],
            global_limitations=[],
        )

        with self.assertRaisesRegex(TraceabilityError, r"duplicated claim ID\(s\): C1"):
            validate_model_references(_document(), _extraction(), duplicate)

    def test_rejects_unexpected_assessment_claim_id(self) -> None:
        assessment = _assessment()
        unexpected_item = assessment.assessments[0].model_copy(update={"claim_id": "C2"})
        unexpected = ClaimAssessmentOutput(
            assessments=[unexpected_item],
            paper_level_issues=[],
            global_limitations=[],
        )

        with self.assertRaisesRegex(TraceabilityError, r"unexpected claim ID\(s\): C2"):
            validate_model_references(_document(), _extraction(), unexpected)

    def test_hydrates_excerpt_only_from_local_source_registry(self) -> None:
        report = build_report(
            _document(),
            _extraction(),
            _assessment(),
            model="gpt-5.6",
        )

        hydrated = report.claims[0].evidence[0].source
        self.assertEqual(hydrated.excerpt, _source().excerpt)
        self.assertEqual(hydrated.page_number, 1)
        self.assertEqual(hydrated.content_origin, "paper")
        self.assertEqual(report.claims[0].inference_origin, "model_inference")
        self.assertEqual(report.schema_version, "1.1")

    def test_bundled_sample_output_matches_report_schema(self) -> None:
        report = load_sample_report()

        self.assertTrue(report.sample_data)
        self.assertEqual(report.schema_version, "1.1")
        self.assertEqual(report.model, "gpt-5.6")
        self.assertGreaterEqual(len(report.claims), 3)


if __name__ == "__main__":
    unittest.main()
