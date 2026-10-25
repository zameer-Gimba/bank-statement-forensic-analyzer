"""
Streamlit dashboard for the Bank Statement Forensic Analyzer.

Run locally:
    streamlit run streamlit_app.py

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from src.case import CaseAnalysis, analyse  # noqa: E402
from src.ingest import ingest_statement  # noqa: E402
from src.integrity import AuditLog  # noqa: E402
from src.report import BAND_COLORS, EXTERNAL_COLOR, build_case_report  # noqa: E402
from src.risk import RULE_LABELS  # noqa: E402

APP_TITLE = "Bank Statement Forensic Analyzer"

SERIES_BLUE = "#2A78D6"
EVIDENCE_COLOR = "#D03B3B"
REFERENCE_GRAY = "#898781"

BAND_ICONS = {"Low": "✅", "Medium": "👀", "High": "⚠️", "Critical": "🚨"}

st.set_page_config(page_title=APP_TITLE, page_icon="🔎", layout="wide")


# ------------------------------------------------------------------ state


def init_state() -> None:
    defaults = {
        "case_reference": f"CASE/{datetime.now():%Y}/001",
        "analyst": "",
        "analysis": None,
        "audit": None,
        "source": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def audit(action: str, details: str = "") -> None:
    if st.session_state.audit is None:
        st.session_state.audit = AuditLog(st.session_state.case_reference)
    st.session_state.audit.record(st.session_state.analyst, action, details)


@st.cache_resource(show_spinner="Analysing the demo case...")
def demo_analysis() -> CaseAnalysis:
    files = sorted(settings.DEMO_CASE_DIR.glob("statement_*"))
    return analyse([ingest_statement(path.read_bytes(), path.name) for path in files])


@st.cache_resource(show_spinner="Reading and analysing statements...", max_entries=3)
def uploaded_analysis(files: tuple[tuple[str, bytes], ...]) -> CaseAnalysis:
    return analyse([ingest_statement(content, name) for name, content in files])


def naira(value: float) -> str:
    return f"₦{value:,.0f}"


def style(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), hoverlabel=dict(namelength=-1))
    return fig


def require_case() -> CaseAnalysis | None:
    analysis = st.session_state.analysis
    if analysis is None:
        st.info("Load the demo case or upload statements on the **Case setup** page first.")
    return analysis


# ------------------------------------------------------------------ pages


def page_setup() -> None:
    st.title(APP_TITLE)
    st.caption(
        "Upload bank statements (CSV, Excel or PDF) from different banks. The analyzer "
        "normalises them, fingerprints every file, checks the balances for gaps or edits, "
        "and flags money-laundering patterns across all accounts at once."
    )

    c1, c2 = st.columns(2)
    st.session_state.case_reference = c1.text_input("Case reference", st.session_state.case_reference)
    st.session_state.analyst = c2.text_input("Analyst name", st.session_state.analyst)

    source = st.radio(
        "Statements",
        ["Demo case (13 synthetic statements, 3 banks)", "Upload statements"],
        horizontal=True,
    )

    if source.startswith("Demo"):
        st.caption(
            "Fictional people, accounts and banks. The demo contains planted mule, "
            "structuring, round-tripping and dormant-account schemes."
        )
        if st.button("Analyse demo case", type="primary"):
            st.session_state.analysis = demo_analysis()
            st.session_state.source = "demo"
            st.session_state.audit = AuditLog(st.session_state.case_reference)
            audit(
                "ingest",
                "Demo case: "
                + ", ".join(
                    f"{s.source_file} sha256={s.sha256[:16]}" for s in st.session_state.analysis.statements
                ),
            )
    else:
        uploads = st.file_uploader(
            "Statement files", type=["csv", "xlsx", "xls", "pdf"], accept_multiple_files=True
        )
        if uploads and st.button("Analyse uploaded statements", type="primary"):
            files = tuple((upload.name, upload.getvalue()) for upload in uploads)
            try:
                analysis = uploaded_analysis(files)
            except ValueError as error:
                st.error(f"Could not read the statements: {error}")
                return
            st.session_state.analysis = analysis
            st.session_state.source = "upload"
            st.session_state.audit = AuditLog(st.session_state.case_reference)
            audit(
                "ingest",
                "Uploaded: "
                + ", ".join(f"{s.source_file} sha256={s.sha256[:16]}" for s in analysis.statements),
            )

    analysis = st.session_state.analysis
    if analysis is None:
        return

    st.success(
        f"{len(analysis.statements)} statements, {len(analysis.transactions):,} transactions analysed. "
        "Use the pages in the sidebar to review the case."
    )

    st.subheader("Evidence register")
    st.caption(
        "Each file's SHA-256 fingerprint. Re-hashing a file later and comparing proves it "
        "has not changed since analysis."
    )
    register = analysis.evidence_register
    st.dataframe(
        register[
            [
                "file",
                "bank",
                "account_number",
                "account_name",
                "transactions",
                "from",
                "to",
                "balance_breaks",
                "sha256",
            ]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "from": st.column_config.DatetimeColumn(format="D MMM YYYY"),
            "to": st.column_config.DatetimeColumn(format="D MMM YYYY"),
            "balance_breaks": st.column_config.NumberColumn("Balance breaks"),
        },
    )

    problems = [s for s in analysis.statements if s.warnings]
    if problems:
        for statement in problems:
            st.warning(f"**{statement.source_file}**: " + " ".join(statement.warnings))
    else:
        st.caption(
            "✅ All statements parsed fully, dates are in order, and every running balance is continuous."
        )


def page_overview(analysis: CaseAnalysis) -> None:
    st.title("Case overview")

    tx = analysis.transactions
    risk = analysis.risk
    flagged = risk[risk["risk_score"] > 0]

    cols = st.columns(4)
    cols[0].metric("Accounts", f"{tx['account_number'].nunique()}")
    cols[1].metric("Transactions", f"{len(tx):,}")
    cols[2].metric("Accounts with red flags", f"{len(flagged)}")
    cols[3].metric("Findings", f"{len(analysis.findings)}")
    st.caption(f"Period: {tx['timestamp'].min():%d %b %Y} – {tx['timestamp'].max():%d %b %Y}")

    st.subheader("Account risk ranking")
    st.caption(
        "Start with the highest scores. Scores add points per red flag, per repeat, and per link to other high-risk accounts."
    )

    table = risk.assign(risk_band=risk["risk_band"].map(lambda b: f"{BAND_ICONS[b]} {b}"))
    st.dataframe(
        table[["account_number", "account_name", "risk_score", "risk_band", "red_flags"]],
        hide_index=True,
        width="stretch",
        height=38 + 35 * len(table),
        column_config={
            "account_number": st.column_config.TextColumn("Account", width="small"),
            "account_name": st.column_config.TextColumn("Name", width="medium"),
            "risk_score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d", width="small"
            ),
            "risk_band": st.column_config.TextColumn("Band", width="small"),
            "red_flags": st.column_config.TextColumn("Red flags", width="large"),
        },
    )
    st.caption("Bank, totals and every transaction are on the **Account analysis** page.")

    if len(analysis.findings):
        st.subheader("Findings by red flag")
        counts = (
            analysis.findings.groupby("rule")["account_number"].nunique().rename("accounts").reset_index()
        )
        counts["label"] = counts["rule"].map(RULE_LABELS)
        counts = counts.sort_values("accounts")
        fig = go.Figure(
            go.Bar(
                x=counts["accounts"],
                y=counts["label"],
                orientation="h",
                marker_color=SERIES_BLUE,
                text=counts["accounts"],
                textposition="outside",
                cliponaxis=False,
                hovertemplate="%{y}: %{x} account(s)<extra></extra>",
            )
        )
        fig.update_layout(xaxis_title="Accounts", bargap=0.35, xaxis=dict(dtick=1))
        st.plotly_chart(style(fig, height=320), width="stretch")


def network_view(
    analysis: CaseAnalysis,
    min_total: float,
    flagged_only: bool,
    all_outside: bool,
) -> tuple[nx.DiGraph, list[tuple]]:
    """
    The part of the money-flow graph to draw.

    By default: subjects with red flags, the subjects they deal with, and
    outside accounts linked to two or more subjects. One-off outside
    accounts (most of them ordinary payees) are hidden unless asked for.
    """

    graph = analysis.graph
    scores = dict(zip(analysis.risk["account_number"], analysis.risk["risk_score"]))
    shared = set(analysis.shared["account"])

    edges = [(u, v, d) for u, v, d in graph.edges(data=True) if d["total"] >= min_total]

    subjects = {n for n, d in graph.nodes(data=True) if d["subject"]}
    flagged = {n for n in subjects if scores.get(n, 0) > 0}

    if flagged_only:
        anchors = flagged | {n for u, v, _ in edges for n in (u, v) if n in subjects and ({u, v} & flagged)}
    else:
        anchors = subjects

    keep = set(anchors)
    for u, v, _ in edges:
        if u not in anchors and v not in anchors:
            continue
        for node in (u, v):
            if node in subjects:
                if node in anchors:
                    keep.add(node)
            elif all_outside or node in shared:
                keep.add(node)

    edges = [(u, v, d) for u, v, d in edges if u in keep and v in keep]

    return graph.subgraph(keep), edges


def component_layout(view: nx.DiGraph) -> dict:
    """
    Lay out each connected group separately, then place the groups side
    by side (largest first), so small groups are not pushed to the edges.
    """

    components = sorted(nx.weakly_connected_components(view), key=len, reverse=True)
    position = {}
    x_offset, y_offset, row_height, row_width = 0.0, 0.0, 0.0, 9.0

    for component in components:
        sub = view.subgraph(component)
        size = max(1.0, len(component) ** 0.5)
        if len(component) == 1:
            local = {next(iter(component)): (0.0, 0.0)}
        else:
            local = nx.spring_layout(sub, seed=7, k=1.0 / size**0.5, iterations=300, scale=size / 2)

        if x_offset + size > row_width and x_offset > 0:
            x_offset, y_offset, row_height = 0.0, y_offset - row_height - 0.8, 0.0

        for node, (x, y) in local.items():
            position[node] = (x + x_offset + size / 2, y + y_offset)

        x_offset += size + 0.8
        row_height = max(row_height, size)

    return position


def network_figure(
    analysis: CaseAnalysis,
    min_total: float,
    label_subjects: bool,
    flagged_only: bool = True,
    all_outside: bool = False,
) -> go.Figure:
    graph = analysis.graph
    bands = dict(zip(analysis.risk["account_number"], analysis.risk["risk_band"]))
    scores = dict(zip(analysis.risk["account_number"], analysis.risk["risk_score"]))

    view, edges = network_view(analysis, min_total, flagged_only, all_outside)

    position = component_layout(view)
    biggest = max((d["total"] for *_, d in edges), default=1)

    fig = go.Figure()

    for u, v, data in edges:
        (x0, y0), (x1, y1) = position[u], position[v]
        fig.add_annotation(
            x=x1,
            y=y1,
            ax=x0,
            ay=y0,
            xref="x",
            yref="y",
            axref="x",
            ayref="y",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            standoff=6,
            arrowwidth=0.8 + 3 * data["total"] / biggest,
            arrowcolor=REFERENCE_GRAY,
            opacity=0.6,
        )

    subjects = [n for n in view.nodes if view.nodes[n]["subject"]]
    others = [n for n in view.nodes if not view.nodes[n]["subject"]]

    def flows(node: str) -> tuple[float, float]:
        incoming = sum(graph.edges[e]["total"] for e in graph.in_edges(node))
        outgoing = sum(graph.edges[e]["total"] for e in graph.out_edges(node))
        return incoming, outgoing

    fig.add_trace(
        go.Scatter(
            x=[position[n][0] for n in others],
            y=[position[n][1] for n in others],
            mode="markers",
            name="Other accounts",
            marker=dict(size=8, color=EXTERNAL_COLOR, line=dict(color="white", width=1)),
            customdata=[[n, view.nodes[n]["name"], *flows(n)] for n in others],
            hovertemplate="<b>%{customdata[1]}</b><br>%{customdata[0]}<br>In ₦%{customdata[2]:,.0f} · Out ₦%{customdata[3]:,.0f}<extra>Other account</extra>",
        )
    )

    for band in ["Low", "Medium", "High", "Critical"]:
        members = [n for n in subjects if bands.get(n) == band]
        if not members:
            continue
        fig.add_trace(
            go.Scatter(
                x=[position[n][0] for n in members],
                y=[position[n][1] for n in members],
                mode="markers+text" if label_subjects else "markers",
                text=[n[-4:] for n in members],
                textposition="top center",
                textfont=dict(size=11),
                name=f"Subject – {band}",
                marker=dict(size=20, color=BAND_COLORS[band], line=dict(color="white", width=2)),
                customdata=[[n, view.nodes[n]["name"], scores.get(n, 0), *flows(n)] for n in members],
                hovertemplate=(
                    "<b>%{customdata[1]}</b><br>%{customdata[0]}<br>Risk score %{customdata[2]}"
                    "<br>In ₦%{customdata[3]:,.0f} · Out ₦%{customdata[4]:,.0f}<extra>" + band + "</extra>"
                ),
            )
        )

    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x")
    fig.update_layout(legend=dict(orientation="h", y=-0.02), dragmode="pan")

    return style(fig, height=640)


def page_network(analysis: CaseAnalysis) -> None:
    st.title("Money-flow network")
    st.caption(
        "Who paid whom. Large dots are the subject accounts, coloured by risk band; grey dots are "
        "other accounts found in the narrations. Arrow thickness shows the amount. Hover for details."
    )

    min_total = st.select_slider(
        "Show flows of at least",
        options=[100_000, 250_000, 500_000, 1_000_000, 2_500_000, 5_000_000],
        value=500_000,
        format_func=naira,
    )
    c1, c2, c3 = st.columns(3)
    flagged_only = c1.toggle(
        "Only accounts with red flags", value=True, help="Plus the subjects they deal with directly."
    )
    all_outside = c2.toggle(
        "Show one-off outside accounts",
        value=False,
        help="Outside accounts that deal with just one subject - mostly ordinary payees.",
    )
    labels = c3.toggle("Label subjects", value=True)

    st.plotly_chart(network_figure(analysis, min_total, labels, flagged_only, all_outside), width="stretch")
    st.caption("Labels show the last 4 digits of each subject account. Drag to pan, scroll to zoom.")

    st.subheader("Accounts linked to several subjects")
    if analysis.shared.empty:
        st.caption("No outside account transacts with two or more subjects.")
    else:
        st.caption(
            "Outside accounts that deal with two or more subjects - candidates for the next statement request."
        )
        st.dataframe(
            analysis.shared,
            hide_index=True,
            width="stretch",
            column_config={
                "account": "Account",
                "name": "Name",
                "linked_subjects": "Subjects linked",
                "subject_accounts": "Subject accounts",
                "total_volume": st.column_config.NumberColumn("Total volume (₦)", format="localized"),
            },
        )


def page_account(analysis: CaseAnalysis) -> None:
    st.title("Account analysis")

    risk = analysis.risk
    labels = {
        row.account_number: f"{BAND_ICONS[row.risk_band]} {row.account_number} · {row.account_name} · {row.bank} · score {row.risk_score}"
        for row in risk.itertuples()
    }
    account = st.selectbox("Account", list(labels), format_func=labels.get)

    if st.session_state.get("last_viewed") != account:
        st.session_state.last_viewed = account
        audit("view_account", account)

    row = risk[risk["account_number"] == account].iloc[0]
    tx = analysis.transactions[analysis.transactions["account_number"] == account].sort_values("timestamp")
    findings = analysis.findings[analysis.findings["account_number"] == account]
    evidence = set(id_ for ids in findings["txn_ids"] for id_ in ids)

    cols = st.columns(4)
    cols[0].metric("Risk score", f"{row.risk_score} · {row.risk_band}")
    cols[1].metric("Total in", naira(row.total_in))
    cols[2].metric("Total out", naira(row.total_out))
    cols[3].metric("Transactions", f"{row.transactions:,}")

    if row.linked_high_risk:
        st.caption(f"Moved significant money with high-risk subject(s): {row.linked_high_risk}")

    fig = go.Figure(
        go.Scatter(
            x=tx["timestamp"],
            y=tx["balance"],
            mode="lines",
            name="Balance",
            line=dict(color=SERIES_BLUE, width=2),
            line_shape="hv",
            hovertemplate="%{x|%d %b %Y %H:%M}<br>Balance ₦%{y:,.0f}<extra></extra>",
        )
    )
    flagged = tx[tx["txn_id"].isin(evidence)]
    if len(flagged):
        fig.add_trace(
            go.Scatter(
                x=flagged["timestamp"],
                y=flagged["balance"],
                mode="markers",
                name="Evidence transactions",
                marker=dict(color=EVIDENCE_COLOR, size=9, line=dict(color="white", width=1)),
                customdata=flagged[["description", "amount"]],
                hovertemplate="%{x|%d %b %Y %H:%M}<br>%{customdata[0]}<br>₦%{customdata[1]:,.0f}<extra>Evidence</extra>",
            )
        )
    fig.update_layout(
        title="Balance over time (red = transactions behind a finding)",
        yaxis_title="Balance (₦)",
        legend=dict(orientation="h", y=1.12, x=1, xanchor="right"),
        hovermode="closest",
    )
    st.plotly_chart(style(fig, height=360), width="stretch")

    st.subheader(f"Findings ({len(findings)})")
    if findings.empty:
        st.caption("No red flags for this account.")
    for finding in findings.itertuples():
        st.markdown(
            f"**{RULE_LABELS.get(finding.rule, finding.rule)}** · {finding.start:%d %b %Y} – {finding.end:%d %b %Y}  \n"
            f"{finding.explanation} _({finding.evidence_count} supporting transactions)_"
        )

    st.subheader("Transactions")
    only = st.toggle("Show only evidence transactions", value=bool(evidence))
    shown = tx[tx["txn_id"].isin(evidence)] if only else tx
    st.dataframe(
        shown[
            [
                "txn_id",
                "timestamp",
                "description",
                "debit",
                "credit",
                "balance",
                "channel",
                "counterparty_account",
                "counterparty_name",
            ]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "timestamp": st.column_config.DatetimeColumn("Date", format="D MMM YYYY, HH:mm"),
            "debit": st.column_config.NumberColumn("Debit (₦)", format="localized"),
            "credit": st.column_config.NumberColumn("Credit (₦)", format="localized"),
            "balance": st.column_config.NumberColumn("Balance (₦)", format="localized"),
        },
    )


def page_report(analysis: CaseAnalysis) -> None:
    st.title("Report & audit trail")

    reference = st.session_state.case_reference

    st.subheader("Downloads")
    c1, c2, c3 = st.columns(3)

    pdf = build_case_report(analysis, reference, st.session_state.analyst, st.session_state.audit)
    safe = reference.replace("/", "-")
    if c1.download_button("Case report (PDF)", pdf, f"{safe}_report.pdf", "application/pdf", type="primary"):
        audit("export", "Case report PDF")

    export = analysis.transactions.copy()
    export["flagged"] = export["txn_id"].isin({i for ids in analysis.findings["txn_ids"] for i in ids})
    if c2.download_button(
        "All transactions (CSV)", export.to_csv(index=False).encode(), f"{safe}_transactions.csv", "text/csv"
    ):
        audit("export", "Normalised transactions CSV")

    findings = analysis.findings.assign(txn_ids=analysis.findings["txn_ids"].map(" ".join))
    if c3.download_button(
        "Findings (CSV)", findings.to_csv(index=False).encode(), f"{safe}_findings.csv", "text/csv"
    ):
        audit("export", "Findings CSV")

    st.subheader("Audit trail")
    st.caption(
        "Every action is logged. Each entry contains the fingerprint of the one before it, "
        "so any later edit or deletion breaks the chain and shows up on verification."
    )
    log: AuditLog = st.session_state.audit
    intact, message = log.verify()
    (st.success if intact else st.error)(message)
    st.dataframe(
        pd.DataFrame(log.entries)[["sequence", "timestamp", "analyst", "action", "details", "hash"]],
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Download audit log (JSONL)", log.to_jsonl().encode(), f"{safe}_audit.jsonl", "application/json"
    )

    with st.expander("Verify an audit log file"):
        upload = st.file_uploader("Audit log (.jsonl)", type=["jsonl", "json", "txt"], key="verify_log")
        if upload is not None:
            ok, result = AuditLog.from_jsonl(upload.getvalue().decode()).verify()
            (st.success if ok else st.error)(result)


def page_about() -> None:
    st.title("About")
    st.markdown("""
