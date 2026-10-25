"""
Red-flag detectors for the Bank Statement Forensic Analyzer.

Each detector looks for one money-laundering typology and returns
findings that carry the evidence (transaction IDs), the amounts and
period involved, and a plain-English explanation an investigator can
verify against the statement.

Typologies:
- pass_through          funds leave almost as soon as they arrive (mules)
- structuring           repeated deposits just under the reporting limit
- fan_in                many senders paying one account in a short time
- fan_out               one account paying many recipients quickly
- round_trip            funds return to their origin via other accounts
- dormant_reactivation  a long-silent account suddenly moves large sums
- rapid_cash_out        large inflow quickly withdrawn as cash

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import networkx as nx
import numpy as np
import pandas as pd

from config import settings
from src.network import transfer_edges

CORPORATE_MARKERS = (" LTD", " LIMITED", " PLC", " ENTERPRISES", " VENTURES", " NIG")


@dataclass
class Finding:
    """One detected red flag."""

    account_number: str
    rule: str
    start: pd.Timestamp
    end: pd.Timestamp
    amount: float
    explanation: str
    txn_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "account_number": self.account_number,
            "rule": self.rule,
            "start": self.start,
            "end": self.end,
            "amount": self.amount,
            "explanation": self.explanation,
            "evidence_count": len(self.txn_ids),
            "txn_ids": self.txn_ids,
        }


def _naira(value: float) -> str:
    return f"NGN {value:,.0f}"


def _merge_windows(windows: list[tuple]) -> list[tuple]:
    """
    Merge overlapping (start, end, ...) windows, keeping the first.
    """

    merged: list[tuple] = []

    for window in sorted(windows, key=lambda item: item[0]):
        if merged and window[0] <= merged[-1][1]:
            continue
        merged.append(window)

    return merged


def is_corporate(name: str) -> bool:
    upper = f" {str(name).upper()}"
    return any(marker in upper for marker in CORPORATE_MARKERS)


# ------------------------------------------------------------------ rules


def detect_pass_through(account: pd.DataFrame) -> list[Finding]:
    """
    Within PASS_THROUGH_WINDOW_HOURS of money arriving, most of it
    (PASS_THROUGH_OUTFLOW_RATIO) leaves again, and the account held
    little of its own beforehand - so the money leaving is the money
    that arrived, not savings.
    """

    window = timedelta(hours=settings.PASS_THROUGH_WINDOW_HOURS)
    credits = account[account["amount"] > 0]
    windows = []

    for _, credit in credits.iterrows():
        start, end = credit["timestamp"], credit["timestamp"] + window

        # Inflows are counted from one window *before* the anchor too, so
        # proceeds collected over several days count as incoming money,
        # not as the account's own prior balance.
        lookback = account[(account["timestamp"] >= start - window) & (account["timestamp"] <= end)]
        in_window = account[(account["timestamp"] >= start) & (account["timestamp"] <= end)]

        incoming = lookback.loc[lookback["amount"] > 0, "amount"]
        inflow = incoming.sum()
        outflow = -in_window.loc[in_window["amount"] < 0, "amount"].sum()
        prior_balance = lookback["balance"].iloc[0] - lookback["amount"].iloc[0]

        if (
            inflow >= settings.PASS_THROUGH_MIN_INFLOW
            and outflow >= settings.PASS_THROUGH_OUTFLOW_RATIO * inflow
            and prior_balance <= settings.PASS_THROUGH_MAX_PRIOR_BALANCE_RATIO * inflow
            and incoming.mean() >= settings.PASS_THROUGH_MIN_AVERAGE_CREDIT
        ):
            windows.append((start, end, inflow, outflow, in_window["txn_id"].tolist()))

    episodes = _merge_windows(windows)

    if len(episodes) < settings.PASS_THROUGH_MIN_EVENTS:
        return []

    return [
        Finding(
            account_number=account["account_number"].iloc[0],
            rule="pass_through",
            start=start,
            end=end,
            amount=outflow,
            explanation=(
                f"{_naira(inflow)} received and {_naira(outflow)} "
                f"({outflow / inflow:.0%}) sent out within "
                f"{2 * settings.PASS_THROUGH_WINDOW_HOURS} hours, with little "
                f"of the account's own money beforehand - "
                f"{len(episodes)} such episodes in the period."
            ),
            txn_ids=ids,
        )
        for start, end, inflow, outflow, ids in episodes
    ]


def detect_structuring(account: pd.DataFrame) -> list[Finding]:
    """
    STRUCTURING_MIN_COUNT or more credits just below the reporting
    threshold within STRUCTURING_WINDOW_DAYS, together exceeding it.
    """

    name = account["account_name"].iloc[0]
    threshold = (
        settings.REPORTING_THRESHOLD_CORPORATE
        if is_corporate(name)
        else settings.REPORTING_THRESHOLD_INDIVIDUAL
    )
    lower = threshold * (1 - settings.STRUCTURING_BAND)

    near = account[(account["amount"] >= lower) & (account["amount"] < threshold)].sort_values("timestamp")
    window = timedelta(days=settings.STRUCTURING_WINDOW_DAYS)
    windows = []

    for _, first in near.iterrows():
        group = near[
            (near["timestamp"] >= first["timestamp"]) & (near["timestamp"] <= first["timestamp"] + window)
        ]
        if len(group) >= settings.STRUCTURING_MIN_COUNT:
            windows.append((group["timestamp"].min(), group["timestamp"].max(), group))

    return [
        Finding(
            account_number=account["account_number"].iloc[0],
            rule="structuring",
            start=start,
            end=end,
            amount=float(group["amount"].sum()),
            explanation=(
                f"{len(group)} deposits between {_naira(group['amount'].min())} and "
                f"{_naira(group['amount'].max())} - each just under the "
                f"{_naira(threshold)} reporting threshold - totalling "
                f"{_naira(group['amount'].sum())} within {(end - start).days + 1} days."
            ),
            txn_ids=group["txn_id"].tolist(),
        )
        for start, end, group in _merge_windows(windows)
    ]


def _detect_fan(account: pd.DataFrame, direction: str) -> list[Finding]:
    """
    Shared logic for fan-in (many senders) and fan-out (many recipients).
    """

    incoming = direction == "in"
    minimum = settings.FAN_IN_MIN_SENDERS if incoming else settings.FAN_OUT_MIN_RECIPIENTS

    flows = account[
        (account["counterparty_account"] != "")
        & ((account["amount"] > 0) if incoming else (account["amount"] < 0))
        & (account["amount"].abs() >= settings.FAN_MIN_TRANSFER)
    ].sort_values("timestamp")

    window = timedelta(days=settings.FAN_WINDOW_DAYS)
    windows = []

    for _, first in flows.iterrows():
        group = flows[
            (flows["timestamp"] >= first["timestamp"]) & (flows["timestamp"] <= first["timestamp"] + window)
        ]
        parties = group["counterparty_account"].nunique()
        total = group["amount"].abs().sum()

        if parties >= minimum and total >= settings.FAN_MIN_TOTAL:
            windows.append((group["timestamp"].min(), group["timestamp"].max(), group, parties, total))

    rule = "fan_in" if incoming else "fan_out"
    noun = "senders" if incoming else "recipients"
    verb = "received from" if incoming else "paid to"

    return [
        Finding(
            account_number=account["account_number"].iloc[0],
            rule=rule,
            start=start,
            end=end,
            amount=float(total),
            explanation=(
                f"{_naira(total)} {verb} {parties} different {noun} within "
                f"{(end - start).days + 1} days (each transfer at least "
                f"{_naira(settings.FAN_MIN_TRANSFER)}, average {_naira(total / len(group))})."
            ),
            txn_ids=group["txn_id"].tolist(),
        )
        for start, end, group, parties, total in _merge_windows(windows)
    ]


def detect_fan_in(account: pd.DataFrame) -> list[Finding]:
    return _detect_fan(account, "in")


def detect_fan_out(account: pd.DataFrame) -> list[Finding]:
    return _detect_fan(account, "out")


def detect_dormant_reactivation(account: pd.DataFrame) -> list[Finding]:
    """
    A gap of DORMANT_MIN_DAYS with no activity, followed by large volume.
    """

    ordered = account.sort_values("timestamp")
    gaps = ordered["timestamp"].diff()
    findings = []

    for position in np.where(gaps >= timedelta(days=settings.DORMANT_MIN_DAYS))[0]:
        restart = ordered["timestamp"].iloc[position]
        burst = ordered[
            (ordered["timestamp"] >= restart)
            & (ordered["timestamp"] <= restart + timedelta(days=settings.DORMANT_REACTIVATION_WINDOW_DAYS))
        ]
        volume = burst["amount"].abs().sum()

        if volume >= settings.DORMANT_REACTIVATION_MIN_VOLUME:
            findings.append(
                Finding(
                    account_number=account["account_number"].iloc[0],
                    rule="dormant_reactivation",
                    start=restart,
                    end=burst["timestamp"].max(),
                    amount=float(volume),
                    explanation=(
                        f"No activity for {gaps.iloc[position].days} days, then "
                        f"{_naira(volume)} moved within "
                        f"{settings.DORMANT_REACTIVATION_WINDOW_DAYS} days of reactivation."
                    ),
                    txn_ids=burst["txn_id"].tolist(),
                )
            )

    return findings


def detect_rapid_cash_out(account: pd.DataFrame) -> list[Finding]:
    """
    Cash withdrawals within CASH_OUT_WINDOW_HOURS of a large inflow
    amounting to at least CASH_OUT_RATIO of it.
    """

    window = timedelta(hours=settings.CASH_OUT_WINDOW_HOURS)
    credits = account[account["amount"] >= settings.CASH_OUT_MIN_AMOUNT]
    cash = account[account["channel"] == "cash_withdrawal"]
    windows = []

    for _, credit in credits.iterrows():
        withdrawals = cash[
            (cash["timestamp"] > credit["timestamp"]) & (cash["timestamp"] <= credit["timestamp"] + window)
        ]
        withdrawn = -withdrawals["amount"].sum()

        if (
            withdrawn >= settings.CASH_OUT_RATIO * credit["amount"]
            and withdrawn >= settings.CASH_OUT_MIN_AMOUNT
        ):
            windows.append(
                (
                    credit["timestamp"],
                    withdrawals["timestamp"].max(),
                    credit["amount"],
                    withdrawn,
                    [credit["txn_id"], *withdrawals["txn_id"].tolist()],
                    len(withdrawals),
                )
            )

    return [
        Finding(
            account_number=account["account_number"].iloc[0],
            rule="rapid_cash_out",
            start=start,
            end=end,
            amount=float(withdrawn),
            explanation=(
                f"{_naira(withdrawn)} withdrawn in cash ({count} withdrawals) within "
                f"{settings.CASH_OUT_WINDOW_HOURS} hours of receiving {_naira(received)}."
            ),
            txn_ids=ids,
        )
        for start, end, received, withdrawn, ids, count in _merge_windows(windows)
    ]


def detect_round_trips(
    transactions: pd.DataFrame,
    subject_accounts: set[str],
) -> list[Finding]:
    """
    Cycles A -> B -> ... -> A of similar amounts, in time order, within
    ROUND_TRIP_MAX_DAYS. Reported for every subject account in the cycle.
    """

    edges = transfer_edges(transactions)
    edges = edges[edges["amount"] >= settings.ROUND_TRIP_MIN_AMOUNT]

    graph = nx.DiGraph()
    for (source, target), group in edges.groupby(["source", "target"]):
        graph.add_edge(source, target, transfers=group.sort_values("timestamp").to_dict("records"))

    findings = []
    reported = set()

    for cycle in nx.simple_cycles(graph, length_bound=settings.ROUND_TRIP_MAX_LENGTH):

        if len(cycle) < 2 or not subject_accounts & set(cycle):
            continue

        hops = list(zip(cycle, cycle[1:] + cycle[:1]))

        for first in graph.edges[hops[0]]["transfers"]:
            chain = [first]

            for hop in hops[1:]:
                previous = chain[-1]
                candidates = [
                    t
                    for t in graph.edges[hop]["transfers"]
                    if previous["timestamp"]
                    < t["timestamp"]
                    <= first["timestamp"] + timedelta(days=settings.ROUND_TRIP_MAX_DAYS)
                    and abs(t["amount"] - first["amount"])
                    <= settings.ROUND_TRIP_AMOUNT_TOLERANCE * first["amount"]
                ]
                if not candidates:
                    break
                chain.append(candidates[0])

            if len(chain) != len(hops):
                continue

            key = tuple(t["txn_id"] for t in chain)
            if key in reported:
                continue
            reported.add(key)

            route = " -> ".join(cycle + cycle[:1])
            for account in sorted(subject_accounts & set(cycle)):
                findings.append(
                    Finding(
                        account_number=account,
                        rule="round_trip",
                        start=chain[0]["timestamp"],
                        end=chain[-1]["timestamp"],
                        amount=float(chain[0]["amount"]),
                        explanation=(
                            f"{_naira(chain[0]['amount'])} left {cycle[0]} and "
                            f"{_naira(chain[-1]['amount'])} came back within "
                            f"{(chain[-1]['timestamp'] - chain[0]['timestamp']).days + 1} days "
                            f"via {route}."
                        ),
                        txn_ids=[t["txn_id"] for t in chain],
                    )
                )

    return findings


ACCOUNT_DETECTORS = [
    detect_pass_through,
    detect_structuring,
    detect_fan_in,
    detect_fan_out,
    detect_dormant_reactivation,
    detect_rapid_cash_out,
]


def run_detectors(transactions: pd.DataFrame) -> pd.DataFrame:
    """
    Run every detector over all subject accounts.

    Returns
    -------
    pd.DataFrame
        One row per finding (see Finding.as_dict).
    """

    findings: list[Finding] = []

    for _, account in transactions.groupby("account_number"):
        account = account.sort_values("timestamp")
        for detector in ACCOUNT_DETECTORS:
            findings.extend(detector(account))

    findings.extend(detect_round_trips(transactions, set(transactions["account_number"].unique())))

    columns = list(Finding("", "", pd.NaT, pd.NaT, 0.0, "").as_dict().keys())

    return (
        pd.DataFrame([finding.as_dict() for finding in findings], columns=columns)
        .sort_values(["account_number", "start"])
        .reset_index(drop=True)
    )
