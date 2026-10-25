"""
Account risk scoring for the Bank Statement Forensic Analyzer.

Combines the findings for each subject account into a 0-100 risk score
and a priority band, so investigators know where to look first.

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import pandas as pd

from config.settings import (
    NETWORK_HIGH_RISK_SCORE,
    NETWORK_LINK_CAP,
    NETWORK_LINK_MIN_AMOUNT,
    NETWORK_LINK_POINTS,
    RISK_BANDS,
    RULE_WEIGHTS,
)

# Extra points for each repeat of a pattern beyond the first, capped.
REPEAT_POINTS = 5
REPEAT_CAP = 15

RULE_LABELS = {
    "pass_through": "Pass-through (mule) activity",
    "structuring": "Structuring below reporting threshold",
    "fan_in": "Fan-in from many senders",
    "fan_out": "Fan-out to many recipients",
    "round_trip": "Round-tripping",
    "dormant_reactivation": "Dormant account reactivated",
    "rapid_cash_out": "Rapid cash-out",
    "network": "Linked to high-risk subjects",
}


def risk_band(score: float) -> str:
    """
    Priority band for a 0-100 score.
    """

    band = RISK_BANDS[0][1]

    for minimum, label in RISK_BANDS:
        if score >= minimum:
            band = label

    return band


def _rule_score(findings: pd.DataFrame, account_number: str) -> tuple[int, pd.Series]:
    own = findings[findings["account_number"] == account_number]
    counts = own["rule"].value_counts()

    score = 0
    for rule, count in counts.items():
        score += RULE_WEIGHTS.get(rule, 10)
        score += min(REPEAT_CAP, REPEAT_POINTS * (count - 1))

    return score, counts


def network_links(
    edges: pd.DataFrame,
    high_risk: set[str],
) -> dict[str, set[str]]:
    """
    For each account, the high-risk subjects it exchanged at least
    NETWORK_LINK_MIN_AMOUNT with (in either direction).
    """

    if edges.empty:
        return {}

    pairs = (
        edges.assign(
            a=edges[["source", "target"]].min(axis=1),
            b=edges[["source", "target"]].max(axis=1),
        )
        .groupby(["a", "b"])["amount"]
        .sum()
    )

    links: dict[str, set[str]] = {}

    for (a, b), total in pairs.items():
        if total < NETWORK_LINK_MIN_AMOUNT:
            continue
        if b in high_risk:
            links.setdefault(a, set()).add(b)
        if a in high_risk:
            links.setdefault(b, set()).add(a)

    return links


def score_accounts(
    findings: pd.DataFrame,
    accounts: pd.DataFrame,
    edges: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Risk table: one row per subject account, highest risk first.

    The score adds points for each red-flag rule triggered (more for
    repeats) and, when transfer edges are given, network points for
    each high-risk subject the account moved significant money with -
    so a ring leader who only consolidates mule proceeds still ranks
    high.

    Parameters
    ----------
    findings : pd.DataFrame
        Output of run_detectors().
    accounts : pd.DataFrame
        account_number, account_name, bank, transactions, total_in,
        total_out for every subject account.
    edges : pd.DataFrame, optional
        Output of network.transfer_edges().
    """

    base = {account: _rule_score(findings, account) for account in accounts["account_number"]}

    high_risk = {account for account, (score, _) in base.items() if score >= NETWORK_HIGH_RISK_SCORE}

    links = network_links(edges, high_risk) if edges is not None else {}

    rows = []

    for account in accounts.itertuples():
        score, counts = base[account.account_number]
        linked = links.get(account.account_number, set())

        flags = [RULE_LABELS.get(rule, rule) for rule in counts.index]

        if linked:
            score += min(NETWORK_LINK_CAP, NETWORK_LINK_POINTS * len(linked))
            flags.append(f"{RULE_LABELS['network']} ({len(linked)})")

        score = min(100, score)

        rows.append(
            {
                "account_number": account.account_number,
                "account_name": account.account_name,
                "bank": account.bank,
                "transactions": account.transactions,
                "total_in": account.total_in,
                "total_out": account.total_out,
                "risk_score": int(score),
                "risk_band": risk_band(score),
                "red_flags": ", ".join(flags),
                "findings": int((findings["account_number"] == account.account_number).sum()),
                "linked_high_risk": ", ".join(sorted(linked)),
            }
        )

    return pd.DataFrame(rows).sort_values(["risk_score", "total_in"], ascending=False).reset_index(drop=True)


def account_summary(transactions: pd.DataFrame) -> pd.DataFrame:
    """
    Per-account totals used by score_accounts().
    """

    return (
        transactions.groupby(["account_number", "account_name", "bank"])
        .agg(
            transactions=("txn_id", "count"),
            total_in=("credit", "sum"),
            total_out=("debit", "sum"),
        )
        .reset_index()
    )
