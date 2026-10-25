"""
Synthetic case generator for the Bank Statement Forensic Analyzer.

Creates a realistic investigation case: bank statements for a set of
subject accounts held at three fictional Nigerian banks, each bank with
its own export format (CSV, Excel, PDF), header block, date format and
narration style - the way statements actually arrive from different
banks.

Known money-laundering typologies are planted in the data and written to
a ground-truth file, so detection accuracy can be measured. All names,
account numbers and banks are fictional; no real customer data is used.

Usage:
    python -m src.generator            # writes data/demo_case/

Author: Muhammad Ibrahim Gimba
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config.settings import DEMO_CASE_DIR, DEMO_TRUTH_FILE, RANDOM_STATE

FIRST_NAMES = [
    "Chinedu",
    "Aisha",
    "Tunde",
    "Ngozi",
    "Ibrahim",
    "Fatima",
    "Emeka",
    "Bisi",
    "Musa",
    "Halima",
    "Segun",
    "Amaka",
    "Yusuf",
    "Zainab",
    "Kelechi",
    "Funmi",
    "Abdullahi",
    "Chioma",
    "Babatunde",
    "Hauwa",
    "Obinna",
    "Kemi",
    "Sani",
    "Nkechi",
    "Olumide",
    "Maryam",
    "Ifeanyi",
    "Adaeze",
    "Garba",
    "Folake",
    "Uche",
    "Rukayya",
    "Femi",
    "Ebere",
]

SURNAMES = [
    "Okafor",
    "Bello",
    "Adeyemi",
    "Eze",
    "Mohammed",
    "Okonkwo",
    "Balogun",
    "Abubakar",
    "Nwosu",
    "Ogunleye",
    "Danjuma",
    "Okeke",
    "Lawal",
    "Umar",
    "Afolabi",
    "Chukwu",
    "Aliyu",
    "Obi",
    "Ajayi",
    "Yakubu",
    "Nnamdi",
    "Salisu",
    "Oyelaran",
    "Ibe",
    "Tijani",
    "Onyeka",
    "Garba",
    "Adebayo",
]

BUSINESS_WORDS = [
    "Global",
    "Ventures",
    "Resources",
    "Enterprises",
    "Logistics",
    "Concepts",
    "Integrated",
    "Services",
    "Trading",
    "Links",
    "Hub",
]

# Fictional banks: (code, display name, export format)
BANKS = {
    "SVB": "Savanna Bank",
    "LGB": "Lagoon Bank",
    "SHB": "Sahel Bank",
}

EXTERNAL_BANKS = ["Delta Bank", "Plateau Bank", "Benue Bank", "Kano Bank"]

PERIOD_START = datetime(2026, 1, 1)
PERIOD_END = datetime(2026, 6, 30, 23, 59)


@dataclass
class Account:
    """A bank account in the synthetic world."""

    number: str
    name: str
    bank: str
    subject: bool = False
    role: str = "normal"
    is_corporate: bool = False
    typologies: list[str] = field(default_factory=list)
    # Patterns that are a natural side-effect of the planted scheme: not
    # penalised if detected, not required either.
    secondary: list[str] = field(default_factory=list)


@dataclass
class Event:
    """One movement of money."""

    time: datetime
    amount: float
    source: Account | None
    target: Account | None
    channel: str
    note: str = ""


class CaseGenerator:
    """
    Generate a synthetic investigation case with planted typologies.
    """

    def __init__(self, seed: int = RANDOM_STATE) -> None:
        self.rng = np.random.default_rng(seed)
        self.used_numbers: set[str] = set()
        self.events: list[Event] = []
        self.subjects: list[Account] = []

    # ------------------------------------------------------------ helpers

    def _nuban(self) -> str:
        while True:
            number = "".join(str(d) for d in self.rng.integers(0, 10, 10))
            if number[0] != "0" and number not in self.used_numbers:
                self.used_numbers.add(number)
                return number

    def _person(self) -> str:
        return (
            f"{self.rng.choice(SURNAMES)} {self.rng.choice(FIRST_NAMES)} " f"{self.rng.choice(FIRST_NAMES)}"
        ).upper()

    def _business(self) -> str:
        words = self.rng.choice(BUSINESS_WORDS, size=2, replace=False)
        return f"{self.rng.choice(SURNAMES)} {words[0]} {words[1]} LTD".upper()

    def _external(self, corporate: bool = False) -> Account:
        return Account(
            number=self._nuban(),
            name=self._business() if corporate else self._person(),
            bank=str(self.rng.choice(EXTERNAL_BANKS)),
            is_corporate=corporate,
        )

    def _subject(
        self,
        bank: str,
        role: str,
        corporate: bool = False,
        typologies: list[str] | None = None,
        secondary: list[str] | None = None,
    ) -> Account:
        account = Account(
            number=self._nuban(),
            name=self._business() if corporate else self._person(),
            bank=BANKS[bank],
            subject=True,
            role=role,
            is_corporate=corporate,
            typologies=typologies or [],
            secondary=secondary or [],
        )
        self.subjects.append(account)
        return account

    def _time(self, start: datetime, end: datetime, business_hours: bool = True) -> datetime:
        seconds = (end - start).total_seconds()
        moment = start + timedelta(seconds=float(self.rng.uniform(0, seconds)))
        if business_hours:
            moment = moment.replace(
                hour=int(self.rng.integers(7, 21)),
                minute=int(self.rng.integers(0, 60)),
            )
        return moment

    def _amount(self, low: float, high: float, round_to: int = 1) -> float:
        value = float(self.rng.uniform(low, high))
        return float(round(value / round_to) * round_to)

    def _add(self, *args, **kwargs) -> None:
        self.events.append(Event(*args, **kwargs))

    # ---------------------------------------------------------- behaviour

    def _background(self, account: Account, monthly_income: float, busy: float) -> None:
        """
        Ordinary activity: income, bills, POS spending, small transfers.
        """

        months = pd.date_range(PERIOD_START, PERIOD_END, freq="MS")

        for month_start in months:
            month_end = month_start + pd.offsets.MonthEnd(1)

            self._add(
                self._time(month_start + timedelta(days=24), month_start + timedelta(days=27)),
                round(monthly_income * self.rng.uniform(0.95, 1.05), -2),
                self._external(corporate=True),
                account,
                "SALARY",
            )

            for _ in range(int(self.rng.poisson(12 * busy))):
                self._add(
                    self._time(month_start, month_end),
                    self._amount(1_500, 45_000, 50),
                    account,
                    None,
                    "POS",
                )

            for _ in range(int(self.rng.poisson(4 * busy))):
                self._add(
                    self._time(month_start, month_end),
                    self._amount(10_000, 120_000, 1_000),
                    account,
                    self._external(),
                    "TRANSFER",
                )

            for _ in range(int(self.rng.poisson(2 * busy))):
                self._add(
                    self._time(month_start, month_end),
                    self._amount(5_000, 80_000, 1_000),
                    self._external(),
                    account,
                    "TRANSFER",
                )

            for _ in range(int(self.rng.poisson(2))):
                self._add(
                    self._time(month_start, month_end),
                    float(self.rng.choice([1_000, 2_000, 5_000, 10_000])),
                    account,
                    None,
                    "AIRTIME",
                )

            if self.rng.random() < 0.7:
                self._add(
                    self._time(month_start, month_end),
                    float(self.rng.choice([20_000, 40_000, 60_000])),
                    account,
                    None,
                    "ATM",
                )

    def _business_activity(self, account: Account) -> None:
        """
        A legitimate retailer: many small customer payments, supplier
        payments - a realistic source of false positives.
        """

        days = pd.date_range(PERIOD_START, PERIOD_END, freq="D")

        for day in days:
            for _ in range(int(self.rng.poisson(5))):
                self._add(
                    self._time(day, day + timedelta(hours=23)),
                    self._amount(3_000, 60_000, 500),
                    self._external(),
                    account,
                    "TRANSFER",
                )
            if day.weekday() == 0:
                self._add(
                    self._time(day, day + timedelta(hours=23)),
                    self._amount(400_000, 1_200_000, 1_000),
                    account,
                    self._external(corporate=True),
                    "TRANSFER",
                )

    def _mule_ring(self, collector: Account, mules: list[Account], boss: Account) -> None:
        """
        Fraud proceeds collected from many victims, layered through mule
        accounts within hours, partly cashed out, and consolidated.
        """

        for wave_start in [datetime(2026, 2, 9), datetime(2026, 3, 16), datetime(2026, 4, 20)]:

            total = 0.0

            for _ in range(int(self.rng.integers(12, 18))):
                amount = self._amount(150_000, 950_000, 1_000)
                total += amount
                self._add(
                    self._time(wave_start, wave_start + timedelta(days=4)),
                    amount,
                    self._external(),
                    collector,
                    "TRANSFER",
                )

            forward_time = wave_start + timedelta(days=4, hours=6)
            share = total * 0.95 / len(mules)

            for mule in mules:
                self._add(
                    forward_time + timedelta(minutes=int(self.rng.integers(5, 90))),
                    round(share, -3),
                    collector,
                    mule,
                    "TRANSFER",
                )

                cash = round(share * self.rng.uniform(0.25, 0.4), -3)
                for split in np.array_split(np.arange(4), 2):
                    self._add(
                        forward_time + timedelta(hours=int(self.rng.integers(2, 10))),
                        round(cash / 2, -3),
                        mule,
                        None,
                        "ATM" if split[0] == 0 else "POS_WITHDRAWAL",
                    )

                self._add(
                    forward_time + timedelta(hours=int(self.rng.integers(10, 30))),
                    round(share - cash - 20_000, -3),
                    mule,
                    boss,
                    "TRANSFER",
                )

    def _facilitator(self, facilitator: Account, payers: list[Account]) -> None:
        """
        One outside account paid by several subjects - the kind of link
        that tells investigators whose statements to request next.
        """

        for payer in payers:
            for _ in range(2):
                self._add(
                    self._time(datetime(2026, 2, 1), datetime(2026, 6, 1)),
                    self._amount(250_000, 900_000, 10_000),
                    payer,
                    facilitator,
                    "TRANSFER",
                )

    def _distribution(self, boss: Account) -> None:
        """
        The consolidating account pays out to many recipients quickly.
        """

        start = datetime(2026, 5, 4)

        for _ in range(int(self.rng.integers(10, 14))):
            self._add(
                self._time(start, start + timedelta(days=3)),
                self._amount(300_000, 1_500_000, 10_000),
                boss,
                self._external(),
                "TRANSFER",
            )

    def _structuring(self, account: Account) -> None:
        """
        Repeated deposits just under the NGN 5m reporting threshold.
        """

        start = datetime(2026, 3, 2)

        for day in sorted(self.rng.choice(np.arange(12), size=7, replace=False)):
            self._add(
                self._time(start + timedelta(days=int(day)), start + timedelta(days=int(day), hours=8)),
                self._amount(4_550_000, 4_980_000, 10_000),
                None,
                account,
                "CASH_DEPOSIT",
            )

        self._add(
            datetime(2026, 3, 16, 11, 20),
            30_000_000,
            account,
            self._external(corporate=True),
            "TRANSFER",
        )

    def _round_trip(self, ring: list[Account]) -> None:
        """
        Funds move A -> B -> C -> A to disguise their origin.
        """

        moment = datetime(2026, 4, 6, 10, 0)
        amount = 8_000_000.0

        for _ in range(2):
            for source, target in zip(ring, ring[1:] + ring[:1]):
                self._add(moment, amount, source, target, "TRANSFER")
                moment += timedelta(days=int(self.rng.integers(1, 4)), hours=int(self.rng.integers(1, 6)))
                amount = round(amount * self.rng.uniform(0.95, 0.99), -3)
            moment += timedelta(days=20)
            amount = 6_500_000.0

    def _dormant(self, account: Account) -> None:
        """
        Little activity, a long silence, then a sudden large flow.
        """

        for day in [5, 18]:
            self._add(datetime(2026, 1, day, 12), 15_000.0, self._external(), account, "TRANSFER")

        burst = datetime(2026, 5, 18)
        for _ in range(4):
            amount = self._amount(1_500_000, 3_000_000, 10_000)
            self._add(
                self._time(burst, burst + timedelta(days=6)),
                amount,
                self._external(corporate=True),
                account,
                "TRANSFER",
            )
            self._add(
                self._time(burst + timedelta(days=1), burst + timedelta(days=9)),
                round(amount * 0.97, -3),
                account,
                self._external(),
                "TRANSFER",
            )

    def _legit_large(self, account: Account) -> None:
        """
        A legitimate contractor with large but regular payments.
        """

        for month in range(1, 7):
            self._add(
                datetime(2026, month, 10, 10),
                self._amount(9_000_000, 14_000_000, 100_000),
                self._external(corporate=True),
                account,
                "TRANSFER",
            )
            for _ in range(3):
                self._add(
                    datetime(2026, month, int(self.rng.integers(12, 27)), 14),
                    self._amount(1_500_000, 3_500_000, 10_000),
                    account,
                    self._external(corporate=True),
                    "TRANSFER",
                )

    # -------------------------------------------------------------- build

    def build(self) -> None:
        """
        Create subject accounts and all of their activity.
        """

        collector = self._subject("SVB", "collector", typologies=["fan_in", "pass_through"])
        mules = [
            self._subject("LGB", "mule", typologies=["pass_through", "rapid_cash_out"]),
            self._subject("SHB", "mule", typologies=["pass_through", "rapid_cash_out"]),
            self._subject("SVB", "mule", typologies=["pass_through", "rapid_cash_out"]),
        ]
        boss = self._subject("LGB", "consolidator", typologies=["fan_out"], secondary=["pass_through"])
        structurer = self._subject("SHB", "structurer", typologies=["structuring"])
        ring = [
            self._subject(
                "SVB", "round_trip", corporate=True, typologies=["round_trip"], secondary=["pass_through"]
            ),
            self._subject(
                "LGB", "round_trip", corporate=True, typologies=["round_trip"], secondary=["pass_through"]
            ),
            self._subject(
                "SHB", "round_trip", corporate=True, typologies=["round_trip"], secondary=["pass_through"]
            ),
        ]
        dormant = self._subject(
            "SVB", "dormant", typologies=["dormant_reactivation"], secondary=["pass_through"]
        )
        salaried = self._subject("LGB", "normal")
        retailer = self._subject("SHB", "normal_business", corporate=True)
        contractor = self._subject("SVB", "normal_contractor", corporate=True)

        for account in [collector, *mules, boss, structurer, *ring, salaried]:
            self._background(account, monthly_income=float(self.rng.uniform(180_000, 650_000)), busy=1.0)

        self._background(retailer, monthly_income=0.0, busy=0.5)
        self._background(contractor, monthly_income=0.0, busy=0.6)

        self._business_activity(retailer)
        self._legit_large(contractor)
        self._mule_ring(collector, mules, boss)
        self._distribution(boss)
        self._structuring(structurer)
        self._round_trip(ring)
        self._dormant(dormant)

        facilitator = self._external()
        self.facilitator = facilitator
        self._facilitator(facilitator, [collector, boss, structurer])

        self.events = [
            event for event in self.events if event.amount > 0 and PERIOD_START <= event.time <= PERIOD_END
        ]

    # ---------------------------------------------------------- statements

    def statement(self, account: Account) -> pd.DataFrame:
        """
        Canonical statement rows (before bank-specific formatting).
        """

        rows = []

        for event in self.events:
            if event.target is account:
                direction, other = "CR", event.source
            elif event.source is account:
                direction, other = "DR", event.target
            else:
                continue

            rows.append(
                {
                    "time": event.time,
                    "direction": direction,
                    "amount": event.amount,
                    "channel": event.channel,
                    "other_name": other.name if other else "",
                    "other_number": other.number if other else "",
                    "other_bank": other.bank if other else "",
                }
            )

        frame = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)

        signed = np.where(frame["direction"] == "CR", frame["amount"], -frame["amount"])
        running = np.cumsum(signed)
        opening = max(0.0, -running.min()) + float(self.rng.uniform(20_000, 300_000))
        frame["balance"] = np.round(opening + running, 2)
        frame["opening_balance"] = opening

        return frame


# ------------------------------------------------------------------ formats


def _narration_savanna(row: pd.Series) -> str:
    channel = row["channel"]
    if channel == "TRANSFER":
        word = "FROM" if row["direction"] == "CR" else "TO"
        return f"NIP TRF {word} {row['other_name']}/{row['other_number']}/{row['other_bank'].upper()}"
    if channel == "SALARY":
        return f"SALARY PAYMENT {row['other_name']}/{row['other_number']}"
    if channel == "ATM":
        return "ATM WDL ABJ WUSE 2 BRANCH"
    if channel == "POS_WITHDRAWAL":
        return "POS CASH WDL AGENT KUBWA"
    if channel == "POS":
        return "POS PURCHASE SHOPRITE ABUJA"
    if channel == "CASH_DEPOSIT":
        return "CASH DEPOSIT BY SELF"
    return "AIRTIME RECHARGE MTN"


def _narration_lagoon(row: pd.Series) -> str:
    channel = row["channel"]
    if channel in ("TRANSFER", "SALARY"):
        return f"TRANSFER/{row['other_number']}/{row['other_name']}/{'INWARD' if row['direction'] == 'CR' else 'OUTWARD'}"
    if channel == "ATM":
        return "CASH WITHDRAWAL ATM LAGOS ISLAND"
    if channel == "POS_WITHDRAWAL":
        return "POS WITHDRAWAL MONIEPOINT AGENT"
    if channel == "POS":
        return "CARD PAYMENT JUMIA ONLINE"
    if channel == "CASH_DEPOSIT":
        return "CASH LODGEMENT COUNTER"
    return "VTU AIRTIME GLO"


def _narration_sahel(row: pd.Series) -> str:
    channel = row["channel"]
    if channel in ("TRANSFER", "SALARY"):
        prefix = "FIP" if row["direction"] == "CR" else "FOP"
        return f"{prefix}:{row['other_name']}:{row['other_number']}"
    if channel == "ATM":
        return "ATM CASH WITHDRAWAL KANO"
    if channel == "POS_WITHDRAWAL":
        return "POS CASH-OUT AGENT"
    if channel == "POS":
        return "WEB/POS PAYMENT"
    if channel == "CASH_DEPOSIT":
        return "CASH DEPOSIT"
    return "AIRTIME TOPUP 9MOBILE"


def write_savanna_csv(account: Account, frame: pd.DataFrame, path: Path) -> None:
    """
    Savanna Bank: CSV, metadata header block, separate debit/credit
    columns, dates like 05-Feb-2026 14:32.
    """

    table = pd.DataFrame(
        {
            "Trans Date": frame["time"].dt.strftime("%d-%b-%Y %H:%M"),
            "Value Date": frame["time"].dt.strftime("%d-%b-%Y"),
            "Narration": frame.apply(_narration_savanna, axis=1),
            "Debit": np.where(frame["direction"] == "DR", frame["amount"].map("{:,.2f}".format), ""),
            "Credit": np.where(frame["direction"] == "CR", frame["amount"].map("{:,.2f}".format), ""),
            "Balance": frame["balance"].map("{:,.2f}".format),
        }
    )

    with path.open("w", newline="") as handle:
        handle.write("SAVANNA BANK PLC - CUSTOMER STATEMENT\n")
        handle.write(f"Account Name:,{account.name}\n")
        handle.write(f"Account Number:,{account.number}\n")
        handle.write("Period:,01-Jan-2026 to 30-Jun-2026\n")
        handle.write(f"Opening Balance:,\"{frame['opening_balance'].iloc[0]:,.2f}\"\n")
        handle.write("\n")
        table.to_csv(handle, index=False)


def write_lagoon_xlsx(account: Account, frame: pd.DataFrame, path: Path) -> None:
    """
    Lagoon Bank: Excel, signed amount column, ISO timestamps.
    """

    table = pd.DataFrame(
        {
            "Date": frame["time"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Description": frame.apply(_narration_lagoon, axis=1),
            "Amount": np.where(frame["direction"] == "CR", frame["amount"], -frame["amount"]),
            "Balance": frame["balance"],
        }
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        meta = pd.DataFrame(
            [
                ["LAGOON BANK LIMITED", ""],
                ["Customer", account.name],
                ["Account No", account.number],
                ["Statement Period", "2026-01-01 to 2026-06-30"],
            ]
        )
        meta.to_excel(writer, index=False, header=False, startrow=0)
        table.to_excel(writer, index=False, startrow=5)


def write_sahel_pdf(account: Account, frame: pd.DataFrame, path: Path) -> None:
    """
    Sahel Bank: PDF, withdrawals/lodgements columns, dd/mm/yyyy dates.
    """

    styles = getSampleStyleSheet()

    document = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )

    header = [
        Paragraph("SAHEL BANK - STATEMENT OF ACCOUNT", styles["Heading2"]),
        Paragraph(f"Account Name: {account.name}", styles["Normal"]),
        Paragraph(f"Account Number: {account.number}", styles["Normal"]),
        Paragraph("Period: 01/01/2026 - 30/06/2026", styles["Normal"]),
        Spacer(1, 4 * mm),
    ]

    rows = [["Posted Date", "Details", "Withdrawals", "Lodgements", "Balance"]]

    for _, row in frame.iterrows():
        rows.append(
            [
                row["time"].strftime("%d/%m/%Y %H:%M"),
                _narration_sahel(row),
                f"{row['amount']:,.2f}" if row["direction"] == "DR" else "",
                f"{row['amount']:,.2f}" if row["direction"] == "CR" else "",
                f"{row['balance']:,.2f}",
            ]
        )

    table = Table(rows, colWidths=[32 * mm, 120 * mm, 35 * mm, 35 * mm, 38 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )

    document.build(header + [table])


WRITERS = {
    "Savanna Bank": (write_savanna_csv, "csv"),
    "Lagoon Bank": (write_lagoon_xlsx, "xlsx"),
    "Sahel Bank": (write_sahel_pdf, "pdf"),
}


def generate_case(output_dir: Path = DEMO_CASE_DIR, seed: int = RANDOM_STATE) -> dict:
    """
    Generate a full case and write statements plus ground truth.

    Returns
    -------
    dict
        The ground truth: subject accounts, their roles and the
        typologies planted in each.
    """

    generator = CaseGenerator(seed)
    generator.build()

    output_dir.mkdir(parents=True, exist_ok=True)

    for old in output_dir.glob("statement_*"):
        old.unlink()

    truth = {"accounts": {}}

    for account in generator.subjects:
        frame = generator.statement(account)
        writer, extension = WRITERS[account.bank]
        slug = account.bank.split()[0].lower()
        writer(account, frame, output_dir / f"statement_{slug}_{account.number}.{extension}")

        truth["accounts"][account.number] = {
            "name": account.name,
            "bank": account.bank,
            "role": account.role,
            "typologies": account.typologies,
            "secondary": account.secondary,
            "transactions": int(len(frame)),
        }

    truth["linked_external"] = {
        generator.facilitator.number: generator.facilitator.name,
    }

    truth_path = output_dir / DEMO_TRUTH_FILE.name
    truth_path.write_text(json.dumps(truth, indent=2))

    return truth


def main() -> None:
    truth = generate_case()
    total = sum(item["transactions"] for item in truth["accounts"].values())
    print(f"Generated {len(truth['accounts'])} statements, {total:,} transactions -> {DEMO_CASE_DIR}")


if __name__ == "__main__":
    main()