Investigators often receive bank statements for many accounts from several banks, each
in its own format - and review them largely by hand. This tool automates the first pass.

**Pipeline**

1. **Ingest** - CSV, Excel and PDF statements; header detection, column mapping by
   synonym, explicit date-format detection, counterparty extraction from narrations.
2. **Integrity** - SHA-256 fingerprint per file; balance-continuity check to expose missing
   pages, removed rows or edited figures; hash-chained audit log.
3. **Detection** - pass-through (mule) accounts, structuring below the NGN 5m reporting
   threshold (MLPPA 2022), fan-in, fan-out, round-tripping, dormant-account reactivation
   and rapid cash-out. Each finding cites its evidence transactions.
4. **Network** - who-paid-whom graph across all statements; outside accounts linked to
   several subjects.
5. **Scoring and reporting** - account risk ranking and a PDF case report.
""")

    if settings.ACCURACY_REPORT.exists():
        accuracy = json.loads(settings.ACCURACY_REPORT.read_text())
        st.subheader("Measured accuracy")
        accounts = accuracy["accounts"]
        st.caption(
            f"On {accuracy['cases']} freshly generated cases never used while building the rules: "
            f"{accounts['suspicious_flagged']}/{accounts['suspicious_accounts']} suspicious accounts flagged, "
            f"{accounts['clean_flagged']}/{accounts['clean_accounts']} clean accounts flagged."
        )
        st.dataframe(pd.DataFrame(accuracy["typologies"]), hide_index=True, width="stretch")

    st.markdown("""
