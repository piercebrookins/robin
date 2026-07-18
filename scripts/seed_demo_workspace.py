from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


ROOT = Path("RobinWorkspace")
SOURCE = ROOT / "source-data"


def main() -> None:
    SOURCE.mkdir(parents=True, exist_ok=True)
    (ROOT / "generated").mkdir(parents=True, exist_ok=True)
    (ROOT / "sessions").mkdir(parents=True, exist_ok=True)
    (ROOT / "cache").mkdir(parents=True, exist_ok=True)

    rows = [
        {"year": 2024, "quarter": "Q1", "scenario": "actual", "revenue": 9_800_000, "expenses": 7_250_000, "operating_income": 2_550_000},
        {"year": 2024, "quarter": "Q2", "scenario": "actual", "revenue": 10_600_000, "expenses": 7_700_000, "operating_income": 2_900_000},
        {"year": 2024, "quarter": "Q3", "scenario": "actual", "revenue": 11_900_000, "expenses": 8_300_000, "operating_income": 3_600_000},
        {"year": 2024, "quarter": "Q4", "scenario": "actual", "revenue": 12_800_000, "expenses": 8_700_000, "operating_income": 4_100_000},
        {"year": 2024, "quarter": "Q4", "scenario": "forecast", "revenue": 13_200_000, "expenses": 8_900_000, "operating_income": 4_300_000},
    ]
    df = pd.DataFrame(rows)
    df["operating_margin"] = df["operating_income"] / df["revenue"]
    df.to_csv(SOURCE / "finance_2024_quarterly_results.csv", index=False)
    with pd.ExcelWriter(SOURCE / "finance_2024_quarterly_results.xlsx") as writer:
        df.to_excel(writer, sheet_name="Quarterly Results", index=False)

    (SOURCE / "finance_context_report.pdf").write_bytes(_minimal_pdf())
    (SOURCE / "calendar_demo.ics").write_text(_demo_calendar())
    print(f"Seeded demo workspace at {ROOT.resolve()}")


def _demo_calendar() -> str:
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=5)
    end = start + timedelta(minutes=30)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//Robin//Demo Calendar//EN\n"
        "BEGIN:VEVENT\n"
        f"UID:robin-demo-{stamp}\n"
        f"DTSTAMP:{stamp}\n"
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\n"
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}\n"
        "SUMMARY:Robin Demo Finance Review\n"
        "DESCRIPTION:Join with Google Meet: https://meet.google.com/abc-defg-hij\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )


def _minimal_pdf() -> bytes:
    text = "Robin Finance Context Report: 2024 growth improved through Q4. Actuals are preferred over forecasts for board reporting."
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj",
    ]
    body = "%PDF-1.4\n" + "\n".join(objects) + "\n"
    offsets = []
    cursor = len("%PDF-1.4\n")
    for obj in objects:
        offsets.append(cursor)
        cursor += len(obj) + 1
    xref_start = len(body)
    xref = "xref\n0 6\n0000000000 65535 f \n" + "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    trailer = f"trailer << /Root 1 0 R /Size 6 >>\nstartxref\n{xref_start}\n%%EOF\n"
    return (body + xref + trailer).encode("latin-1")


if __name__ == "__main__":
    main()
