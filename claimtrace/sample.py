"""Load the bundled synthetic report used for zero-cost product exploration."""

from pathlib import Path

from .models import ClaimTraceReport


def load_sample_report() -> ClaimTraceReport:
    sample_path = Path(__file__).resolve().parent.parent / "sample_outputs" / "demo_report.json"
    return ClaimTraceReport.model_validate_json(sample_path.read_text(encoding="utf-8"))

