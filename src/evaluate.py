"""
Detection accuracy on the synthetic demo case.

Compares the red flags raised for each account with the typologies that
were deliberately planted by the generator.

Usage:
    python -m src.evaluate                 # the demo case
    python -m src.evaluate --cases 50      # 50 freshly generated cases,
                                           # saved to reports/accuracy.json

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import pandas as pd

from config.settings import ACCURACY_REPORT, DEMO_CASE_DIR, DEMO_TRUTH_FILE
from src.detectors import run_detectors
from src.generator import generate_case
from src.ingest import combine, ingest_paths
from src.risk import RULE_LABELS

# Seeds never used while developing or tuning the detectors.
HELD_OUT_SEED_START = 1_000


def evaluate(findings: pd.DataFrame, truth: dict) -> tuple[pd.DataFrame, dict]:
    """
    Per-typology precision and recall at account level, plus whether
    every suspicious account was flagged and every clean one was not.

    Secondary patterns (a natural side-effect of a planted scheme, e.g.
    a round-trip account also passing funds through) are neither
    required nor counted as false alarms.
    """

    accounts = truth["accounts"]
    flagged = findings.groupby("account_number")["rule"].apply(set).to_dict()

    rows = []

    for rule in [r for r in RULE_LABELS if r != "network"]:
        planted = {a for a, info in accounts.items() if rule in info["typologies"]}
        acceptable = {a for a, info in accounts.items() if rule in info.get("secondary", [])}
        detected = {a for a, rules in flagged.items() if rule in rules} - acceptable
        hits = planted & detected
        rows.append(
            {
                "typology": RULE_LABELS[rule],
                "planted": len(planted),
                "detected": len(detected),
                "correct": len(hits),
                "precision": len(hits) / len(detected) if detected else None,
                "recall": len(hits) / len(planted) if planted else None,
            }
        )

    suspicious = {a for a, info in accounts.items() if info["typologies"]}
    clean = set(accounts) - suspicious
    any_flag = set(flagged)

    summary = {
        "suspicious_accounts": len(suspicious),
        "suspicious_flagged": len(suspicious & any_flag),
        "clean_accounts": len(clean),
        "clean_flagged": len(clean & any_flag),
    }

    return pd.DataFrame(rows), summary


def evaluate_many(cases: int, first_seed: int = HELD_OUT_SEED_START) -> dict:
    """
    Generate fresh cases, run detection, and aggregate accuracy.
    """

    tables, summaries = [], []

    with tempfile.TemporaryDirectory() as workdir:
        for seed in range(first_seed, first_seed + cases):
            folder = Path(workdir) / str(seed)
            truth = generate_case(folder, seed)
            findings = run_detectors(combine(ingest_paths(sorted(folder.glob("statement_*")))))
            table, summary = evaluate(findings, truth)
            tables.append(table)
            summaries.append(summary)

    combined = (
        pd.concat(tables)
        .groupby("typology", sort=False)[["planted", "detected", "correct"]]
        .sum()
        .reset_index()
    )
    combined["precision"] = (combined["correct"] / combined["detected"]).round(3)
    combined["recall"] = (combined["correct"] / combined["planted"]).round(3)

    return {
        "cases": cases,
        "seeds": [first_seed, first_seed + cases - 1],
        "typologies": combined.to_dict("records"),
        "accounts": pd.DataFrame(summaries).sum().astype(int).to_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure detection accuracy.")
    parser.add_argument("--cases", type=int, default=0, help="Evaluate on N freshly generated cases.")
    args = parser.parse_args()

    if args.cases:
        result = evaluate_many(args.cases)
        ACCURACY_REPORT.parent.mkdir(parents=True, exist_ok=True)
        ACCURACY_REPORT.write_text(json.dumps(result, indent=2))
        print(pd.DataFrame(result["typologies"]).to_string(index=False))
        accounts = result["accounts"]
        print(
            f"\n{args.cases} unseen cases: suspicious accounts flagged "
            f"{accounts['suspicious_flagged']}/{accounts['suspicious_accounts']}, clean accounts flagged "
            f"{accounts['clean_flagged']}/{accounts['clean_accounts']}  -> {ACCURACY_REPORT}"
        )
        return

    statements = ingest_paths(sorted(DEMO_CASE_DIR.glob("statement_*")))
    findings = run_detectors(combine(statements))
    truth = json.loads(DEMO_TRUTH_FILE.read_text())

    table, summary = evaluate(findings, truth)

    print(table.to_string(index=False))
    print(
        f"\nSuspicious accounts flagged: {summary['suspicious_flagged']}/{summary['suspicious_accounts']}"
        f" | Clean accounts flagged: {summary['clean_flagged']}/{summary['clean_accounts']}"
    )


if __name__ == "__main__":
    main()
