"""
Case report generator for the Bank Statement Forensic Analyzer.

Produces a PDF analysis report for investigators: evidence register with
file fingerprints, data-quality checks, account risk ranking, findings
with their evidence, linked counterparties and a money-flow diagram.

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.case import CaseAnalysis  # noqa: E402
from src.integrity import AuditLog  # noqa: E402
from src.risk import RULE_LABELS  # noqa: E402

BAND_COLORS = {
    "Low": "#0CA30C",
    "Medium": "#FAB219",
    "High": "#EC835A",
    "Critical": "#D03B3B",
}

EXTERNAL_COLOR = "#B8B7B0"
HEADER_COLOR = colors.HexColor("#1F2A44")


def _table(rows: list[list], widths: list[float], font_size: float = 8) -> Table:
    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8CDD8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def network_image(analysis: CaseAnalysis, min_total: float = 1_000_000) -> io.BytesIO:
    """
    Money-flow diagram: subject accounts plus counterparties linked by
    flows of at least min_total.
    """

    graph = analysis.graph
    bands = dict(zip(analysis.risk["account_number"], analysis.risk["risk_band"]))

    keep = {n for n, d in graph.nodes(data=True) if d["subject"]}
    edges = [(u, v) for u, v, d in graph.edges(data=True) if d["total"] >= min_total]
    keep |= {n for edge in edges for n in edge}

    view = graph.subgraph(keep).copy()
    view.remove_edges_from([(u, v) for u, v, d in view.edges(data=True) if d["total"] < min_total])

    figure, axis = plt.subplots(figsize=(7.2, 5.4), dpi=150)
    position = nx.spring_layout(view, seed=7, k=1.4 / max(len(view), 1) ** 0.5)

    node_colors = [
        BAND_COLORS[bands.get(node, "Low")] if view.nodes[node]["subject"] else EXTERNAL_COLOR
        for node in view.nodes
    ]
    sizes = [260 if view.nodes[node]["subject"] else 60 for node in view.nodes]
    widths = [
        0.4 + 2.6 * (view.edges[e]["total"] / max(d["total"] for *_, d in view.edges(data=True)))
        for e in view.edges
    ]

    nx.draw_networkx_edges(
        view, position, ax=axis, width=widths, edge_color="#898781", arrowsize=8, alpha=0.7
    )
    nx.draw_networkx_nodes(
        view, position, ax=axis, node_color=node_colors, node_size=sizes, linewidths=0.8, edgecolors="white"
    )
    nx.draw_networkx_labels(
        view,
        position,
        labels={n: n[-4:] for n in view.nodes if view.nodes[n]["subject"]},
        font_size=6,
        ax=axis,
    )

    axis.set_axis_off()
    axis.set_title(
        f"Money flows >= NGN {min_total:,.0f} (subjects coloured by risk band; "
        "grey = other accounts; labels = last 4 digits)",
        fontsize=7,
    )

    buffer = io.BytesIO()
    figure.tight_layout()
    figure.savefig(buffer, format="png")
    plt.close(figure)
    buffer.seek(0)

    return buffer


def build_case_report(
    analysis: CaseAnalysis,
    case_reference: str,
    analyst: str,
    audit: AuditLog | None = None,
) -> bytes:
    """
    Create the PDF case analysis report.
    """

    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Statement analysis {case_reference}",
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tx = analysis.transactions
    risk = analysis.risk
    flagged = risk[risk["findings"] > 0]

    story = [
        Paragraph("Bank Statement Analysis Report", styles["Title"]),
        Paragraph(
            f"Case reference: <b>{case_reference}</b> &middot; Analyst: <b>{analyst or 'unspecified'}</b> "
            f"&middot; Generated: {generated}",
            body,
        ),
        Spacer(1, 4 * mm),
        Paragraph("1. Summary", styles["Heading2"]),
        Paragraph(
            f"{len(analysis.statements)} statements covering {tx['account_number'].nunique()} accounts and "
            f"{len(tx):,} transactions from {tx['timestamp'].min():%d %b %Y} to {tx['timestamp'].max():%d %b %Y} "
            f"were analysed. <b>{len(flagged)} account(s)</b> show red flags, with "
            f"<b>{len(analysis.findings)} finding(s)</b> in total. "
            f"{len(analysis.shared)} other account(s) transact with two or more subjects.",
            body,
        ),
        Spacer(1, 3 * mm),
        Paragraph("2. Account risk ranking", styles["Heading2"]),
        _table(
            [["Account", "Name", "Bank", "Score", "Band", "Red flags"]]
            + [
                [
                    row.account_number,
                    Paragraph(row.account_name, small),
                    row.bank,
                    str(row.risk_score),
                    row.risk_band,
                    Paragraph(row.red_flags or "-", small),
                ]
                for row in risk.itertuples()
            ],
            [24 * mm, 42 * mm, 24 * mm, 13 * mm, 16 * mm, 61 * mm],
        ),
        Spacer(1, 4 * mm),
        KeepTogether(
            [
                Paragraph("3. Money-flow network", styles["Heading2"]),
                Image(network_image(analysis), width=170 * mm, height=127 * mm),
            ]
        ),
        PageBreak(),
        Paragraph("4. Findings", styles["Heading2"]),
        Paragraph(
            "Each finding lists the period, the amount involved and the number of supporting "
            "transactions. Transaction IDs are in the accompanying CSV export.",
            body,
        ),
        Spacer(1, 2 * mm),
    ]

    names = dict(zip(risk["account_number"], risk["account_name"]))

    finding_rows = [["Account", "Red flag", "Period", "Explanation", "Txns"]]
    for row in analysis.findings.itertuples():
        finding_rows.append(
            [
                Paragraph(f"{row.account_number}<br/>{names.get(row.account_number, '')}", small),
                Paragraph(RULE_LABELS.get(row.rule, row.rule), small),
                Paragraph(f"{row.start:%d %b %Y}<br/>to {row.end:%d %b %Y}", small),
                Paragraph(row.explanation, small),
                str(row.evidence_count),
            ]
        )

    story.append(_table(finding_rows, [32 * mm, 28 * mm, 22 * mm, 88 * mm, 10 * mm]))

    if len(analysis.shared):
        story += [
            Spacer(1, 4 * mm),
            Paragraph("5. Accounts linked to several subjects", styles["Heading2"]),
            Paragraph("Consider requesting statements for these accounts.", body),
            Spacer(1, 2 * mm),
            _table(
                [["Account", "Name", "Subjects linked", "Total volume (NGN)"]]
                + [
                    [r.account, Paragraph(r.name, small), str(r.linked_subjects), f"{r.total_volume:,.0f}"]
                    for r in analysis.shared.head(25).itertuples()
                ],
                [28 * mm, 70 * mm, 30 * mm, 42 * mm],
            ),
        ]

    register = analysis.evidence_register

    story += [
        Spacer(1, 4 * mm),
        Paragraph("6. Evidence register and data quality", styles["Heading2"]),
        Paragraph(
            "SHA-256 fingerprints of the files analysed. Re-hashing a file and comparing "
            "proves it is unchanged. Balance breaks indicate missing pages, removed rows or "
            "edited figures.",
            body,
        ),
        Spacer(1, 2 * mm),
        _table(
            [["File / account", "SHA-256", "Rows", "Breaks"]]
            + [
                [
                    Paragraph(f"{r.file}<br/>{r.bank} {r.account_number}", small),
                    Paragraph(r.sha256, small),
                    str(r.transactions),
                    str(r.balance_breaks),
                ]
                for r in register.itertuples()
            ],
            [58 * mm, 90 * mm, 12 * mm, 12 * mm],
            font_size=7,
        ),
    ]

    if audit is not None:
        intact, message = audit.verify()
        story += [
            Spacer(1, 3 * mm),
            Paragraph(
                f"<b>Audit log:</b> {len(audit.entries)} entries; {message}",
                body,
            ),
        ]

    story += [
        Spacer(1, 6 * mm),
        Paragraph(
            "<i>Automated analysis to support, not replace, investigative judgement. Every finding "
            "must be verified against the source statements before any action is taken.</i>",
            body,
        ),
    ]

    document.build(story)

    return buffer.getvalue()
