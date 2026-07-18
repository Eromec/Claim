"""Allowlisted OpenAI models exposed by ClaimTrace."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    """Product metadata for one supported analysis model."""

    model_id: str
    label: str
    tier: str
    description: str


MODEL_OPTIONS = (
    ModelOption(
        model_id="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        tier="Best evidence judgment",
        description=(
            "Flagship quality for difficult papers, subtle caveats, and the most "
            "demanding claim–evidence audits."
        ),
    ),
    ModelOption(
        model_id="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        tier="Balanced",
        description=(
            "Strong analysis with a better quality, latency, and cost balance for "
            "routine papers."
        ),
    ),
    ModelOption(
        model_id="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        tier="Fastest",
        description=(
            "Efficient, high-volume screening when turnaround and API cost matter "
            "more than maximum nuance."
        ),
    ),
)

DEFAULT_MODEL = "gpt-5.6-sol"
MODEL_BY_ID = {option.model_id: option for option in MODEL_OPTIONS}


def get_model_option(model_id: str) -> ModelOption:
    """Return model metadata or reject an arbitrary model ID."""

    try:
        return MODEL_BY_ID[model_id]
    except KeyError as exc:
        supported = ", ".join(MODEL_BY_ID)
        raise ValueError(f"unsupported model; choose one of: {supported}") from exc
