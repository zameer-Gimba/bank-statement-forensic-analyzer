"""
Case analysis for the Bank Statement Forensic Analyzer.

Runs the full analysis over a set of ingested statements: combine
transactions, detect red flags, score accounts, build the money-flow
network and find counterparties shared between subjects.

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import pandas as pd

from src.detectors import run_detectors
from src.ingest import Statement, combine
from src.network import build_graph, shared_counterparties, transfer_edges
from src.risk import account_summary, score_accounts


@dataclass
class CaseAnalysis:
    """Everything the dashboard and the report need."""

    statements: list[Statement]
    transactions: pd.DataFrame
    findings: pd.DataFrame
    risk: pd.DataFrame
    graph: nx.DiGraph
    shared: pd.DataFrame

    @property
    def evidence_register(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "file": s.source_file,
                    "sha256": s.sha256,
                    "bank": s.bank,
                    "account_number": s.account_number,
                    "account_name": s.account_name,
                    "transactions": len(s.transactions),
                    "from": s.period[0],
                    "to": s.period[1],
                    "balance_breaks": len(s.balance_breaks),
                    "warnings": " ".join(s.warnings),
                }
                for s in self.statements
            ]
        )


def analyse(statements: list[Statement]) -> CaseAnalysis:
    """
    Run the complete analysis.
    """

    transactions = combine(statements)

    findings = run_detectors(transactions)

    risk = score_accounts(findings, account_summary(transactions), transfer_edges(transactions))

    subjects = dict(zip(risk["account_number"], risk["account_name"]))

    graph = build_graph(transactions, subjects)

    return CaseAnalysis(
        statements=statements,
        transactions=transactions,
        findings=findings,
        risk=risk,
        graph=graph,
        shared=shared_counterparties(graph),
    )
