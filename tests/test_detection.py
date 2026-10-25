"""
Tests for detection, scoring and the network.
"""

from src.evaluate import evaluate


def test_planted_typologies_are_found_without_false_alarms(analysis, truth):
    table, summary = evaluate(analysis.findings, truth)
    assert summary["suspicious_flagged"] == summary["suspicious_accounts"]
    assert summary["clean_flagged"] == 0
    assert (table["recall"] == 1.0).all()


def test_findings_cite_evidence(analysis):
    known = set(analysis.transactions["txn_id"])
    for ids in analysis.findings["txn_ids"]:
        assert ids and set(ids) <= known


def test_ring_leader_ranks_high_through_network_links(analysis, truth):
    roles = {a: info["role"] for a, info in truth["accounts"].items()}
    risk = analysis.risk.set_index("account_number")
    leader = next(a for a, r in roles.items() if r == "consolidator")
    assert risk.loc[leader, "risk_band"] in ("High", "Critical")
    for account, role in roles.items():
        if role.startswith("normal"):
            assert risk.loc[account, "risk_score"] == 0


def test_facilitator_is_found_by_link_analysis(analysis, truth):
    assert set(truth["linked_external"]) <= set(analysis.shared["account"])


def test_transfer_between_subjects_counted_once(analysis):
    from src.network import transfer_edges

    edges = transfer_edges(analysis.transactions)
    subjects = set(analysis.transactions["account_number"])
    internal = edges[edges["source"].isin(subjects) & edges["target"].isin(subjects)]
    key = internal[["source", "target", "amount"]].assign(day=internal["timestamp"].dt.floor("D"))
    assert not key.duplicated().any()
