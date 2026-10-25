"""
Tests for the audit log and report.
"""

from src.integrity import AuditLog
from src.report import build_case_report


def test_audit_chain_detects_edits_and_deletions():
    log = AuditLog("CASE/1")
    for action in ["ingest", "view", "export"]:
        log.record("Analyst", action)
    assert log.verify()[0]

    edited = AuditLog.from_jsonl(log.to_jsonl())
    edited.entries[1]["details"] = "changed"
    assert not edited.verify()[0]

    deleted = AuditLog.from_jsonl(log.to_jsonl())
    del deleted.entries[1]
    assert not deleted.verify()[0]


def test_case_report_builds(analysis):
    log = AuditLog("CASE/1")
    log.record("Analyst", "ingest")
    pdf = build_case_report(analysis, "CASE/1", "Analyst", log)
    assert pdf.startswith(b"%PDF")
