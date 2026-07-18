from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from claimtrace.analyzer import (
    AnalysisConfig,
    _hash_safety_identifier,
    analyze_document,
)
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


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs["text_format"]
        if schema is ClaimExtractionOutput:
            output = ClaimExtractionOutput(
                paper_title="Synthetic study",
                paper_title_source_ids=["p001-b001"],
                claims=[
                    ClaimCandidate(
                        claim_id="C1",
                        statement="The treatment reduced the measured endpoint.",
                        claim_type=ClaimType.DESCRIPTIVE,
                        importance=Importance.PRIMARY,
                        claim_source_ids=["p001-b001"],
                        scope_qualifier="The reported model and endpoint.",
                    )
                ],
                extraction_notes=[],
            )
        elif schema is ClaimAssessmentOutput:
            output = ClaimAssessmentOutput(
                assessments=[
                    ModelClaimAssessment(
                        claim_id="C1",
                        overall_relationship=Relationship.DIRECT,
                        confidence_score=0.92,
                        assessment_summary="The comparison directly measures the claim endpoint.",
                        alternative_interpretation="The finding may be model-specific.",
                        evidence=[
                            ModelEvidenceLink(
                                source_id="p001-b001",
                                relationship=Relationship.DIRECT,
                                supports_or_limits="Reports the treatment comparison.",
                                caveat="Single model.",
                            )
                        ],
                        issues=[],
                    )
                ],
                paper_level_issues=[],
                global_limitations=[],
            )
        else:  # pragma: no cover - defensive failure for schema drift
            raise AssertionError(f"Unexpected schema: {schema}")
        return SimpleNamespace(output_parsed=output, status="completed")


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def _document() -> PaperDocument:
    return PaperDocument(
        filename="synthetic.pdf",
        sha256="b" * 64,
        page_count=1,
        sources=[
            SourceUnit(
                source_id="p001-b001",
                page_number=1,
                block_index=1,
                kind=SourceKind.STATISTICAL_RESULT,
                section="Results",
                label="Statistical result",
                excerpt="Treatment reduced the endpoint versus vehicle (n = 10; p = 0.02).",
                bbox=[72.0, 100.0, 500.0, 140.0],
            )
        ],
    )


class AnalyzerPipelineTests(unittest.TestCase):
    def test_runs_two_structured_gpt_stages_and_hydrates_report(self) -> None:
        fake_client = _FakeClient()
        progress: list[tuple[str, float]] = []

        with patch("claimtrace.analyzer._client", return_value=fake_client):
            report = analyze_document(
                document=_document(),
                api_key="sk-test",
                config=AnalysisConfig(max_claims=5),
                progress=lambda message, fraction: progress.append((message, fraction)),
            )

        self.assertEqual(len(fake_client.responses.calls), 2)
        self.assertTrue(all(call["model"] == "gpt-5.6" for call in fake_client.responses.calls))
        self.assertTrue(all(call["store"] is False for call in fake_client.responses.calls))
        safety_identifiers = [
            str(call["safety_identifier"]) for call in fake_client.responses.calls
        ]
        self.assertEqual(len(set(safety_identifiers)), 1)
        self.assertEqual(len(safety_identifiers[0]), 64)
        int(safety_identifiers[0], 16)  # SHA-256 hexadecimal output.
        self.assertEqual(report.claims[0].evidence[0].source.page_number, 1)
        self.assertIn("p = 0.02", report.claims[0].evidence[0].source.excerpt)
        self.assertEqual(progress[-1], ("Claim-evidence report ready", 1.0))

    def test_hashes_supplied_session_identifier_deterministically(self) -> None:
        raw_identifier = "random-test-session-token"

        first = _hash_safety_identifier(raw_identifier)
        second = _hash_safety_identifier(raw_identifier)

        self.assertEqual(first, second)
        self.assertNotEqual(first, raw_identifier)
        self.assertEqual(len(first), 64)
        int(first, 16)


if __name__ == "__main__":
    unittest.main()
