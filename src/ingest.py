"""
Statement ingestion for the Bank Statement Forensic Analyzer.

Reads bank statements in CSV, Excel or PDF form - each bank with its own
layout - and converts them into one canonical transaction table:

    txn_id, source_file, bank, account_number, account_name, timestamp,
    description, debit, credit, amount, balance, channel,
    counterparty_account, counterparty_name

Column names are matched by synonyms rather than hard-coded per bank,
the account holder is read from the statement header, and counterparties
are extracted from the free-text narration.

Every statement is also checked for balance continuity
(previous balance + credit - debit = balance). A break means a missing
page, a removed row or an edited figure - something an investigator must
know before relying on the statement.

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pdfplumber

COLUMN_SYNONYMS = {
    "timestamp": ["trans date", "transaction date", "posted date", "posting date", "date", "txn date"],
    "description": ["narration", "description", "details", "remarks", "particulars", "transaction details"],
    "debit": ["debit", "withdrawals", "withdrawal", "dr", "debits", "money out"],
    "credit": ["credit", "lodgements", "lodgement", "deposits", "cr", "credits", "money in"],
    "amount": ["amount", "transaction amount"],
    "balance": ["balance", "running balance", "closing balance"],
}

HEADER_KEYWORDS = {"date", "balance", "narration", "description", "details", "debit", "credit", "amount"}

ACCOUNT_NUMBER_PATTERN = re.compile(r"account\s*(?:number|no\.?)[\s:,\"]*(\d{10})", re.IGNORECASE)
ACCOUNT_NAME_PATTERN = re.compile(
    r"(?:account\s*name|customer)\s*[:,][\s:,\"]*([A-Z0-9 .&'-]+)", re.IGNORECASE
)
BANK_PATTERN = re.compile(r"^\s*([A-Z][A-Z ]+BANK)", re.IGNORECASE | re.MULTILINE)

COUNTERPARTY_PATTERNS = [
    re.compile(r"NIP TRF (?:FROM|TO) (?P<name>.+?)/(?P<account>\d{10})(?:/(?P<bank>.+))?$"),
    re.compile(r"SALARY PAYMENT (?P<name>.+?)/(?P<account>\d{10})"),
    re.compile(r"TRANSFER/(?P<account>\d{10})/(?P<name>.+?)/(?:INWARD|OUTWARD)"),
    re.compile(r"F[IO]P:(?P<name>.+?):(?P<account>\d{10})"),
]

GENERIC_ACCOUNT = re.compile(r"(?<!\d)(\d{10})(?!\d)")

CHANNEL_RULES = [
    ("cash_withdrawal", ["ATM", "CASH WITHDRAWAL", "CASH WDL", "POS CASH", "POS WITHDRAWAL", "CASH-OUT"]),
    ("cash_deposit", ["CASH DEPOSIT", "CASH LODGEMENT"]),
    ("salary", ["SALARY"]),
    ("card_payment", ["POS PURCHASE", "CARD PAYMENT", "WEB/POS"]),
    ("airtime", ["AIRTIME", "VTU"]),
]

CANONICAL_COLUMNS = [
    "txn_id",
    "source_file",
    "bank",
    "account_number",
    "account_name",
    "timestamp",
    "description",
    "debit",
    "credit",
    "amount",
    "balance",
    "channel",
    "counterparty_account",
    "counterparty_name",
]


@dataclass
class Statement:
    """One ingested statement and its quality checks."""

    source_file: str
    sha256: str
    bank: str
    account_number: str
    account_name: str
    transactions: pd.DataFrame
    balance_breaks: pd.DataFrame
    warnings: list[str] = field(default_factory=list)

    @property
    def period(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return (
            self.transactions["timestamp"].min(),
            self.transactions["timestamp"].max(),
        )


# ---------------------------------------------------------------- reading


DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d %b %Y %H:%M",
    "%d %b %Y",
]


def parse_dates(series: pd.Series) -> pd.Series:
    """
    Parse statement dates using one explicit format for the whole file.

    Guessing per value is dangerous: a year-first date such as
    2026-05-04 read "day first" silently becomes 5 April. Each known
    format is tried in turn and the first that parses (nearly) every row
    wins. Otherwise year-first values are parsed as ISO and everything
    else as day-first, the Nigerian convention.
    """

    text = series.astype(str).str.strip()

    for date_format in DATE_FORMATS:
        parsed = pd.to_datetime(text, format=date_format, errors="coerce")
        if parsed.notna().mean() >= 0.98:
            return parsed

    iso = text.str.match(r"^\d{4}-\d{2}-\d{2}")

    return pd.to_datetime(text, dayfirst=not iso.mean() > 0.5, format="mixed", errors="coerce")


def _to_number(series: pd.Series) -> pd.Series:
    """
    Parse amounts such as '1,250.00', '(500.00)', '' or 'NGN 2,000'.
    """

    text = (
        series.astype(str)
        .str.replace(r"[^\d.()\-]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    )

    return pd.to_numeric(text.replace({"": np.nan, "-": np.nan}), errors="coerce")


def _find_header_row(rows: list[list[str]]) -> int:
    """
    Index of the row that looks like the transaction table header.
    """

    for index, row in enumerate(rows):
        cells = {str(cell).strip().lower() for cell in row if str(cell).strip()}
        if len(cells & HEADER_KEYWORDS) >= 2 or (
            sum(any(keyword in cell for keyword in HEADER_KEYWORDS) for cell in cells) >= 3
        ):
            return index

    raise ValueError("Could not find a transaction table header in the statement.")


def _read_tabular(rows: list[list[str]]) -> tuple[pd.DataFrame, str]:
    """
    Split raw rows into the metadata text (above the header) and a
    transaction table (from the header down).
    """

    header_index = _find_header_row(rows)

    metadata = "\n".join(
        ",".join(str(cell) for cell in row if str(cell).strip() and str(cell) != "nan")
        for row in rows[:header_index]
    )

    header = [str(cell).strip() for cell in rows[header_index]]
    body = [row[: len(header)] for row in rows[header_index + 1 :] if any(str(c).strip() for c in row)]

    table = pd.DataFrame(body, columns=header)
    table = table.loc[:, [column for column in table.columns if column]]

    return table, metadata


def read_rows(content: bytes, filename: str) -> list[list[str]]:
    """
    Raw rows of a statement file, whatever its format.
    """

    suffix = Path(filename).suffix.lower()

    if suffix == ".csv":
        text = content.decode("utf-8-sig", errors="replace")
        return pd.read_csv(
            io.StringIO(text),
            header=None,
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=False,
            names=range(64),
            engine="python",
        ).values.tolist()

    if suffix in (".xlsx", ".xls"):
        frame = pd.read_excel(io.BytesIO(content), header=None, dtype=str)
        return frame.fillna("").values.tolist()

    if suffix == ".pdf":
        rows: list[list[str]] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            first_text = pdf.pages[0].extract_text() or ""
            rows.extend([[line] for line in first_text.splitlines()[:8]])
            seen_header = False
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        cells = [cell or "" for cell in row]
                        is_header = len({c.strip().lower() for c in cells} & HEADER_KEYWORDS) >= 2
                        if is_header and seen_header:
                            continue
                        seen_header = seen_header or is_header
                        rows.append(cells)
        return rows

    raise ValueError(f"Unsupported file type: {suffix} (use CSV, Excel or PDF).")


# -------------------------------------------------------------- normalising


def _map_columns(table: pd.DataFrame) -> dict[str, str]:
    """
    Map canonical names to the statement's own column names.
    """

    mapping: dict[str, str] = {}

    lowered = {column: column.strip().lower() for column in table.columns}

    for canonical, synonyms in COLUMN_SYNONYMS.items():
        for synonym in synonyms:
            match = next(
                (
                    column
                    for column, low in lowered.items()
                    if low == synonym and column not in mapping.values()
                ),
                None,
            )
            if match:
                mapping[canonical] = match
                break

    missing = {"timestamp", "description", "balance"} - mapping.keys()

    if missing:
        raise ValueError(f"Statement is missing required columns: {sorted(missing)}")

    if "amount" not in mapping and not {"debit", "credit"} <= mapping.keys():
        raise ValueError("Statement needs either an amount column or debit and credit columns.")

    return mapping


def parse_counterparty(description: str) -> tuple[str, str]:
    """
    Extract (account number, name) of the other party from a narration.
    """

    text = str(description).strip()

    for pattern in COUNTERPARTY_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("account"), match.group("name").strip()

    generic = GENERIC_ACCOUNT.search(text)

    return (generic.group(1), "") if generic else ("", "")


def classify_channel(description: str, counterparty_account: str) -> str:
    """
    Classify a transaction by its narration.
    """

    text = str(description).upper()

    for channel, keywords in CHANNEL_RULES:
        if any(keyword in text for keyword in keywords):
            return channel

    return "transfer" if counterparty_account else "other"


def _metadata_value(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def check_balance_continuity(frame: pd.DataFrame, tolerance: float = 0.5) -> pd.DataFrame:
    """
    Rows where previous balance + amount does not equal the balance.
    """

    expected = frame["balance"].shift(1) + frame["amount"]
    gap = (frame["balance"] - expected).abs()

    breaks = frame.loc[gap > tolerance, ["timestamp", "description", "amount", "balance"]].copy()
    breaks["expected_balance"] = expected[gap > tolerance]
    breaks["difference"] = (breaks["balance"] - breaks["expected_balance"]).round(2)

    return breaks.reset_index(drop=True)


def ingest_statement(content: bytes, filename: str) -> Statement:
    """
    Ingest one statement file into the canonical format.
    """

    sha256 = hashlib.sha256(content).hexdigest()

    table, metadata = _read_tabular(read_rows(content, filename))
    mapping = _map_columns(table)

    frame = pd.DataFrame()
    frame["timestamp"] = parse_dates(table[mapping["timestamp"]])
    frame["description"] = table[mapping["description"]].astype(str).str.strip()
    frame["balance"] = _to_number(table[mapping["balance"]])

    if "amount" in mapping:
        frame["amount"] = _to_number(table[mapping["amount"]])
    else:
        frame["amount"] = _to_number(table[mapping["credit"]]).fillna(0) - _to_number(
            table[mapping["debit"]]
        ).fillna(0)

    warnings = []

    unparsed = frame["timestamp"].isna() | frame["amount"].isna()
    if unparsed.any():
        warnings.append(f"{int(unparsed.sum())} row(s) could not be parsed and were skipped.")

    frame = frame[~unparsed].reset_index(drop=True)

    frame["debit"] = (-frame["amount"]).clip(lower=0)
    frame["credit"] = frame["amount"].clip(lower=0)

    parties = frame["description"].map(parse_counterparty)
    frame["counterparty_account"] = [account for account, _ in parties]
    frame["counterparty_name"] = [name for _, name in parties]
    frame["channel"] = [
        classify_channel(description, account)
        for description, account in zip(frame["description"], frame["counterparty_account"])
    ]

    account_number = _metadata_value(ACCOUNT_NUMBER_PATTERN, metadata)
    account_name = _metadata_value(ACCOUNT_NAME_PATTERN, metadata)
    bank = _metadata_value(BANK_PATTERN, metadata).title()

    if not account_number:
        warnings.append("Account number not found in the statement header.")
        account_number = f"UNKNOWN-{sha256[:8]}"

    frame["account_number"] = account_number
    frame["account_name"] = account_name
    frame["bank"] = bank or "Unknown Bank"
    frame["source_file"] = Path(filename).name
    frame["txn_id"] = [f"{account_number}-{index:05d}" for index in range(len(frame))]

    if not frame["timestamp"].is_monotonic_increasing:
        warnings.append(
            "Transactions are not in date order - check that dates were "
            "read in the right format before relying on the timeline."
        )

    breaks = check_balance_continuity(frame)

    if len(breaks):
        warnings.append(
            f"{len(breaks)} balance break(s): the statement may have missing "
            "pages, removed rows or edited figures."
        )

    return Statement(
        source_file=Path(filename).name,
        sha256=sha256,
        bank=frame["bank"].iloc[0] if len(frame) else bank,
        account_number=account_number,
        account_name=account_name,
        transactions=frame[CANONICAL_COLUMNS],
        balance_breaks=breaks,
        warnings=warnings,
    )


def ingest_paths(paths: list[Path]) -> list[Statement]:
    """
    Ingest statement files from disk.
    """

    return [ingest_statement(path.read_bytes(), path.name) for path in sorted(paths)]


def combine(statements: list[Statement]) -> pd.DataFrame:
    """
    All transactions from all statements in one table.
    """

    if not statements:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    return (
        pd.concat([statement.transactions for statement in statements], ignore_index=True)
        .sort_values(["timestamp", "account_number"])
        .reset_index(drop=True)
    )
