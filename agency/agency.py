#!/usr/bin/env python3
"""Job search pipeline store. Agents talk to the DB only through this CLI."""

import argparse
import html
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("AGENCY_DB", ROOT / "agency" / "agency.db"))

STATUSES = [
    "found",
    "discarded",
    "tailoring",
    "awaiting_approval",
    "applied",
    "screening",
    "interview",
    "offer",
    "rejected",
    "closed",  # posting withdrawn/expired before an outcome — not a rejection
]
EMPLOYMENT_TYPES = ["full_time", "contract", "unknown"]
STALE_STATUSES = ("applied", "screening")
STALE_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY,
    company         TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT '',
    url             TEXT,
    jd_text         TEXT,
    status          TEXT NOT NULL DEFAULT 'found',
    employment_type TEXT NOT NULL DEFAULT 'full_time',
    draft_path      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    agent         TEXT NOT NULL,
    app_id        INTEGER,
    ran_at        TEXT NOT NULL DEFAULT (datetime('now')),
    duration_ms   INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    ok            INTEGER,  -- NULL while running; 0/1 once finish_run() sets it
    summary       TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    app_id  INTEGER NOT NULL REFERENCES applications(id),
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    agent   TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id       INTEGER PRIMARY KEY,
    app_id   INTEGER NOT NULL REFERENCES applications(id),
    question TEXT NOT NULL,
    answer   TEXT NOT NULL,
    ts       TEXT NOT NULL DEFAULT (datetime('now')),
    kind     TEXT NOT NULL DEFAULT 'screening'
);
"""
QUESTION_KINDS = ("screening", "interview")


def connect(path=DB_PATH):
    # autocommit (isolation_level=None): a run started by a background thread on its
    # own connection must be visible to the dashboard's polling connection as soon as
    # it commits, not just at the next transaction boundary.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(applications)")}
    for column, decl in (("fit_text", "TEXT"), ("fit_score", "INTEGER")):
        if column not in have:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {decl}")
    if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(questions)")}:
        conn.execute("ALTER TABLE questions ADD COLUMN kind TEXT NOT NULL DEFAULT 'screening'")

    # early runs.ok was NOT NULL DEFAULT 1; a pending (not-yet-finished) run needs to
    # store NULL there, and SQLite can't ALTER a column's NOT NULL away in place.
    ok_notnull = next((r[3] for r in conn.execute("PRAGMA table_info(runs)")
                       if r[1] == "ok"), 0)
    if ok_notnull:
        conn.executescript("""
            ALTER TABLE runs RENAME TO runs_old;
            CREATE TABLE runs (
                id            INTEGER PRIMARY KEY,
                agent         TEXT NOT NULL,
                app_id        INTEGER,
                ran_at        TEXT NOT NULL DEFAULT (datetime('now')),
                duration_ms   INTEGER,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                cost_usd      REAL,
                ok            INTEGER,
                summary       TEXT
            );
            INSERT INTO runs SELECT * FROM runs_old;
            DROP TABLE runs_old;
        """)
    return conn


def log_event(conn, app_id, agent, message):
    conn.execute(
        "INSERT INTO events (app_id, agent, message) VALUES (?, ?, ?)",
        (app_id, agent, message),
    )
    conn.commit()


def add(conn, company, role="", url=None, jd_text=None,
        employment_type="full_time", status="found", agent="human",
        created_at=None):
    if employment_type not in EMPLOYMENT_TYPES:
        raise ValueError(f"employment_type must be one of {EMPLOYMENT_TYPES}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    cur = conn.execute(
        """INSERT INTO applications
           (company, role, url, jd_text, employment_type, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?,
                   COALESCE(?, datetime('now')), COALESCE(?, datetime('now')))""",
        (company, role, url, jd_text, employment_type, status, created_at, created_at),
    )
    conn.commit()
    log_event(conn, cur.lastrowid, agent, f"added ({status})")
    return cur.lastrowid


def set_status(conn, app_id, status, agent="human", draft_path=None, notes=None):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    conn.execute(
        """UPDATE applications
           SET status = ?,
               draft_path = COALESCE(?, draft_path),
               notes = COALESCE(?, notes),
               updated_at = datetime('now')
           WHERE id = ?""",
        (status, draft_path, notes, app_id),
    )
    conn.commit()
    log_event(conn, app_id, agent, f"status -> {status}")


BLOCKED_HOSTS = ("linkedin.com", "seek.com.au")


def fetch_jd(url, timeout=25):
    """Pull the readable text off a job posting page.

    Employer career sites and Built In serve the description in the HTML, so plain
    urllib is enough. LinkedIn and Seek do not — they answer with a login wall or a
    403 — so say that instead of storing a page of navigation chrome.
    """
    import re
    import urllib.request

    if any(h in url for h in BLOCKED_HOSTS):
        raise ValueError("LinkedIn and SEEK block automated reads. Paste the description in instead.")
    # A bare "Mozilla/5.0" reads as a bot to some sites (Canva 403s on it) — a full
    # browser UA string passes the same check a real visitor would.
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    page = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>",
                  " ", page)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", page))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) < 400:
        raise ValueError("Couldn't find the description. Open the link and paste it in.")
    return text


def parse_posting(url):
    """Best-effort (company, role, jd) from a posting URL.

    The role and company come out of the page title, which every job board writes
    differently — treat the result as a prefill to correct, not as fact.
    """
    import re

    jd = fetch_jd(url)
    title = jd.splitlines()[0].strip() if jd else ""
    title = re.split(r"\s+[|·]\s+", title)[0].strip()  # drop the trailing site name
    for sep in (" at ", " - ", " – ", " — "):
        if sep in title:
            role, company = title.split(sep, 1)
            return company.strip(), role.strip(), jd
    return "", title, jd


def set_jd(conn, app_id, jd, agent="human"):
    conn.execute(
        "UPDATE applications SET jd_text = ?, updated_at = datetime('now') WHERE id = ?",
        (jd, app_id),
    )
    conn.commit()
    log_event(conn, app_id, agent, f"JD saved ({len(jd):,} chars)")


def rename(conn, app_id, company, role):
    conn.execute("UPDATE applications SET company = ?, role = ? WHERE id = ?",
                 (company, role, app_id))
    conn.commit()


def add_question(conn, app_id, question, answer, agent="screen", kind="screening"):
    """Log one question and its drafted answer: a screening/form question, or an
    interview question the candidate is preparing for."""
    conn.execute(
        "INSERT INTO questions (app_id, question, answer, kind) VALUES (?, ?, ?, ?)",
        (app_id, question, answer, kind),
    )
    conn.commit()
    log_event(conn, app_id, agent, f"answered a {kind} question")


def questions_for(conn, app_id, kind="screening"):
    return conn.execute(
        "SELECT * FROM questions WHERE app_id = ? AND kind = ? ORDER BY ts DESC",
        (app_id, kind),
    ).fetchall()


def set_fit(conn, app_id, score, text, agent="fit"):
    """Store the fit diagnosis. Deliberately does not change status — reading the
    diagnosis and deciding to pursue the role are two separate acts, and the decision
    is the candidate's."""
    conn.execute(
        "UPDATE applications SET fit_score = ?, fit_text = ? WHERE id = ?",
        (score, text, app_id),
    )
    conn.commit()
    log_event(conn, app_id, agent, f"fit {score}%")


