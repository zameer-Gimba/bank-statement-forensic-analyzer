"""
Tests for statement ingestion.
"""

import pandas as pd

from src.ingest import (
    check_balance_continuity,
    classify_channel,
    ingest_statement,
    parse_counterparty,
    parse_dates,
)


def test_every_format_is_read_completely(statements, truth):
    accounts = truth["accounts"]
    assert len(statements) == len(accounts)
    banks = {s.bank for s in statements}
    assert banks == {"Savanna Bank", "Lagoon Bank", "Sahel Bank"}
    for statement in statements:
        expected = accounts[statement.account_number]
        assert statement.account_name == expected["name"]
        assert len(statement.transactions) == expected["transactions"]
        assert statement.balance_breaks.empty
        assert statement.warnings == []


def test_year_first_dates_are_not_swapped():
    # 2026-05-04 must stay 4 May; reading it "day first" gives 5 April.
    parsed = parse_dates(pd.Series(["2026-05-04 14:04:00", "2026-05-13 09:00:00"]))
    assert parsed.iloc[0] == pd.Timestamp("2026-05-04 14:04:00")


def test_day_first_dates():
    parsed = parse_dates(pd.Series(["04/05/2026 10:00", "13/05/2026 11:30"]))
    assert parsed.iloc[0] == pd.Timestamp("2026-05-04 10:00")


def test_counterparty_extraction():
    assert parse_counterparty("NIP TRF FROM BELLO AISHA MUSA/1234567890/DELTA BANK") == (
        "1234567890",
        "BELLO AISHA MUSA",
    )
    assert parse_counterparty("TRANSFER/1234567890/OKAFOR EMEKA/OUTWARD") == ("1234567890", "OKAFOR EMEKA")
    assert parse_counterparty("FIP:EZE NGOZI:1234567890") == ("1234567890", "EZE NGOZI")
    assert parse_counterparty("POS PURCHASE SHOPRITE") == ("", "")


def test_channel_classification():
    assert classify_channel("ATM WDL ABJ", "") == "cash_withdrawal"
    assert classify_channel("CASH DEPOSIT BY SELF", "") == "cash_deposit"
    assert classify_channel("NIP TRF TO X/1234567890", "1234567890") == "transfer"


def test_balance_break_detects_edited_row():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=3, freq="D"),
            "description": ["a", "b", "c"],
            "amount": [100.0, -50.0, 20.0],
            "balance": [100.0, 50.0, 90.0],  # should be 70
        }
    )
    breaks = check_balance_continuity(frame)
    assert len(breaks) == 1
    assert breaks["difference"].iloc[0] == 20.0


def test_tampered_statement_is_flagged(case_dir):
    path = sorted(case_dir.glob("statement_savanna_*.csv"))[0]
    lines = path.read_text().splitlines()
    del lines[12]  # remove one transaction row
    statement = ingest_statement("\n".join(lines).encode(), path.name)
    assert len(statement.balance_breaks) >= 1
    assert any("balance break" in warning for warning in statement.warnings)
