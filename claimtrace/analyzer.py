"""Multi-model GPT-5.6 analysis using Responses and Structured Outputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
from typing import Callable, TypeVar

from pydantic import BaseModel

from .exceptions import AnalysisError, DocumentTooLargeError
from .model_catalog import DEFAULT_MODEL, get_model_option
from .models import (
    ClaimAssessmentOutput,
    ClaimExtractionOutput,
    ClaimTraceReport,
    PaperDocument,
)
from .pdf_parser import build_source_packet
from .prompts import ASSESSMENT_INSTRUCTIONS, EXTRACTION_INSTRUCTIONS
from .report_builder import build_report, validate_model_references


ProgressCallback = Callable[[str, float], None]
SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _hash_safety_identifier(session_identifier: str | None = None) -> str:
    """Return a non-identifying SHA-256 token for one analysis session.

    Streamlit supplies a random session token. Non-Streamlit callers receive a
    fresh random token for the duration of this ``analyze_document`` call.
    Neither the raw token nor any user/document attribute is sent to OpenAI.
    """

    raw_identifier = session_identifier or secrets.token_urlsafe(32)
    domain_separated = f"claimtrace-session-v1:{raw_identifier}".encode("utf-8")
    return hashlib.sha256(domain_separated).hexdigest()


@dataclass(frozen=True)
class AnalysisConfig:
    model: str = DEFAULT_MODEL
    reasoning_effort: str = "medium"
    max_claims: int = 8
    max_paper_characters: int = 500_000
    timeout_seconds: float = 240.0
    max_output_tokens: int = 20_000

    def __post_init__(self) -> None:
        get_model_option(self.model)
        if self.reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ValueError("unsupported reasoning effort")
        if not 1 <= self.max_claims <= 20:
            raise ValueError("max_claims must be between 1 and 20")


def _client(api_key: str, config: AnalysisConfig):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AnalysisError(
            "The openai package is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    return OpenAI(
        api_key=api_key,
        timeout=config.timeout_seconds,
        max_retries=2,
    )


def _parse_response(
    client,
    *,
    config: AnalysisConfig,
    instructions: str,
    input_text: str,
    schema: type[SchemaT],
    safety_identifier: str,
) -> SchemaT:
    """Call Responses.parse and turn API failures into stable domain errors."""

    model_label = get_model_option(config.model).label

    try:
        response = client.responses.parse(
            model=config.model,
            reasoning={"effort": config.reasoning_effort},
            instructions=instructions,
            input=input_text,
            text_format=schema,
            max_output_tokens=config.max_output_tokens,
            safety_identifier=safety_identifier,
            store=False,
        )
    except Exception as exc:
        name = type(exc).__name__
        safe_messages = {
            "AuthenticationError": "OpenAI authentication failed. Check your API key and project access.",
            "PermissionDeniedError": (
                f"This API project does not have access to {model_label}. "
                "Choose another model or check the project model permissions."
            ),
            "RateLimitError": "The OpenAI API rate limit was reached. Wait briefly and retry.",
            "APITimeoutError": (
                f"The {model_label} request timed out. Retry, choose a faster model, "
                "or use a shorter searchable PDF."
            ),
            "APIConnectionError": "ClaimTrace could not connect to the OpenAI API.",
            "BadRequestError": "The OpenAI API rejected the request. Check the paper size and model access.",
        }
        message = safe_messages.get(name, f"{model_label} analysis failed ({name}).")
        raise AnalysisError(message) from exc

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        status = getattr(response, "status", "unknown")
        incomplete = getattr(response, "incomplete_details", None)
        detail = getattr(incomplete, "reason", "no structured output") if incomplete else "no structured output"
        raise AnalysisError(
            f"{model_label} returned no validated structured output "
            f"(status={status}, reason={detail})."
        )
    if not isinstance(parsed, schema):
        # This is defensive: current SDK helpers already instantiate the schema.
        try:
            parsed = schema.model_validate(parsed)
        except Exception as exc:
            raise AnalysisError("The structured output did not validate locally.") from exc
    return parsed


def analyze_document(
    document: PaperDocument,
    api_key: str,
    config: AnalysisConfig | None = None,
    progress: ProgressCallback | None = None,
    session_identifier: str | None = None,
) -> ClaimTraceReport:
    """Run claim extraction, evidence assessment, and fail-closed hydration."""

    config = config or AnalysisConfig()
    model_option = get_model_option(config.model)
    if not api_key.strip():
        raise AnalysisError("An OpenAI API key is required for live analysis.")

    source_packet = build_source_packet(document)
    if len(source_packet) > config.max_paper_characters:
        raise DocumentTooLargeError(
            f"The extracted paper contains {len(source_packet):,} prompt characters, above "
            f"the configured {config.max_paper_characters:,} safety limit. Increase "
            "CLAIMTRACE_MAX_PAPER_CHARS deliberately or analyze a shorter main-text PDF."
        )

    notify = progress or (lambda _message, _fraction: None)
    client = _client(api_key, config)
    safety_identifier = _hash_safety_identifier(session_identifier)

    notify(f"Extracting major claims with {model_option.label}", 0.15)
    extraction_input = f"""
<task_config>
maximum_claims={config.max_claims}
document_name={document.filename}
page_count={document.page_count}
</task_config>

<paper_source_registry>
{source_packet}
</paper_source_registry>
""".strip()
    extraction = _parse_response(
        client,
        config=config,
        instructions=EXTRACTION_INSTRUCTIONS,
        input_text=extraction_input,
        schema=ClaimExtractionOutput,
        safety_identifier=safety_identifier,
    )
    if len(extraction.claims) > config.max_claims:
        raise AnalysisError(
            f"{model_option.label} returned more claims than the configured maximum; "
            "the report was rejected."
        )
    validate_model_references(document, extraction)

    if extraction.claims:
        notify("Linking claims to evidence and auditing limitations", 0.55)
        claims_json = extraction.model_dump_json(indent=2)
        assessment_input = f"""
<candidate_claims>
{claims_json}
</candidate_claims>

<paper_source_registry>
{source_packet}
</paper_source_registry>
""".strip()
        assessment = _parse_response(
            client,
            config=config,
            instructions=ASSESSMENT_INSTRUCTIONS,
            input_text=assessment_input,
            schema=ClaimAssessmentOutput,
            safety_identifier=safety_identifier,
        )
    else:
        assessment = ClaimAssessmentOutput(
            assessments=[],
            paper_level_issues=[],
            global_limitations=[
                "The parsed text did not contain a major scientific claim that could be source-anchored."
            ],
        )

    notify("Validating every source reference locally", 0.88)
    report = build_report(document, extraction, assessment, config.model)
    notify("Claim-evidence report ready", 1.0)
    return report
