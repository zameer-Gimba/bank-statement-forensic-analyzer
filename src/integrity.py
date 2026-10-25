"""
Evidence integrity for the Bank Statement Forensic Analyzer.

- Evidence register: every statement file is fingerprinted with SHA-256
  on arrival, so anyone can later prove the analysed file is the one
  received (re-hash it and compare).
- Audit log: every action is recorded in a hash chain. Each entry stores
  the hash of the previous entry, so editing, inserting or deleting any
  past entry breaks the chain and is detected by verify().

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

GENESIS = "0" * 64


def sha256_bytes(content: bytes) -> str:
    """
    SHA-256 fingerprint of a file's contents.
    """

    return hashlib.sha256(content).hexdigest()


def _entry_hash(entry: dict) -> str:
    payload = {key: value for key, value in entry.items() if key != "hash"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass
class AuditLog:
    """
    Append-only, hash-chained log of analyst actions.
    """

    case_reference: str
    entries: list[dict] = field(default_factory=list)

    @property
    def head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else GENESIS

    def record(self, analyst: str, action: str, details: str = "") -> dict:
        """
        Append an entry chained to the previous one.
        """

        entry = {
            "sequence": len(self.entries) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "case_reference": self.case_reference,
            "analyst": analyst or "unspecified",
            "action": action,
            "details": details,
            "previous_hash": self.head,
        }
        entry["hash"] = _entry_hash(entry)

        self.entries.append(entry)

        return entry

    def verify(self) -> tuple[bool, str]:
        """
        Check the whole chain.

        Returns
        -------
        tuple[bool, str]
            (intact, message)
        """

        previous = GENESIS

        for position, entry in enumerate(self.entries, start=1):

            if entry.get("previous_hash") != previous:
                return False, f"Chain broken at entry {position}: previous-hash mismatch."

            if _entry_hash(entry) != entry.get("hash"):
                return False, f"Entry {position} was modified after it was recorded."

            previous = entry["hash"]

        return True, f"All {len(self.entries)} entries intact (chain head {previous[:16]}...)."

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(entry, sort_keys=True) for entry in self.entries)

    @classmethod
    def from_jsonl(cls, text: str) -> "AuditLog":
        entries = [json.loads(line) for line in text.splitlines() if line.strip()]
        reference = entries[0]["case_reference"] if entries else ""
        return cls(case_reference=reference, entries=entries)
