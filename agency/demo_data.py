#!/usr/bin/env python3
"""Fill a throwaway DB with made-up applications, so the dashboard has something to show.

    AGENCY_DB=agency/demo.db python3 agency/demo_data.py
    AGENCY_DB=agency/demo.db streamlit run agency/app.py

Every company, role and posting below is fictional.
"""
import random
from datetime import datetime, timedelta, timezone

import agency

JD = ("We're looking for a data professional to build and maintain our reporting layer. "
      "You'll model data in SQL, publish Power BI dashboards, and work with finance and "
      "operations to define metrics. Experience with dbt, Python, or a cloud warehouse "
      "is a plus. Full-time, hybrid, 3 days in the office.")

FIT = """**Verdict** — apply

**Match** — {score}%. SQL and Power BI line up directly; dbt is the main gap.

**Strengths**
- Built the month-end reporting model in SQL and Power BI end to end
- Has worked with finance stakeholders on metric definitions

**Gaps**
- No production dbt experience
- Cloud warehouse exposure is limited to one migration project

**Side project ideas**
- Rebuild one existing report as a small dbt project on DuckDB
"""

# company, role, status, days ago, fit score
ROWS = [
    ("Northwind Analytics", "Analytics Engineer", "found", 0, None),
    ("Bluegum Energy", "BI Developer", "found", 1, 64),
    ("Harbourline Insurance", "Data Analyst", "found", 1, 81),
    ("Kestrel Logistics", "Reporting Analyst", "discarded", 3, 42),
    ("Larkspur Health", "Data Engineer", "tailoring", 2, 73),
    ("Copperleaf Retail", "BI Analyst", "awaiting_approval", 2, 78),
    ("Riverbend Super", "Data & Insights Analyst", "applied", 5, 76),
    ("Saltbush Telecom", "Analytics Engineer", "applied", 11, 70),
    ("Ironbark Bank", "Data Analyst", "applied", 16, 68),
    ("Wattle Group", "Power BI Developer", "screening", 9, 85),
    ("Paperbark Media", "Data Engineer", "interview", 14, 80),
    ("Silverton Freight", "BI Engineer", "rejected", 21, 61),
    ("Mallee Foods", "Reporting Analyst", "rejected", 25, 66),
    ("Coastal Water", "Data Analyst", "closed", 30, 72),
]


def main():
    random.seed(7)
    conn = agency.connect()
    if conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]:
        raise SystemExit(f"{agency.DB_PATH} already has data; point AGENCY_DB at a new file.")
    now = datetime.now(timezone.utc)
    for company, role, status, days, score in ROWS:
        ts = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        i = agency.add(conn, company, role, url=f"https://example.com/jobs/{company.split()[0].lower()}",
                       jd_text=JD, status="found", agent="scout", created_at=ts)
        if score is not None:
            agency.set_fit(conn, i, score, FIT.format(score=score))
        if status != "found":
            agency.set_status(conn, i, status)
            conn.execute("UPDATE applications SET updated_at = ? WHERE id = ?", (ts, i))
    for agent in ("scout", "fit", "fit", "tailor", "screen"):
        agency.record_run(conn, agent, {
            "duration_ms": random.randint(20_000, 180_000),
            "total_cost_usd": round(random.uniform(0.05, 0.6), 2), "is_error": False,
            "result": "done",
            "usage": {"input_tokens": random.randint(5_000, 60_000),
                      "output_tokens": random.randint(500, 4_000)},
        })
    print(f"demo data written to {agency.DB_PATH}")


if __name__ == "__main__":
    main()
