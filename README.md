# Bank Statement Forensic Analyzer

![Python](https://img.shields.io/badge/Python-3.12-blue.svg)
![Streamlit](https://img.shields.io/badge/Dashboard-Streamlit-red)
![License](https://img.shields.io/badge/License-MIT-orange)

**Automated first-pass analysis of bank statements for financial crime investigations.**

In fraud and money-laundering cases, investigators receive bank statements for many accounts from several banks - each in its own format, often as PDF - and work through them largely by hand. This tool reads them all, checks they have not been tampered with, traces the money across accounts, and flags the patterns investigators look for, citing the exact transactions behind every finding.

> All demo data is **synthetic**: fictional people, account numbers and banks. Never upload real customer data to a public deployment.

---

## What it does

| Step | What happens |
|------|--------------|
| **Ingest** | Reads CSV, Excel and PDF statements; finds the transaction table under each bank's header block; maps column names by synonym; detects the date format explicitly per file; extracts the counterparty (name and account number) from the free-text narration. |
| **Verify** | SHA-256 fingerprint of every file (evidence register); **balance-continuity check** - previous balance + credit − debit must equal the balance, so missing pages, removed rows or edited figures are exposed; warning if dates are out of order. |
| **Detect** | Seven money-laundering typologies (below). Every finding cites its evidence transactions and explains itself in plain English. |
| **Trace** | Who-paid-whom network across all statements; transfers between two subjects are counted once; outside accounts linked to several subjects are surfaced as candidates for the next statement request. |
| **Prioritise** | 0-100 risk score per account: points per red flag, per repeat, and per link to other high-risk accounts - so a ring leader who only consolidates mule proceeds still ranks high. |
| **Report** | PDF case report (ranking, network diagram, findings, evidence register); CSV exports; **hash-chained audit log** of every analyst action - editing or deleting any entry breaks the chain. |

## Red flags detected

| Typology | Rule (thresholds in `config/settings.py`) |
|----------|------|
| **Pass-through (mule)** | ≥80% of incoming money leaves within 48 hours, the account held little of its own beforehand, incoming transfers average ≥ ₦100k; at least 2 episodes |
| **Structuring** | ≥3 credits within 14 days each just under the reporting threshold (₦5m individuals / ₦10m companies - MLPPA 2022) |
| **Fan-in** | ≥8 different senders within 7 days (transfers ≥ ₦100k), total ≥ ₦1m |
| **Fan-out** | ≥8 different recipients within 7 days (transfers ≥ ₦100k), total ≥ ₦1m |
| **Round-tripping** | Money leaves an account and returns through up to 3 other accounts within 30 days, amounts within 25% |
| **Dormant reactivation** | ≥90 days without activity, then ≥ ₦2m moved within 30 days |
| **Rapid cash-out** | ≥25% of a large inflow withdrawn as cash (ATM/POS agent) within 24 hours |

## Accuracy

Measured on **50 freshly generated cases never used while building or tuning the rules** (`python -m src.evaluate --cases 50`, seeds 1000-1049):

| Typology | Planted | Detected | Precision | Recall |
|----------|--------:|---------:|----------:|-------:|
| Pass-through (mule) | 200 | 200 | 1.00 | 1.00 |
| Structuring | 50 | 50 | 1.00 | 1.00 |
| Fan-in | 50 | 50 | 1.00 | 1.00 |
| Fan-out | 50 | 50 | 1.00 | 1.00 |
| Round-tripping | 150 | 150 | 1.00 | 1.00 |
| Dormant reactivation | 50 | 50 | 1.00 | 1.00 |
| Rapid cash-out | 150 | 150 | 1.00 | 1.00 |

**500/500 suspicious accounts flagged; 0/150 clean accounts flagged** - including a busy retailer with hundreds of small customer payments and a contractor with large, regular payments, both designed to trigger false alarms.

**Read this honestly:** the test cases come from this project's own generator, so the rules and the data share the same assumptions, and the planted schemes are cleaner than real ones. Real-world accuracy will be lower. The value of the measurement is that it is reproducible and catches regressions.

How the rules were developed: the first version looked perfect on the demo case but failed on unseen cases (fan-out caught in 4 of 20 cases, 6 clean accounts flagged). That exposed a real bug - year-first dates (`2026-05-04`) were being read day-first (5 April), silently moving transactions between months - and two weak rules. Each fix was then checked on a further set of unseen cases before the final held-out run above.

---

## Quick start

```bash
git clone https://github.com/zameer-Gimba/bank-statement-forensic-analyzer
cd bank-statement-forensic-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

streamlit run streamlit_app.py     # dashboard - click "Analyse demo case"
pytest                             # 14 tests
python -m src.generator            # regenerate the demo case
python -m src.evaluate --cases 50  # measure accuracy on fresh cases
```

## Dashboard

| Page | Contents |
|------|----------|
| **Case setup** | Case reference, analyst, demo case or upload; evidence register with SHA-256 and data-quality warnings |
| **Overview** | Account risk ranking, findings by red flag |
| **Network** | Interactive money-flow graph (filters for amount, flagged accounts, one-off payees); accounts linked to several subjects |
| **Account analysis** | Balance timeline with evidence transactions marked, findings, transaction table |
| **Report & audit** | PDF case report, CSV exports, audit trail with chain verification |

## The demo case

`src/generator.py` builds a six-month case: 13 subject accounts at three fictional banks, each exporting in a different format - **Savanna Bank** (CSV, debit/credit columns, `05-Feb-2026` dates), **Lagoon Bank** (Excel, signed amounts, ISO dates) and **Sahel Bank** (PDF, withdrawals/lodgements, `dd/mm/yyyy` dates) - with distinct narration styles. Planted schemes:

- a **mule ring**: victims pay a collector, who forwards to three mules within hours; the mules withdraw part in cash and pass the rest to a ring leader, who pays out to many recipients
- **structuring**: seven cash deposits of ₦4.55m-4.98m within 12 days, then a ₦30m transfer
- **round-tripping**: ₦8m moving between three companies and back, twice
- a **dormant account** reactivated with ₦10m+ of flows
- a **facilitator** paid by three different subjects
- three **legitimate** accounts (salary earner, retailer, contractor) as false-alarm controls

`data/demo_case/ground_truth.json` records what was planted.

## Project structure

```
├── streamlit_app.py       dashboard
├── config/settings.py     all detection thresholds
├── src/
│   ├── generator.py       synthetic multi-bank case generator
│   ├── ingest.py          CSV / Excel / PDF ingestion, balance checks
│   ├── detectors.py       seven typology detectors
│   ├── network.py         money-flow graph, shared counterparties
│   ├── risk.py            account risk scoring
│   ├── case.py            end-to-end analysis
│   ├── integrity.py       SHA-256 evidence register, hash-chained audit log
│   ├── report.py          PDF case report
│   └── evaluate.py        accuracy against planted ground truth
├── data/demo_case/        demo statements + ground truth
├── reports/accuracy.json  held-out accuracy
└── tests/
```

## Limitations

- Counterparty extraction relies on narration patterns; statements from banks with different narration styles may need extra patterns in `src/ingest.py`.
- Scanned (image) PDFs need OCR first; only text-based PDFs are read.
- Thresholds are configurable starting points, not legal definitions.
- Findings are leads for an investigator to verify against the source statements, not conclusions.

## Author

**Muhammad Ibrahim Gimba** - [GitHub](https://github.com/zameer-Gimba) · [LinkedIn](https://www.linkedin.com/in/muhammad-ibrahim-gimba-60b87718b)

Related project: [Financial Crime Risk Scoring System](https://github.com/zameer-Gimba/financial-crime-risk-scoring-system) ([live demo](https://financial-crime-risk-scoring-system.streamlit.app/))

## License

MIT