**Limitations.** Accuracy is measured on synthetic data from this project's own generator, so the
rules and the test data share the same assumptions; real statements are messier and real-world
accuracy will be lower. Narration formats differ between banks, so new formats may need extra
counterparty patterns. Findings are leads for an investigator to verify against the source
statements, not conclusions.

**Author:** Muhammad Ibrahim Gimba ·
[GitHub](https://github.com/zameer-Gimba/bank-statement-forensic-analyzer)
""")


# ------------------------------------------------------------------ main


def main() -> None:
    init_state()

    pages = {
        "Case setup": page_setup,
        "Overview": page_overview,
        "Network": page_network,
        "Account analysis": page_account,
        "Report & audit": page_report,
        "About": page_about,
    }

    with st.sidebar:
        st.header("Navigation")
        choice = st.radio("Go to", list(pages), label_visibility="collapsed")
        st.divider()
        analysis = st.session_state.analysis
        if analysis is not None:
            st.caption(
                f"Case **{st.session_state.case_reference}**\n\n"
                f"{len(analysis.statements)} statements · {len(analysis.transactions):,} transactions"
            )
        st.caption("Synthetic demo data only. Never upload real customer data to a public deployment.")

    if choice in ("Case setup", "About"):
        pages[choice]()
    else:
        analysis = require_case()
        if analysis is not None:
            pages[choice](analysis)


main()
