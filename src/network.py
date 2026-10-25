"""
Money-flow network for the Bank Statement Forensic Analyzer.

Builds a directed graph of who paid whom from the ingested statements.
A transfer between two subject accounts appears in both statements (a
debit on one, a credit on the other); it is counted once.

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import networkx as nx
import pandas as pd


def transfer_edges(transactions: pd.DataFrame) -> pd.DataFrame:
    """
    One row per money movement between two identified accounts.

    Returns
    -------
    pd.DataFrame
        source, target, amount, timestamp, txn_id, source_name,
        target_name
    """

    transfers = transactions[
        (transactions["counterparty_account"] != "") & transactions["channel"].isin(["transfer", "salary"])
    ]

    outgoing = transfers[transfers["amount"] < 0]
    incoming = transfers[transfers["amount"] > 0]

    edges = pd.concat(
        [
            pd.DataFrame(
                {
                    "source": outgoing["account_number"],
                    "target": outgoing["counterparty_account"],
                    "source_name": outgoing["account_name"],
                    "target_name": outgoing["counterparty_name"],
                    "amount": -outgoing["amount"],
                    "timestamp": outgoing["timestamp"],
                    "txn_id": outgoing["txn_id"],
                }
            ),
            pd.DataFrame(
                {
                    "source": incoming["counterparty_account"],
                    "target": incoming["account_number"],
                    "source_name": incoming["counterparty_name"],
                    "target_name": incoming["account_name"],
                    "amount": incoming["amount"],
                    "timestamp": incoming["timestamp"],
                    "txn_id": incoming["txn_id"],
                }
            ),
        ],
        ignore_index=True,
    )

    # The same transfer seen from both sides: same parties, amount and day.
    edges["day"] = edges["timestamp"].dt.floor("D")

    return (
        edges.sort_values("timestamp")
        .drop_duplicates(subset=["source", "target", "amount", "day"])
        .drop(columns="day")
        .reset_index(drop=True)
    )


def build_graph(
    transactions: pd.DataFrame,
    subject_accounts: dict[str, str],
) -> nx.DiGraph:
    """
    Aggregated money-flow graph.

    Nodes carry: name, subject (bool). Edges carry: total, count,
    first, last.
    """

    edges = transfer_edges(transactions)

    graph = nx.DiGraph()

    for (source, target), group in edges.groupby(["source", "target"]):
        graph.add_edge(
            source,
            target,
            total=float(group["amount"].sum()),
            count=int(len(group)),
            first=group["timestamp"].min(),
            last=group["timestamp"].max(),
        )

    names = dict(zip(edges["source"], edges["source_name"]))
    names.update(dict(zip(edges["target"], edges["target_name"])))
    names.update(subject_accounts)

    for node in graph.nodes:
        graph.nodes[node]["name"] = names.get(node, "") or node
        graph.nodes[node]["subject"] = node in subject_accounts

    for account, name in subject_accounts.items():
        if account not in graph:
            graph.add_node(account, name=name, subject=True)

    return graph


def shared_counterparties(
    graph: nx.DiGraph,
    min_subjects: int = 2,
) -> pd.DataFrame:
    """
    Non-subject accounts that transact with several subject accounts.

    These links often reveal accounts that should be added to the case.
    """

    rows = []

    for node, data in graph.nodes(data=True):

        if data["subject"]:
            continue

        neighbours = set(graph.predecessors(node)) | set(graph.successors(node))
        subjects = sorted(n for n in neighbours if graph.nodes[n]["subject"])

        if len(subjects) >= min_subjects:
            volume = sum(
                graph.edges[edge]["total"]
                for edge in list(graph.in_edges(node)) + list(graph.out_edges(node))
            )
            rows.append(
                {
                    "account": node,
                    "name": data["name"],
                    "linked_subjects": len(subjects),
                    "subject_accounts": ", ".join(subjects),
                    "total_volume": volume,
                }
            )

    columns = ["account", "name", "linked_subjects", "subject_accounts", "total_volume"]

    return (
        pd.DataFrame(rows, columns=columns)
        .sort_values(["linked_subjects", "total_volume"], ascending=False)
        .reset_index(drop=True)
    )
