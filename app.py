"""ClaimTrace Streamlit application."""

from __future__ import annotations

import html
import os
from pathlib import Path
import secrets

import streamlit as st
from dotenv import load_dotenv

from claimtrace.analyzer import AnalysisConfig, analyze_document
from claimtrace.exceptions import ClaimTraceError
from claimtrace.models import (
    ClaimRecord,
    ClaimTraceReport,
    EvidenceLink,
    IssueCategory,
    IssueFlag,
    Relationship,
    SourceUnit,
)
from claimtrace.pdf_parser import parse_pdf
from claimtrace.sample import load_sample_report


load_dotenv()

APP_ROOT = Path(__file__).resolve().parent

RELATIONSHIP_LABELS = {
    Relationship.DIRECT: "Direct",
    Relationship.INDIRECT: "Indirect",
    Relationship.PARTIAL: "Partial",
    Relationship.UNSUPPORTED: "Unsupported",
}

RELATIONSHIP_CLASSES = {
    Relationship.DIRECT: "ct-direct",
    Relationship.INDIRECT: "ct-indirect",
    Relationship.PARTIAL: "ct-partial",
    Relationship.UNSUPPORTED: "ct-unsupported",
}

ISSUE_LABELS = {
    IssueCategory.OVERCLAIMING: "Overclaiming",
    IssueCategory.MISSING_CONTROL: "Missing control",
    IssueCategory.CAUSAL_OVERINTERPRETATION: "Causal overinterpretation",
    IssueCategory.REPRODUCIBILITY: "Reproducibility",
    IssueCategory.STATISTICAL_REPORTING: "Statistical reporting",
    IssueCategory.GENERALIZABILITY: "Generalizability",
}

SOURCE_LABELS = {
    "paragraph": "Paragraph",
    "figure": "Figure caption",
    "table": "Table text",
    "method": "Method",
    "statistical_result": "Statistical result",
}