def start_run(conn, agent, app_id=None):
    """Record that an agent has started, before it finishes.

    `duration_ms IS NULL` is the pending marker — `running()` polls on exactly that,
    so a dashboard can show "in progress" while a background thread runs the process.
    """
    cur = conn.execute(
        "INSERT INTO runs (agent, app_id, ok, summary) VALUES (?, ?, NULL, NULL)",
        (agent, app_id),
    )
    return cur.lastrowid


def finish_run(conn, run_id, payload):
    """Fill in a run started with `start_run`, from `claude -p --output-format json`.

    Cached reads are counted as input — they are what the run actually consumed,
    even though they are cheaper, and `cost_usd` already prices them correctly.
    """
    u = payload.get("usage") or {}
    conn.execute(
        """UPDATE runs SET duration_ms = ?, input_tokens = ?, output_tokens = ?,
                           cost_usd = ?, ok = ?, summary = ? WHERE id = ?""",
        (payload.get("duration_ms"),
         (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
         + (u.get("cache_creation_input_tokens") or 0),
         u.get("output_tokens") or 0,
         payload.get("total_cost_usd"),
         0 if payload.get("is_error") else 1,
         (payload.get("result") or "")[:500],
         run_id),
    )


def record_run(conn, agent, payload, app_id=None):
    """Store a completed run in one shot (used by the demo/tests)."""
    finish_run(conn, start_run(conn, agent, app_id), payload)


def running(conn):
    """Runs still in flight — started but not yet finished.

    `elapsed_s` is computed in SQL (julianday delta) rather than in Python, since
    `ran_at` is a naive UTC string ('now' in SQLite) and parsing it as a Python
    datetime then comparing to a local `time.time()` would be off by the timezone
    offset — wrong by hours in Melbourne.
    """
    return conn.execute(
        """SELECT *, (julianday('now') - julianday(ran_at)) * 86400 AS elapsed_s
           FROM runs WHERE duration_ms IS NULL ORDER BY ran_at"""
    ).fetchall()


def last_runs(conn):
    """Most recent *finished* run per agent, with that agent's running totals."""
    return conn.execute(
        """SELECT r.agent, r.ran_at, r.duration_ms, r.ok, t.runs, t.tokens, t.cost
           FROM runs r
           JOIN (SELECT agent, MAX(id) AS last_id, COUNT(*) AS runs,
                        SUM(input_tokens + output_tokens) AS tokens,
                        SUM(cost_usd) AS cost
                 FROM runs WHERE duration_ms IS NOT NULL GROUP BY agent) t
             ON t.last_id = r.id
           ORDER BY r.ran_at DESC"""
    ).fetchall()


def set_notes(conn, app_id, notes, agent="human"):
    """Notes are not progress — deliberately does not bump updated_at, so editing a
    note never hides an application from the follow-up list."""
    conn.execute("UPDATE applications SET notes = ? WHERE id = ?", (notes, app_id))
    conn.commit()
    log_event(conn, app_id, agent, "notes edited")


def rows(conn, full_time_only=False):
    sql = "SELECT * FROM applications"
    if full_time_only:
        sql += " WHERE employment_type = 'full_time'"
    sql += " ORDER BY updated_at DESC"
    return conn.execute(sql).fetchall()


def get(conn, app_id):
    return conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()


def events(conn, app_id):
    return conn.execute(
        "SELECT * FROM events WHERE app_id = ? ORDER BY ts DESC, id DESC", (app_id,)
    ).fetchall()


def dismissed_on(conn):
    """When each discarded posting was actually dismissed, keyed by app id.

    `updated_at` moves on any later edit, so it drifts away from the decision —
    the status-change event is the real record. Postings dismissed before that
    logging existed have no event and are simply absent, so callers fall back to
    `updated_at` for those.
    """
    return {r["app_id"]: r["ts"] for r in conn.execute(
        """SELECT app_id, MAX(ts) AS ts FROM events
           WHERE message = 'status -> discarded' GROUP BY app_id"""
    ).fetchall()}


def duplicates(conn, url=None, company=None, role=None):
    """Postings already in the pipeline that look like the one being added.

    Matches the exact url, or company and role together — the same job reached
    through a different job board carries a different url but the same pair, which
    is how the same posting kept getting added twice.
    """
    clauses, params = [], []
    if url:
        base = url.split("?")[0]
        # match exact url OR same base url (strips tracking params like ?source=LinkedIn)
        clauses.append("(url = ? OR url = ? OR url LIKE ?)")
        params += [url, base, base + "?%"]
    if company:
        clauses.append("(lower(trim(company)) = lower(trim(?)) "
                       "AND lower(trim(COALESCE(role, ''))) = lower(trim(?)))")
        params += [company, role or ""]
    if not clauses:
        return []
    return conn.execute(
        f"SELECT * FROM applications WHERE {' OR '.join(clauses)} ORDER BY id", params
    ).fetchall()


def same_company(conn, company):
    """Every posting at this company, any role. 'Acme' also matches 'Acme Energy' and
    'Acme Health' matches 'Acme Health (Acme Group)', since boards name the same employer
    differently."""
    c = (company or "").strip().lower()
    if not c:
        return []
    return conn.execute(
        """SELECT * FROM applications
           WHERE lower(trim(company)) = ?
              OR lower(trim(company)) LIKE ? || ' %'
              OR ? LIKE lower(trim(company)) || ' %'
           ORDER BY id""", (c, c, c)
    ).fetchall()


def stale(conn, days=STALE_DAYS):
    """Applications sitting in an active status with no update for `days`."""
    placeholders = ",".join("?" * len(STALE_STATUSES))
    return conn.execute(
        f"""SELECT * FROM applications
            WHERE status IN ({placeholders})
              AND updated_at < datetime('now', ?)
            ORDER BY updated_at""",
        (*STALE_STATUSES, f"-{days} day"),
    ).fetchall()


# --- seed ---------------------------------------------------------------

# tailored CVs saved as "CV_<your name>_<company>.pdf"
CV_RE = re.compile(r"^CV_[^_]+_(.+)\.pdf$")
SKIP = {"Master"}


def seed(conn, folder=ROOT):
    """Backfill historical applications from the existing tailored CV filenames."""
    added = 0
    for pdf in sorted(Path(folder).glob("CV_*_*.pdf")):
        m = CV_RE.match(pdf.name)
        if not m:
            continue
        company = m.group(1).strip()
        if company in SKIP:
            continue
        role = ""
        # "Acme Health (Data Analyst)" -> company + role
        if company.endswith(")") and "(" in company:
            company, role = company[:-1].split("(", 1)
            company, role = company.strip(), role.strip()
        if conn.execute(
            "SELECT 1 FROM applications WHERE company = ? AND role = ?", (company, role)
        ).fetchone():
            continue
        ts = datetime.fromtimestamp(pdf.stat().st_mtime, timezone.utc)
        app_id = add(
            conn, company, role, status="applied", agent="seed",
            created_at=ts.strftime("%Y-%m-%d %H:%M:%S"),
        )
        conn.execute(
            "UPDATE applications SET draft_path = ? WHERE id = ?", (str(pdf), app_id)
        )
        conn.commit()
        added += 1
    return added


# --- CLI ----------------------------------------------------------------


def _print(rs):
    for r in rs:
        print(f"{r['id']:>3}  {r['status']:<18} {r['company']} — {r['role'] or '?'}"
              f"  ({r['updated_at']})")


def main(argv=None):
    p = argparse.ArgumentParser(description="job search pipeline store")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the DB")
    s.add_argument("--seed", action="store_true", help="backfill from existing CV files")

    s = sub.add_parser("add", help="add a posting")
    s.add_argument("company")
    s.add_argument("--role", default="")
    s.add_argument("--url")
    s.add_argument("--jd", help="job description text, or - to read stdin")
    s.add_argument("--employment-type", default="full_time", choices=EMPLOYMENT_TYPES)
    s.add_argument("--status", default="found", choices=STATUSES)
    s.add_argument("--agent", default="human")

    s = sub.add_parser("list", help="list applications")
    s.add_argument("--full-time-only", action="store_true")
    s.add_argument("--stale", action="store_true", help="only ones needing follow-up")

    s = sub.add_parser("show", help="print one application and its events")
    s.add_argument("id", type=int)

    s = sub.add_parser("status", help="set status")
    s.add_argument("id", type=int)
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--draft-path")
    s.add_argument("--notes")
    s.add_argument("--agent", default="human")

    s = sub.add_parser("fit", help="store a fit diagnosis")
    s.add_argument("id", type=int)
    s.add_argument("score", type=int, help="match rate 0-100")
    s.add_argument("--text", default="-", help="diagnosis markdown, or - to read stdin")
    s.add_argument("--agent", default="fit")

    s = sub.add_parser("notes", help="set notes without touching progress")
    s.add_argument("id", type=int)
    s.add_argument("notes")
    s.add_argument("--agent", default="human")

    s = sub.add_parser("event", help="append an event")
    s.add_argument("id", type=int)
    s.add_argument("message")
    s.add_argument("--agent", default="human")

    s = sub.add_parser("question", help="log a screening question and its answer")
    s.add_argument("id", type=int)
    s.add_argument("question")
    s.add_argument("--answer", default="-", help="answer text, or - to read stdin")
    s.add_argument("--agent", default="screen")
    s.add_argument("--kind", choices=QUESTION_KINDS, default="screening")

    a = p.parse_args(argv)
    conn = connect()

    if a.cmd == "init":
        n = seed(conn) if a.seed else 0
        print(f"db at {DB_PATH}" + (f", seeded {n} applications" if a.seed else ""))
    elif a.cmd == "add":
        jd = sys.stdin.read() if a.jd == "-" else a.jd
        print(add(conn, a.company, a.role, a.url, jd, a.employment_type, a.status, a.agent))
    elif a.cmd == "list":
        _print(stale(conn) if a.stale else rows(conn, a.full_time_only))
    elif a.cmd == "show":
        r = get(conn, a.id)
        if not r:
            sys.exit(f"no application {a.id}")
        for k in r.keys():
            print(f"{k}: {r[k]}")
        print("\n--- events ---")
        for e in events(conn, a.id):
            print(f"{e['ts']}  [{e['agent']}] {e['message']}")
    elif a.cmd == "status":
        set_status(conn, a.id, a.status, a.agent, a.draft_path, a.notes)
        print(f"{a.id} -> {a.status}")
    elif a.cmd == "fit":
        set_fit(conn, a.id, a.score,
                sys.stdin.read() if a.text == "-" else a.text, a.agent)
    elif a.cmd == "notes":
        set_notes(conn, a.id, a.notes, a.agent)
    elif a.cmd == "event":
        log_event(conn, a.id, a.agent, a.message)
    elif a.cmd == "question":
        add_question(conn, a.id, a.question,
                     sys.stdin.read() if a.answer == "-" else a.answer, a.agent, a.kind)


def demo():
    conn = connect(":memory:")
    i = add(conn, "Acme", "Analytics Engineer", url="http://x", employment_type="contract")
    assert get(conn, i)["status"] == "found"
    assert len(rows(conn, full_time_only=True)) == 0, "contract row must be filtered out"
    assert len(rows(conn)) == 1

    set_status(conn, i, "applied", agent="tailor", draft_path="/tmp/d.pdf")
    r = get(conn, i)
    assert r["status"] == "applied" and r["draft_path"] == "/tmp/d.pdf"
    assert [e["message"] for e in events(conn, i)] == ["status -> applied", "added (found)"]

    # editing a note must not count as progress
    conn.execute("UPDATE applications SET updated_at = datetime('now','-8 day') WHERE id=?", (i,))
    set_notes(conn, i, "recruiter said friday")
    assert get(conn, i)["notes"] == "recruiter said friday"
    assert [r["id"] for r in stale(conn)] == [i], "note edit must not clear a follow-up"
    conn.execute("UPDATE applications SET updated_at = datetime('now') WHERE id=?", (i,))

    # fresh row is not stale; backdating past the window makes it stale
    assert stale(conn) == []
    conn.execute("UPDATE applications SET updated_at = datetime('now','-6 day') WHERE id=?", (i,))
    assert stale(conn) == [], "6 days is inside the 7-day window"
    conn.execute("UPDATE applications SET updated_at = datetime('now','-8 day') WHERE id=?", (i,))
    assert [r["id"] for r in stale(conn)] == [i]

    # terminal statuses never nag
    set_status(conn, i, "rejected")
    conn.execute("UPDATE applications SET updated_at = datetime('now','-30 day') WHERE id=?", (i,))
    assert stale(conn) == []

    # a fit diagnosis informs the decision; it must not make it
    before = get(conn, i)["status"]
    set_fit(conn, i, 72, "match 72%")
    after = get(conn, i)
    assert after["fit_score"] == 72 and after["status"] == before

    # the same job reached by a different url is still the same job
    assert [r["id"] for r in duplicates(conn, url="http://x")] == [i]
    assert [r["id"] for r in duplicates(conn, company=" acme ", role="ANALYTICS engineer")] == [i]
    assert duplicates(conn, url="http://other", company="Acme", role="Data Analyst") == []
    assert duplicates(conn) == [], "no criteria must not match the whole table"
    assert [r["id"] for r in same_company(conn, "ACME")] == [i]
    assert [r["id"] for r in same_company(conn, "Acme Corp")] == [i]
    assert same_company(conn, "Acmeco") == [] and same_company(conn, " ") == []

    # the dismissal date comes off the status change, not updated_at
    assert dismissed_on(conn) == {}, "nothing discarded yet"
    set_status(conn, i, "discarded")
    conn.execute("UPDATE applications SET updated_at = datetime('now','+5 day') WHERE id=?", (i,))
    assert dismissed_on(conn)[i] < get(conn, i)["updated_at"]

    record_run(conn, "scout", {
        "duration_ms": 4200, "total_cost_usd": 0.12, "is_error": False, "result": "added 3",
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5},
    })
    r = last_runs(conn)[0]
    assert r["agent"] == "scout" and r["tokens"] == 105 and r["ok"] == 1

    # a pending run must show up as running and stay out of the finished totals
    run_id = start_run(conn, "tailor", i)
    assert [r["agent"] for r in running(conn)] == ["tailor"]
    assert "tailor" not in [r["agent"] for r in last_runs(conn)]
    finish_run(conn, run_id, {
        "duration_ms": 9000, "total_cost_usd": 0.30, "is_error": False, "result": "done",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    })
    assert running(conn) == []
    assert "tailor" in [r["agent"] for r in last_runs(conn)]

    add_question(conn, i, "Why this role?", "Because of the migration work.")
    qs = questions_for(conn, i)
    assert len(qs) == 1 and qs[0]["question"] == "Why this role?"
    add_question(conn, i, "Tell me about a failure.", "The refresh outage.", kind="interview")
    assert len(questions_for(conn, i)) == 1
    assert questions_for(conn, i, "interview")[0]["answer"] == "The refresh outage."

    try:
        add(conn, "Bad", status="nonsense")
        assert False, "invalid status accepted"
    except ValueError:
        pass
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        demo()
    else:
        main()