def _configured_secret(name: str) -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def _integer_setting(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_setting(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _streamlit_session_identifier() -> str:
    """Return a random token stable only for the current Streamlit session."""

    state_key = "_claimtrace_random_session_identifier"
    if state_key not in st.session_state:
        st.session_state[state_key] = secrets.token_urlsafe(32)
    return str(st.session_state[state_key])


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ct-ink: #17212b;
          --ct-muted: #64748b;
          --ct-line: #dce5e8;
          --ct-teal: #0f766e;
          --ct-navy: #15324a;
          --ct-paper: #f7faf9;
        }
        .stApp { background: linear-gradient(180deg, #f4f8f7 0, #ffffff 380px); }
        .block-container { max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }
        .ct-hero {
          border: 1px solid #d7e6e3;
          border-radius: 22px;
          padding: 2.1rem 2.25rem;
          background:
            radial-gradient(circle at 88% 8%, rgba(45, 212, 191, .16), transparent 32%),
            linear-gradient(135deg, rgba(255,255,255,.98), rgba(240,249,247,.96));
          box-shadow: 0 18px 48px rgba(21, 50, 74, .08);
          margin-bottom: 1.5rem;
        }
        .ct-kicker { color: #0f766e; font-size: .78rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
        .ct-title { color: #15324a; font-size: clamp(2.25rem, 5vw, 4.35rem); font-weight: 780; letter-spacing: -.055em; line-height: .98; margin: .5rem 0 .8rem; }
        .ct-subtitle { color: #526575; font-size: 1.05rem; max-width: 760px; line-height: 1.65; margin: 0; }
        .ct-badge {
          display: inline-block; padding: .24rem .62rem; border-radius: 999px;
          font-size: .72rem; font-weight: 800; letter-spacing: .02em; margin-right: .35rem;
        }
        .ct-direct { color: #065f46; background: #d1fae5; border: 1px solid #a7f3d0; }
        .ct-indirect { color: #1e40af; background: #dbeafe; border: 1px solid #bfdbfe; }
        .ct-partial { color: #92400e; background: #fef3c7; border: 1px solid #fde68a; }
        .ct-unsupported { color: #991b1b; background: #fee2e2; border: 1px solid #fecaca; }
        .ct-paper-tag { color: #155e75; background: #cffafe; border: 1px solid #a5f3fc; }
        .ct-model-tag { color: #5b21b6; background: #ede9fe; border: 1px solid #ddd6fe; }
        .ct-claim {
          font-size: 1.12rem; line-height: 1.58; color: #17212b; font-weight: 650;
          border-left: 4px solid #0f766e; padding: .6rem .9rem; background: #f8fbfa;
          border-radius: 0 10px 10px 0; margin: .5rem 0 1rem;
        }
        .ct-eyebrow { color: #64748b; text-transform: uppercase; letter-spacing: .1em; font-size: .68rem; font-weight: 800; }
        .ct-divider { height: 1px; background: linear-gradient(90deg, transparent, #cbd9dc, transparent); margin: 2rem 0; }
        [data-testid="stMetric"] { background: rgba(255,255,255,.78); border: 1px solid #e1e9eb; padding: .8rem 1rem; border-radius: 14px; }
        [data-testid="stFileUploader"] { border: 1px dashed #9dbab5; border-radius: 16px; padding: .35rem; background: rgba(255,255,255,.62); }
        div[data-testid="stExpander"] { border-color: #dbe5e7; border-radius: 14px; background: rgba(255,255,255,.72); }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _badge(text: str, css_class: str) -> str:
    return f'<span class="ct-badge {css_class}">{html.escape(text)}</span>'


def _source_title(source: SourceUnit) -> str:
    kind = SOURCE_LABELS.get(source.kind.value, source.kind.value.replace("_", " ").title())
    return f"Page {source.page_number} · {kind} · {source.source_id}"


def _render_source(
    source: SourceUnit,
    *,
    inference: str = "",
    caveat: str = "",
    relationship: Relationship | None = None,
    expanded: bool = False,
) -> None:
    with st.expander(_source_title(source), expanded=expanded):
        st.markdown(
            _badge("Paper content", "ct-paper-tag")
            + f" <span style='color:#64748b;font-size:.8rem'>Section: {html.escape(source.section)}</span>",
            unsafe_allow_html=True,
        )
        # st.code prevents paper text from being interpreted as Markdown or HTML.
        st.code(source.excerpt, language=None)
        if inference or caveat:
            badges = _badge("Model inference", "ct-model-tag")
            if relationship is not None:
                badges += _badge(
                    RELATIONSHIP_LABELS[relationship], RELATIONSHIP_CLASSES[relationship]
                )
            st.markdown(badges, unsafe_allow_html=True)
        if inference:
            st.write(inference)
        if caveat:
            st.caption(f"Caveat: {caveat}")


def _render_issue(issue: IssueFlag) -> None:
    with st.container(border=True):
        severity_color = {"high": "🔴", "medium": "🟠", "low": "🟡"}[issue.severity.value]
        st.markdown(
            f"**{severity_color} {ISSUE_LABELS[issue.category]}** · {issue.severity.value.title()} severity"
        )
        st.markdown(_badge("Model inference", "ct-model-tag"), unsafe_allow_html=True)
        st.write(issue.description)
        st.caption(f"Why it matters: {issue.why_it_matters}")
        st.markdown(f"**Suggested check:** {issue.recommendation}")
        if issue.sources:
            st.caption("Paper spans relevant to this inference")
            for source in issue.sources:
                _render_source(source)
        else:
            st.caption(
                "Absence-based flag: no unrelated paper span is cited as proof. "
                "Interpret this as ‘not reported in the analyzed paper.’"
            )


def _render_claim(claim: ClaimRecord, *, expanded: bool = False) -> None:
    label = RELATIONSHIP_LABELS[claim.overall_relationship]
    # A bordered container keeps source expanders legal; Streamlit does not allow
    # an expander to be nested inside another expander.
    with st.container(border=True):
        st.markdown(f"### {claim.claim_id} · {html.escape(label)}")
        st.markdown(
            _badge(label, RELATIONSHIP_CLASSES[claim.overall_relationship])
            + _badge("AI-extracted claim", "ct-model-tag"),
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="ct-claim">{html.escape(claim.statement)}</div>',
            unsafe_allow_html=True,
        )
        metadata_cols = st.columns(3)
        metadata_cols[0].caption(f"Claim type · {claim.claim_type.value.replace('_', ' ').title()}")
        metadata_cols[1].caption(f"Importance · {claim.importance.value.title()}")
        metadata_cols[2].caption(f"Model confidence · {claim.confidence_score:.0%}")
        st.caption(f"Stated scope: {claim.scope_qualifier}")

        assessment_tab, evidence_tab, risks_tab = st.tabs(
            ["Assessment", f"Evidence ({len(claim.evidence)})", f"Flags ({len(claim.issues)})"]
        )

        with assessment_tab:
            st.markdown(_badge("Model inference", "ct-model-tag"), unsafe_allow_html=True)
            st.write(claim.model_assessment)
            if claim.alternative_interpretation:
                st.info(f"Alternative interpretation: {claim.alternative_interpretation}")
            st.markdown("**Where the authors state the claim**")
            for source in claim.claim_sources:
                _render_source(source)

        with evidence_tab:
            if not claim.evidence:
                st.warning(
                    "No supporting paper source was linked. The unsupported label is model "
                    "inference; ClaimTrace does not create a citation to fill the gap."
                )
            for index, evidence in enumerate(claim.evidence):
                _render_source(
                    evidence.source,
                    inference=evidence.model_inference,
                    caveat=evidence.caveat,
                    relationship=evidence.relationship,
                    expanded=index == 0,
                )

        with risks_tab:
            if not claim.issues:
                st.success("No specific claim-level issue was flagged.")
            for issue in claim.issues:
                _render_issue(issue)


def _evidence_rows(report: ClaimTraceReport) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for claim in report.claims:
        for source in claim.claim_sources:
            rows.append(
                {
                    "Claim": claim.claim_id,
                    "Link": "Claim location",
                    "Relationship": "—",
                    "Page": source.page_number,
                    "Type": source.kind.value,
                    "Source ID": source.source_id,
                    "Paper excerpt": source.excerpt,
                }
            )
        for evidence in claim.evidence:
            rows.append(
                {
                    "Claim": claim.claim_id,
                    "Link": "Evidence",
                    "Relationship": evidence.relationship.value,
                    "Page": evidence.source.page_number,
                    "Type": evidence.source.kind.value,
                    "Source ID": evidence.source.source_id,
                    "Paper excerpt": evidence.source.excerpt,
                }
            )
    return rows


def _render_report(report: ClaimTraceReport) -> None:
    if report.sample_data:
        st.warning(
            "Synthetic demo loaded — every paper excerpt and numerical result below is fictional "
            "and exists only to demonstrate ClaimTrace."
        )

    st.markdown('<div class="ct-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="ct-eyebrow">Interactive claim–evidence report</div>', unsafe_allow_html=True)
    st.header(report.paper_title)
    st.markdown(_badge("AI-extracted title", "ct-model-tag"), unsafe_allow_html=True)
    st.caption(
        f"{report.document_name} · {report.page_count} pages · "
        f"{report.source_unit_count} indexed source spans · model {report.model}"
    )
    if report.paper_title_sources:
        with st.expander("Inspect title provenance"):
            for source in report.paper_title_sources:
                st.caption(_source_title(source))
                st.markdown(_badge("Paper content", "ct-paper-tag"), unsafe_allow_html=True)
                st.code(source.excerpt, language=None)

    total_claims = len(report.claims)
    supported = sum(
        claim.overall_relationship in {Relationship.DIRECT, Relationship.INDIRECT}
        for claim in report.claims
    )
    review_needed = sum(
        claim.overall_relationship in {Relationship.PARTIAL, Relationship.UNSUPPORTED}
        for claim in report.claims
    )
    issue_count = sum(len(claim.issues) for claim in report.claims) + len(report.paper_level_issues)
    high_risk_count = sum(
        issue.severity.value == "high"
        for claim in report.claims
        for issue in claim.issues
    ) + sum(issue.severity.value == "high" for issue in report.paper_level_issues)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Major claims", total_claims)
    metric_cols[1].metric("Direct / indirect", supported)
    metric_cols[2].metric("Needs review", review_needed)
    metric_cols[3].metric("Audit flags", issue_count, delta=f"{high_risk_count} high", delta_color="inverse")

    filter_cols = st.columns([1.1, 1.3, 1.4])
    selected_relationships = filter_cols[0].multiselect(
        "Evidence relationship",
        options=list(Relationship),
        default=list(Relationship),
        format_func=lambda value: RELATIONSHIP_LABELS[value],
    )
    selected_issues = filter_cols[1].multiselect(
        "Flag category",
        options=list(IssueCategory),
        format_func=lambda value: ISSUE_LABELS[value],
        placeholder="All categories",
    )
    search_text = filter_cols[2].text_input("Search claims", placeholder="e.g., survival, mechanism")

    visible_claims = []
    for claim in report.claims:
        if claim.overall_relationship not in selected_relationships:
            continue
        if selected_issues and not any(issue.category in selected_issues for issue in claim.issues):
            continue
        if search_text and search_text.lower() not in (
            f"{claim.statement} {claim.scope_qualifier} {claim.model_assessment}"
        ).lower():
            continue
        visible_claims.append(claim)

    claims_tab, map_tab, audit_tab, json_tab = st.tabs(
        ["Claims", "Evidence map", "Paper-level audit", "Structured JSON"]
    )

    with claims_tab:
        if not visible_claims:
            st.info("No claims match the current filters.")
        for index, claim in enumerate(visible_claims):
            _render_claim(claim, expanded=index == 0)

    with map_tab:
        st.caption(
            "All page numbers and excerpts in this table come from local PDF parsing. "
            "Relationship labels come from GPT-5.6."
        )
        rows = _evidence_rows(report)
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("No source links are available.")

    with audit_tab:
        st.markdown(_badge("Model inference", "ct-model-tag"), unsafe_allow_html=True)
        if report.paper_level_issues:
            st.subheader("Paper-level flags")
            for issue in report.paper_level_issues:
                _render_issue(issue)
        else:
            st.success("No paper-level issue was specifically flagged.")

        if report.global_limitations:
            st.subheader("Analysis limitations")
            for limitation in report.global_limitations:
                st.markdown(f"- {limitation}")
        if report.analysis_warnings:
            st.subheader("Warnings and extraction notes")
            for warning in report.analysis_warnings:
                st.warning(warning)
        st.info(report.provenance_statement)

    report_json = report.model_dump_json(indent=2)
    with json_tab:
        st.caption("Validated against the ClaimTrace Pydantic report schema.")
        st.download_button(
            "Download report JSON",
            data=report_json,
            file_name=f"{Path(report.document_name).stem}_claimtrace.json",
            mime="application/json",
            width="stretch",
        )
        st.code(report_json, language="json")


def main() -> None:
    st.set_page_config(
        page_title="ClaimTrace · Biomedical evidence audit",
        page_icon="🧬",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()
    session_identifier = _streamlit_session_identifier()

    st.markdown(
        """
        <section class="ct-hero">
          <div class="ct-kicker">Biomedical claim provenance</div>
          <div class="ct-title">ClaimTrace</div>
          <p class="ct-subtitle">
            Turn a research paper into an auditable map of claims, supporting passages,
            figures, tables, methods, statistics, and evidence gaps — with every paper
            excerpt anchored to its original PDF page.
          </p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("## ClaimTrace")
        st.caption("Trace the claim. Inspect the evidence.")
        st.divider()
        st.markdown("**Analysis engine**")
        st.code("gpt-5.6", language=None)
        configured_key = _configured_secret("OPENAI_API_KEY")
        entered_key = st.text_input(
            "OpenAI API key",
            type="password",
            placeholder="Configured on server" if configured_key else "sk-…",
            help="Used for this server-side analysis. It is not written to the report.",
        )
        api_key = entered_key.strip() or configured_key
        if api_key:
            st.success("API key available", icon="✓")
        else:
            st.caption("No key? Explore the synthetic sample without an API call.")

        max_claims = st.slider("Maximum major claims", 3, 12, 8)
        reasoning_effort = st.select_slider(
            "Reasoning effort",
            options=["none", "low", "medium", "high"],
            value="medium",
            help="Medium is the quality-oriented default for evidence auditing.",
        )
        st.divider()
        st.markdown("**Built-in guardrails**")
        st.caption(
            "• Page-preserving local extraction\n\n"
            "• Source-ID-only model citations\n\n"
            "• Fail-closed provenance validation\n\n"
            "• Paper text separated from model inference"
        )

    upload_col, action_col = st.columns([1.65, 1])
    with upload_col:
        uploaded_file = st.file_uploader(
            "Upload a searchable biomedical paper",
            type=["pdf"],
            help="Text-based PDFs work best. Scanned PDFs must be OCRed first.",
        )
        if uploaded_file:
            st.caption(f"Selected: {uploaded_file.name} · {uploaded_file.size / 1_048_576:.2f} MB")

    with action_col:
        st.markdown("#### Run")
        analyze_clicked = st.button(
            "Analyze with GPT-5.6",
            type="primary",
            width="stretch",
        )
        sample_clicked = st.button("Load synthetic sample", width="stretch")
        if "report" in st.session_state:
            if st.button("Clear current report", width="stretch"):
                del st.session_state["report"]
                st.rerun()

    st.caption(
        "Live mode sends extracted paper text to the OpenAI API. ClaimTrace does not send "
        "the PDF binary, and `store=False` is set on Responses API calls."
    )

    if sample_clicked:
        try:
            sample = load_sample_report()
            st.session_state["report"] = sample.model_dump(mode="json")
            st.rerun()
        except Exception as exc:
            st.error(f"The bundled sample could not be loaded ({type(exc).__name__}).")

    if analyze_clicked:
        if uploaded_file is None:
            st.error("Upload a searchable PDF before starting live analysis.")
        elif not api_key:
            st.error("Enter an OpenAI API key, or load the synthetic sample instead.")
        else:
            progress_bar = st.progress(0, text="Preparing the paper")
            try:
                with st.status("Analyzing paper", expanded=True) as status:
                    status.write("Parsing PDF pages and building the immutable source registry")
                    document = parse_pdf(uploaded_file.getvalue(), uploaded_file.name)
                    progress_bar.progress(8, text="PDF parsed and page anchors preserved")
                    status.write(
                        f"Indexed {len(document.sources)} source spans across "
                        f"{document.page_count} pages"
                    )

                    seen_messages: set[str] = set()

                    def on_progress(message: str, fraction: float) -> None:
                        progress_bar.progress(int(fraction * 100), text=message)
                        if message not in seen_messages:
                            status.write(message)
                            seen_messages.add(message)

                    config = AnalysisConfig(
                        reasoning_effort=reasoning_effort,
                        max_claims=max_claims,
                        max_paper_characters=_integer_setting(
                            "CLAIMTRACE_MAX_PAPER_CHARS", 500_000
                        ),
                        timeout_seconds=_float_setting(
                            "CLAIMTRACE_API_TIMEOUT_SECONDS", 240.0
                        ),
                    )
                    report = analyze_document(
                        document=document,
                        api_key=api_key,
                        config=config,
                        progress=on_progress,
                        session_identifier=session_identifier,
                    )
                    st.session_state["report"] = report.model_dump(mode="json")
                    status.update(label="Analysis complete", state="complete", expanded=False)
                    progress_bar.progress(100, text="Claim-evidence report ready")
            except ClaimTraceError as exc:
                progress_bar.empty()
                st.error(str(exc))
            except Exception as exc:
                progress_bar.empty()
                st.error(
                    "ClaimTrace encountered an unexpected error and did not produce a report. "
                    f"Error type: {type(exc).__name__}."
                )

    payload = st.session_state.get("report")
    if payload:
        try:
            _render_report(ClaimTraceReport.model_validate(payload))
        except Exception as exc:
            st.error(f"The stored report failed local schema validation ({type(exc).__name__}).")


if __name__ == "__main__":
    main()
