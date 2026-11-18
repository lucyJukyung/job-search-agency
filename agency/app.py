"""Job search agency dashboard. Run: streamlit run agency/app.py"""

import html
import json
import os
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path

import pypdf
import streamlit as st

import agency

st.set_page_config(page_title="Job Search Agency", layout="wide")
conn = agency.connect()

MASTER_RESUME = Path(os.environ.get("AGENCY_MASTER_RESUME", agency.ROOT / "master-resume.md"))
USER_NAME = os.environ.get("AGENCY_USER_NAME", "Alex Morgan")
USER_TITLE = os.environ.get("AGENCY_USER_TITLE", "Data Analyst")

ACTIVE = ["awaiting_approval", "applied", "screening", "interview", "offer", "rejected",
          "closed"]
# a posting pulled before an outcome is not an application outcome — keep it out of rates
SUBMITTED = [s for s in ACTIVE if s != "closed"]
REPLIED = ["screening", "interview", "offer", "rejected"]
WAITING = ["applied", "screening"]

LABEL = {
    "found": "New", "discarded": "Dismissed", "tailoring": "Tailoring",
    "awaiting_approval": "Ready to review", "applied": "Applied",
    "screening": "Screening", "interview": "Interview", "offer": "Offer",
    "rejected": "Rejected", "closed": "Posting closed",
}
TAG = {  # background, text — the muted tags from the theme's own palette
    "found": ("#DCDCE1", "#5E5E68"), "discarded": ("#DCDCE1", "#8A8A94"),
    "tailoring": ("#EDE3CE", "#6B5322"), "awaiting_approval": ("#EDE3CE", "#6B5322"),
    "applied": ("#DAE2EC", "#2C4560"), "screening": ("#DCE7DC", "#2C4A33"),
    "interview": ("#DCE7DC", "#2C4A33"), "offer": ("#CFE3D0", "#22401F"),
    "rejected": ("#EBD8DA", "#8E3039"), "closed": ("#DCDCE1", "#8A8A94"),
}

st.html("""
<style>
  h1, h2, h3 { letter-spacing: -0.02em; font-weight: 700; color: #8E3039; }
  .eyebrow { font-size: .72rem; letter-spacing: .09em; text-transform: uppercase;
             color: #6E6E78; font-weight: 600; margin-bottom: .1rem; }
  .tag { display: inline-block; padding: .08rem .45rem; border-radius: .25rem;
         font-size: .78rem; font-weight: 500; line-height: 1.5; }
  .silence { display: grid; grid-template-columns: minmax(10rem, 19rem) 1fr 3.5rem;
             gap: .75rem; align-items: center; padding: .28rem 0;
             border-bottom: 1px solid #C4C4CB; }
  .silence .who { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .silence .who small { color: #6E6E78; }
  .silence .track { background: #C7C7CE; border-radius: 2px; height: 6px; }
  .silence .fill { height: 6px; border-radius: 2px; }
  .silence .days { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                   font-size: .8rem; color: #5E5E68; text-align: right; }
  .runbar { font-size: .8rem; line-height: 1.45; padding: .35rem 0;
            border-bottom: 1px solid #C4C4CB; }
  .runbar .n { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
               color: #6E6E78; }
  .runbar.live .n { color: #8E3039; }
  button[data-testid="stBaseButton-segmented_control"] p { font-size: .8rem; }
  .pulse { display: inline-block; width: .5rem; height: .5rem; border-radius: 50%;
           background: #B5762E; animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }

  /* Headings inside agent-written markdown (fit diagnoses, drafts) are content, not
     page titles — st.title renders through a different DOM wrapper (stHeading), so
     this selector only catches st.markdown() output. */
  [data-testid="stMarkdown"] h1 { font-size: 1.3rem; color: #33333A; }
  [data-testid="stMarkdown"] h2 { font-size: 1.1rem; color: #33333A; }
  [data-testid="stMarkdown"] h3 { font-size: 1rem; color: #33333A; }

  /* Sidebar: a wordmark, then the nav as a list of rows — the radio's own circles
     are hidden so each option reads as a menu item. */
  .mark { display: flex; align-items: center; gap: .5rem; font-weight: 700;
          font-size: 1.05rem; letter-spacing: -.01em; padding: .1rem 0 .7rem; }
  .mark .glyph { font-size: 1.25rem; line-height: 1; }
  section[data-testid="stSidebar"] [role="radiogroup"] { gap: .15rem; }
  section[data-testid="stSidebar"] [role="radiogroup"] label {
      width: 100%; padding: .4rem .6rem; border-radius: .375rem;
      transition: background .12s ease; }
  section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
      background: #C3C3CA; }
  section[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {
      display: none; }
  section[data-testid="stSidebar"] [role="radiogroup"] label span[role="img"] {
      margin-right: .6rem; vertical-align: -.1em; }
  section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
      background: #E0CFD2; }
  section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {
      color: #8E3039; font-weight: 600; }
  .whoami { display: flex; align-items: center; gap: .55rem; padding: .3rem 0; }
  .whoami .av { width: 1.9rem; height: 1.9rem; border-radius: 50%;
                background: linear-gradient(140deg, #A8434D, #7A2831); color: #fff;
                display: grid; place-items: center; font-size: .78rem;
                font-weight: 700; }
  .whoami .nm { font-size: .85rem; font-weight: 600; line-height: 1.2; }
  .whoami .rl { font-size: .75rem; color: #6E6E78; line-height: 1.2; }
</style>
""")


def head(eyebrow, title):
    st.html(f'<div class="eyebrow">{html.escape(eyebrow)}</div>')
    st.title(title)


def tag(status):
    bg, fg = TAG[status]
    return (f'<span class="tag" style="background:{bg};color:{fg}">'
            f'{html.escape(LABEL[status])}</span>')


def days_since(ts):
    return (date.today() - datetime.fromisoformat(ts).date()).days


def note(text):
    """A short explanatory blurb, set apart in its own bordered box."""
    with st.container(border=True):
        st.caption(text)


def dup_warning(dups):
    """Flag a posting that is already on file, before it gets added — and assessed —
    a second time. A dismissal is worth more detail than a live application: the
    point is to remember the call that was already made, and what it was based on."""
    if not dups:
        return
    dismissed = agency.dismissed_on(conn)
    for d in dups:
        who = f"{d['company']} · {d['role'] or 'role not set'} (#{d['id']})"
        if d["status"] == "discarded":
            when = (dismissed.get(d["id"]) or d["updated_at"])[:10]
            score = (f"{d['fit_score']}% match" if d["fit_score"] is not None
                     else "never assessed")
            st.warning(f"You dismissed this on {when} — {score}. {who}")
        else:
            st.warning(f"Already in the pipeline as {LABEL[d['status']]} "
                       f"since {d['updated_at'][:10]}. {who}")


def company_history(company, exclude=()):
    """Other roles already on file at this employer, so a second application there
    is a deliberate choice rather than a surprise."""
    past = [r for r in agency.same_company(conn, company) if r["id"] not in exclude]
    if past:
        roles = "; ".join(f"{r['role'] or 'role not set'} ({LABEL[r['status']]}, "
                          f"{r['updated_at'][:10]})" for r in past)
        st.info(f"Already on file at {company}: {roles}")


CLI = "Bash(python3 agency/agency.py:*)"
SCOUT_TOOLS = ["Read", CLI, "Bash(python3 agency/seek.py:*)"]
FIT_TOOLS = ["Read", "Skill", CLI]
TAILOR_TOOLS = ["Read", "Write", "Edit", "Glob", "Skill", CLI]
SCREEN_TOOLS = ["Read", "Skill", CLI]


def _execute_agent(prompt, agent, tools, app_id, run_id):
    """Runs in a background thread so the page never blocks on the subprocess —
    that is what lets the sidebar's fragment keep polling and show live progress.
    `-p` runs non-interactively, so anything not in `tools` is silently denied
    rather than prompting — the list has to cover everything the agent needs.
    """
    out = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", *tools],
        cwd=str(agency.ROOT), capture_output=True, text=True, timeout=1800,
    )
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError:
        payload = {"is_error": True,
                   "result": (out.stderr or out.stdout or "no output")[-2000:]}
    # sqlite connections can't cross threads — open this thread's own
    agency.finish_run(agency.connect(), run_id, payload)


def run_agent(prompt, agent, label, tools, app_id=None):
    # own connection: on_click callbacks run in a different thread from the one that made `conn`
    run_conn = agency.connect()
    run_id = agency.start_run(run_conn, agent, app_id)
    run_conn.close()
    st.session_state.setdefault("_my_runs", set()).add(run_id)
    threading.Thread(
        target=_execute_agent, args=(prompt, agent, tools, app_id, run_id), daemon=True
    ).start()
    st.toast(f"{label} started — watch the sidebar for live progress.", icon="🟡")


def apply_refine(app_id):
    # on_click callback: the only point where Streamlit lets a widget's value be reset
    key = f"tailor_refine_{app_id}"
    prompt = (f"For application id {app_id}, a draft already exists in drafts/. "
              f"Revise it with these specific changes:\n{st.session_state[key]}")
    run_agent(prompt, "tailor", "Tailoring", TAILOR_TOOLS, app_id)
    st.session_state[key] = ""


def proceed_anyway(app):
    """A fit run that stopped on a pre-filter leaves a BLOCKER: note and no diagnosis."""
    if app["fit_text"] or not (app["notes"] or "").startswith("BLOCKER"):
        return
    st.warning(app["notes"])
    if st.button("Proceed anyway", key=f"fit_anyway_{app['id']}"):
        run_agent(f"Use the fit agent for application id {app['id']}. The candidate has read the "
                  "pre-filter blocker in the notes and wants to proceed anyway.",
                  "fit", "Fit", FIT_TOOLS, app["id"])


QUESTION_PANELS = {
    "screening": ("Screening questions",
                  "Paste a question from the application form or a follow-up email.",
                  "e.g. Tell us more about why you're interested in this role.",
                  "answer this screening question"),
    "interview": ("Interview prep",
                  "Paste a question you expect, or were asked, in an interview for this role.",
                  "e.g. Tell me about a time a pipeline failed and how you fixed it.",
                  "prepare an interview answer (kind: interview) to this question"),
}


def questions_panel(app, kind):
    title, hint, placeholder, task = QUESTION_PANELS[kind]
    st.divider()
    st.html(f'<div class="eyebrow">{title}</div>')
    note(hint)
    with st.form(f"{kind}_q_{app['id']}", clear_on_submit=True):
        question = st.text_area("Question", height=80, label_visibility="collapsed",
                                placeholder=placeholder)
        if st.form_submit_button("Get answer", type="primary", disabled=not app["jd_text"]):
            if question.strip():
                run_agent(f"Use the screen agent for application id {app['id']} to "
                          f"{task}:\n\n{question.strip()}",
                          "screen", title, SCREEN_TOOLS, app["id"])
    if not app["jd_text"]:
        st.caption("No job description on file for this one, so there's nothing to "
                   "ground an answer in.")
    for q in agency.questions_for(conn, app["id"], kind):
        with st.expander(q["question"]):
            st.caption(q["ts"][:16])
            st.markdown(q["answer"])


def pick(rows, label="Select", key=None):
    """A single-app selector. When `key` names a session_state slot that already
    holds a valid id (e.g. set by clicking a table row), Streamlit uses that as the
    widget's current value and `index` below is just the first-ever-render fallback.
    """
    by_id = {r["id"]: r for r in rows}
    ids = list(by_id)
    if key and st.session_state.get(key) not in by_id:
        st.session_state.pop(key, None)  # stale pick from before a filter changed
    i = st.selectbox(
        label, ids, index=0, key=key,
        format_func=lambda i: f"{by_id[i]['company'] or 'Unnamed'} — "
                              f"{by_id[i]['role'] or 'role not set'}",
    )
    return agency.get(conn, i)


def extract_jd_file(uploaded):
    """Text out of an uploaded JD file — a PDF position description or a plain-text
    paste saved to a file. `uploaded` is Streamlit's UploadedFile (file-like)."""
    if uploaded.name.lower().endswith(".pdf"):
        reader = pypdf.PdfReader(uploaded)
        return "\n\n".join(p.extract_text() or "" for p in reader.pages).strip()
    return uploaded.read().decode("utf-8", "ignore").strip()


def drafts_for(app):
    """draft_path is stored relative to agency.ROOT (that's what the tailor agent's
    cwd is) — resolve it against ROOT explicitly rather than the dashboard process's
    own cwd, which depends on how `streamlit run` happened to be launched."""
    if not app["draft_path"]:
        return []
    d = Path(app["draft_path"])
    if not d.is_absolute():
        d = agency.ROOT / d
    d = d.parent if d.suffix else d
    return sorted(d.glob("*.md")) if d.exists() else []


AGENT_NAMES = (("scout", "Scout"), ("fit", "Fit"), ("tailor", "Tailoring"),
               ("screen", "Screening"))


@st.fragment(run_every=2)
def agent_status():
    """Live agent panel: polls the DB every 2s, independent of the rest of the page,
    so a run started elsewhere shows progress without the user touching anything.
    When a run it was watching finishes, it escalates to a full rerun (scope="app",
    st.rerun()'s default) so the page's main content picks up the new fit/draft/status.

    Opens its own DB connection rather than closing over the page-level `conn` —
    a timed fragment's reruns can land on a different thread than the one that
    created `conn`, and sqlite3 connections are thread-affine by default.
    """
    local = agency.connect()
    st.html('<div class="eyebrow">Agents</div>')

    runs = agency.running(local)
    live = {r["agent"]: r for r in runs}

    # tracked by run id, not by seeing the run live: an auth failure finishes in
    # ~0.2s, well inside one 2s poll, so it was never seen running and failed silently
    mine = st.session_state.setdefault("_my_runs", set())
    done = mine - {r["id"] for r in runs}
    if done:
        failed = local.execute(
            f"SELECT agent, summary FROM runs WHERE ok = 0 AND id IN "
            f"({','.join('?' * len(done))})", list(done)).fetchall()
        for r in failed:
            msg = (r["summary"] or "no output")[:300]
            if "authenticate" in msg:
                msg = msg.rstrip(". ") + ". Log in again: run `claude` in Terminal, then `/login`."
            st.session_state.setdefault("_toasts", []).append(
                f"{r['agent'].capitalize()} failed: {msg}")
        mine -= done
        if failed:
            st.rerun()
    was_live = st.session_state.setdefault("_live_agents", set())
    # app id -> status when its run was first seen, to tell an agent's dismissal
    # apart from a posting that was already dismissed
    watched = st.session_state.setdefault("_live_apps", {})
    if was_live - live.keys():
        for app_id in watched.keys() - {r["app_id"] for r in runs}:
            app = agency.get(local, app_id)
            if app and app["status"] == "discarded" and watched[app_id] != "discarded":
                # otherwise the posting just vanishes from the list, which reads as a bug
                reason = app["notes"] or "no reason recorded"
                # a toast raised in a fragment is dropped by the rerun below, so the
                # next full run of the page shows it instead
                st.session_state.setdefault("_toasts", []).append(
                    f"Dismissed {app['company']} — {app['role'] or 'role not set'}: "
                    f"{reason[:300]}")
            del watched[app_id]
        was_live.clear()
        st.rerun()
    was_live.update(live.keys())
    for r in runs:
        if r["app_id"] and r["app_id"] not in watched:
            app = agency.get(local, r["app_id"])
            watched[r["app_id"]] = app["status"] if app else None

    finished = {r["agent"]: r for r in agency.last_runs(local)}
    for name, shown in AGENT_NAMES:
        if name in live:
            elapsed = live[name]["elapsed_s"]
            st.html(f'<div class="runbar live"><span class="pulse"></span> {shown}'
                    f'<br><span class="n">running · {elapsed:.0f}s</span></div>')
            continue
        r = finished.get(name)
        if not r:
            st.html(f'<div class="runbar">{shown}<br>'
                    f'<span class="n">not run yet</span></div>')
            continue
        dot = "🟢" if r["ok"] else "🔴"
        secs = (r["duration_ms"] or 0) / 1000
        st.html(
            f'<div class="runbar"><span class="dot">{dot}</span> {shown}'
            f'<br><span class="n">{r["ran_at"][5:16]} · {secs:.0f}s</span>'
            f'<br><span class="n">{r["runs"]} runs · {r["tokens"]:,} tokens · '
            f'${r["cost"]:.2f}</span></div>'
        )
    total = local.execute("SELECT COALESCE(SUM(cost_usd),0) FROM runs").fetchone()[0]
    st.caption(f"Agents have cost ${total:.2f} so far")


# --- sidebar ------------------------------------------------------------

NAV = {
    "Find jobs": ":material/travel_explore:",
    "Tailor resume": ":material/edit_document:",
    "Applications": ":material/inbox:",
    "Results": ":material/monitoring:",
    "Master resume": ":material/description:",
}

for msg in st.session_state.pop("_toasts", []):
    st.toast(msg, icon="🚫", duration="infinite")

with st.sidebar:
    st.html('<div class="mark"><span class="glyph">👑</span>Job Search Agency</div>')
    page = st.radio(
        "Menu", list(NAV), format_func=lambda p: f"{NAV[p]} {p}",
        label_visibility="collapsed",
    )

    st.divider()
    agent_status()

    st.divider()
    initials = "".join(w[0] for w in USER_NAME.split()[:2]).upper()
    st.html(f'<div class="whoami"><div class="av">{html.escape(initials)}</div>'
            f'<div><div class="nm">{html.escape(USER_NAME)}</div>'
            f'<div class="rl">{html.escape(USER_TITLE)}</div></div></div>')

# --- Find jobs ----------------------------------------------------------

if page == "Find jobs":
    head("Scout", "Find jobs")

    c1, c2 = st.columns([3, 2])
    keywords = c1.text_input("Keywords", "analytics engineer, BI developer")
    location = c2.text_input("Location", "Melbourne")

    if st.button("Find jobs", type="primary"):
        run_agent(
            f"Use the scout agent to find {keywords} roles in {location}.",
            "scout", "Scout", SCOUT_TOOLS,
        )

    note(
        "Scout reads SEEK directly (CBD + 10 km, last 3 days) and drops part-time, casual, and "
        "contracts under 12 months. SEEK descriptions are pulled automatically; LinkedIn "
        "postings still need pasting in."
    )

    with st.expander("Add a posting by link"):
        # A saved posting should leave an empty box ready for the next link. A widget's
        # value can't be reassigned once it exists, so a successful add bumps this
        # counter instead and the next run builds a fresh widget with nothing in it.
        nonce = st.session_state.setdefault("url_nonce", 0)
        url = st.text_input("Posting URL", placeholder="https://…",
                            label_visibility="collapsed", key=f"posting_url_{nonce}")

        if st.button("Look up", disabled=not url):
            try:
                company, role, jd = agency.parse_posting(url)
                st.session_state["new_posting"] = {
                    "url": url, "company": company, "role": role, "jd": jd,
                }
            except Exception as e:
                st.session_state["new_posting"] = {
                    "url": url, "company": "", "role": "", "jd": None, "error": str(e),
                }

        draft = st.session_state.get("new_posting")
        if not draft or draft["url"] != url:
            # the url alone is enough to catch a re-paste, and catches it before
            # the lookup is even run
            dup_warning(agency.duplicates(conn, url=url) if url else [])
            note("Look up a link — the company, role, and description are read off "
                 "the page where the site allows it. Nothing is saved until you "
                 "confirm below.")
        else:
            # now that the company and role are known, the same job posted on a
            # different board is catchable too
            dups = agency.duplicates(conn, url=url, company=draft["company"],
                                     role=draft["role"])
            dup_warning(dups)
            company_history(draft["company"], exclude={d["id"] for d in dups})
            if draft.get("error"):
                st.warning(f"Couldn't read that page: {draft['error']}")
            elif draft["jd"]:
                st.caption(f"Description found ({len(draft['jd']):,} characters). "
                          "Check the company and role — page titles are inconsistent.")
            else:
                st.caption("No description found automatically. Paste it in below.")

            with st.form("add_posting"):
                c1, c2 = st.columns(2)
                new_company = c1.text_input("Company", draft["company"])
                new_role = c2.text_input("Role", draft["role"])
                new_jd = st.text_area("Description", draft["jd"] or "", height=180,
                                      placeholder="Paste the posting text here")
                if st.form_submit_button("Add to pipeline", type="primary"):
                    if not new_company or not new_jd:
                        st.warning("Company and a description are both required — "
                                   "a posting with neither can't be assessed for fit, "
                                   "so nothing was saved.")
                    else:
                        agency.add(conn, new_company, new_role, url, new_jd)
                        del st.session_state["new_posting"]
                        st.session_state["url_nonce"] = nonce + 1
                        # a toast survives the rerun that clears the box; st.success
                        # would be wiped before it was ever read
                        st.toast(f"Added {new_company} — {new_role or 'role not set'}.",
                                 icon="✅")
                        st.rerun()

    st.divider()

    found = [r for r in agency.rows(conn) if r["status"] == "found"]
    if not found:
        st.info("Nothing to review. Run Scout, or add a posting by link.")
        st.stop()

    st.html(f'<div class="eyebrow">{len(found)} to review</div>')
    app = pick(found, "Posting")
    if app["url"]:
        st.markdown(f"[Open the posting]({app['url']})")

    if not app["jd_text"] and app["url"]:
        if st.button("Fetch description"):
            try:
                agency.set_jd(conn, app["id"], agency.fetch_jd(app["url"]),
                              agent="dashboard")
                st.rerun()
            except Exception as e:
                st.warning(str(e))

    uploaded = st.file_uploader(
        "Upload a job description file (some postings offer a PD as a PDF download)",
        type=["pdf", "txt", "md"], key=f"jd_upload_{app['id']}",
    )
    if uploaded is not None:
        marker = f"{uploaded.name}:{uploaded.size}"
        if st.session_state.get(f"_jd_upload_done_{app['id']}") != marker:
            try:
                text = extract_jd_file(uploaded)
            except Exception as e:
                st.warning(f"Couldn't read that file: {e}")
            else:
                if text:
                    agency.set_jd(conn, app["id"], text, agent="dashboard")
                    st.session_state[f"_jd_upload_done_{app['id']}"] = marker
                    st.success(f"Read {len(text):,} characters from {uploaded.name}.")
                    st.rerun()
                else:
                    st.warning("Couldn't find any text in that file.")

    with st.form(f"edit_{app['id']}"):
        c1, c2 = st.columns(2)
        company = c1.text_input("Company", app["company"])
        role = c2.text_input("Role", app["role"])
        with st.expander("Job description", expanded=not app["jd_text"]):
            jd = st.text_area(
                "Job description", app["jd_text"] or "", height=200,
                label_visibility="collapsed", placeholder="Paste the posting text here",
            )
        if st.form_submit_button("Save changes"):
            if (company, role) != (app["company"], app["role"]):
                agency.rename(conn, app["id"], company, role)
            if jd != (app["jd_text"] or ""):
                agency.set_jd(conn, app["id"], jd)
            st.rerun()

    st.divider()
    st.html('<div class="eyebrow">Fit</div>')

    if app["fit_text"]:
        st.metric("Match", f"{app['fit_score']}%")
        st.markdown(app["fit_text"])
    elif app["jd_text"]:
        note("Check whether this role is worth applying to. Nothing is written to "
             "your resume at this step.")
        proceed_anyway(app)
    else:
        note("Add the job description to assess fit.")

    st.divider()
    b1, b2, b3 = st.columns(3)
    if b1.button("Assess fit", disabled=not app["jd_text"]):
        run_agent(f"Use the fit agent for application id {app['id']}.",
                  "fit", "Fit", FIT_TOOLS, app["id"])
    if b2.button("Send to tailoring", type="primary", disabled=not app["jd_text"]):
        agency.set_status(conn, app["id"], "tailoring")
        st.rerun()
    if b3.button("Dismiss"):
        agency.set_status(conn, app["id"], "discarded")
        st.rerun()

# --- Tailor resume ------------------------------------------------------

elif page == "Tailor resume":
    head("Tailoring", "Tailor resume")

    queue = [r for r in agency.rows(conn)
             if r["status"] in ("tailoring", "awaiting_approval")]
    if not queue:
        st.info("Nothing queued. Send a posting over from Find jobs.")
        st.stop()

    app = pick(queue, "Posting")
    st.html(f'<div style="margin:-.4rem 0 .6rem">{tag(app["status"])}</div>')
    company_history(app["company"], exclude={app["id"]})
    if app["url"]:
        st.caption(app["url"])

    left, right = st.columns([2, 1])

    with left:
        files = drafts_for(app)
        if files:
            if st.button("Open drafts folder", icon=":material/folder_open:"):
                subprocess.run(["open", str(files[0].parent)])
            for tab, f in zip(st.tabs([f.stem for f in files]), files):
                tab.markdown(f.read_text())
        elif app["fit_text"]:
            st.metric("Match", f"{app['fit_score']}%")
            st.markdown(app["fit_text"])
        elif app["jd_text"]:
            with st.expander("Job description", expanded=True):
                st.text(app["jd_text"])

    with right:
        if app["fit_score"] is not None:
            st.metric("Match", f"{app['fit_score']}%")
        else:
            st.html('<div class="eyebrow">Fit</div>')
            if app["jd_text"]:
                if st.button("Assess fit", disabled=not app["jd_text"]):
                    run_agent(f"Use the fit agent for application id {app['id']}.",
                              "fit", "Fit", FIT_TOOLS, app["id"])
                proceed_anyway(app)
            else:
                st.caption("No fit check yet.")

        st.divider()
        st.html('<div class="eyebrow">Instructions</div>')
        instructions = st.text_area(
            "Instructions",
            height=100,
            placeholder="What to emphasise, what to drop, anything else — passed to the agent as written.",
            label_visibility="collapsed",
            key=f"tailor_instr_{app['id']}",
        )
        include_cover = st.checkbox("Include cover letter", key=f"tailor_cover_{app['id']}")

        tailor_prompt = f"Use the tailor agent for application id {app['id']}."
        if instructions:
            tailor_prompt += f"\n\nThe candidate's instructions before tailoring:\n{instructions}"
        if include_cover:
            tailor_prompt += "\n\nPlease include a cover letter."

        if st.button("Tailor resume", type="primary", disabled=not app["jd_text"]):
            agency.set_status(conn, app["id"], "tailoring", agent="dashboard")
            run_agent(tailor_prompt, "tailor", "Tailoring", TAILOR_TOOLS, app["id"])
        if not app["jd_text"]:
            st.caption("Add the job description first, over on Find jobs.")

        if files:
            st.divider()
            st.html('<div class="eyebrow">Refine draft</div>')
            refine_notes = st.text_area(
                "Refine",
                height=100,
                placeholder="What to change — the agent edits the existing draft in place.",
                label_visibility="collapsed",
                key=f"tailor_refine_{app['id']}",
            )
            st.button("Apply changes", disabled=not refine_notes,
                      on_click=apply_refine, args=(app["id"],))

        st.divider()
        st.caption("Read the draft, apply on the company's own site, then mark it here.")
        if st.button("Mark as applied", disabled=not files):
            agency.set_status(conn, app["id"], "applied")
            st.rerun()
        if st.button("Dismiss"):
            agency.set_status(conn, app["id"], "discarded")
            st.rerun()

        st.divider()
        st.html('<div class="eyebrow">History</div>')
        for e in agency.events(conn, app["id"]):
            st.html(f'<div class="runbar"><span class="n">{e["ts"][5:16]}</span> '
                    f'[{html.escape(e["agent"])}] {html.escape(e["message"])}</div>')

    questions_panel(app, "screening")

# --- Applications -------------------------------------------------------

elif page == "Applications":
    head("Tracker", "Applications")

    full_time_only = st.checkbox("Full-time only", value=False)

    rows = [r for r in agency.rows(conn, full_time_only) if r["status"] in ACTIVE]
    if not rows:
        st.info("No applications yet.")
        st.stop()

    # The signature of this pipeline is not the table — it is the silence. This one
    # sort control drives both: the bars below it, and the table further down.
    eyebrow_col, sort_col = st.columns([5, 2])
    eyebrow_col.html('<div class="eyebrow">Waiting on a reply</div>')
    order = sort_col.segmented_control(
        "Sort by applied date", ["Newest", "Oldest"], default="Newest", required=True,
        format_func=lambda o: f"{o} :material/{'arrow_downward' if o == 'Newest' else 'arrow_upward'}:",
        label_visibility="collapsed", key="apps_sort_order",
    )
    rows.sort(key=lambda r: r["created_at"], reverse=(order != "Oldest"))

    waiting = sorted((r for r in rows if r["status"] in WAITING),
                     key=lambda r: days_since(r["created_at"]), reverse=(order == "Oldest"))
    if waiting:
        # bar width is always relative to the longest wait, regardless of display order
        longest = max(days_since(r["created_at"]) for r in waiting) or 1
        bars = []
        for r in waiting:
            d = days_since(r["created_at"])
            # calm while it is reasonable to still be waiting, warm once it is not
            colour = ("#8E3039" if d >= 30 else "#B5762E" if d >= agency.STALE_DAYS
                      else "#5E7A8E")
            bars.append(
                f'<div class="silence"><div class="who">{html.escape(r["company"])} '
                f'<small>{html.escape(r["role"] or "")}</small></div>'
                f'<div class="track"><div class="fill" style="width:{d / longest * 100:.0f}%;'
                f'background:{colour}"></div></div>'
                f'<div class="days">{d}d</div></div>'
            )
        st.html("".join(bars))
        note(f"Amber past {agency.STALE_DAYS} days, red past 30. \"Oldest\" puts the "
             "longest wait at the top; \"Newest\" flips it.")
        st.divider()

    event = st.dataframe(
        [{"Company": r["company"], "Role": r["role"], "Status": LABEL[r["status"]],
          "Applied": r["created_at"][:10], "Last change": r["updated_at"][:10]}
         for r in rows],
        hide_index=True, width="stretch",
        on_select="rerun", selection_mode="single-row", key="apps_table",
    )
    note("Check the box on the left of a row to open it below.")

    if event and event.selection and event.selection.rows:
        clicked_id = rows[event.selection.rows[0]]["id"]
        if st.session_state.get("_apps_table_last") != clicked_id:
            st.session_state["_apps_table_last"] = clicked_id
            st.session_state["open_app_id"] = clicked_id
            st.rerun()

    st.divider()
    app = pick(rows, "Open", key="open_app_id")
    if app["url"]:
        st.markdown(f"[Open the posting]({app['url']})")
    left, right = st.columns([2, 1])

    with left:
        new_status = st.selectbox(
            "Status", agency.STATUSES, index=agency.STATUSES.index(app["status"]),
            format_func=lambda s: LABEL[s],
        )
        if new_status != app["status"]:
            agency.set_status(conn, app["id"], new_status)
            st.rerun()

        notes = st.text_area("Notes", app["notes"] or "", height=120)
        if st.button("Save notes"):
            agency.set_notes(conn, app["id"], notes)
            st.rerun()

        for f in drafts_for(app):
            with st.expander(f.stem):
                st.markdown(f.read_text())
        if app["draft_path"] and Path(app["draft_path"]).suffix == ".pdf":
            st.caption(f"Resume sent: {app['draft_path']}")

    with right:
        st.html('<div class="eyebrow">History</div>')
        for e in agency.events(conn, app["id"]):
            st.html(f'<div class="runbar"><span class="n">{e["ts"][5:16]}</span> '
                    f'[{html.escape(e["agent"])}] {html.escape(e["message"])}</div>')
        with st.expander("Delete"):
            if st.button("Delete this application"):
                conn.execute("DELETE FROM events WHERE app_id = ?", (app["id"],))
                conn.execute("DELETE FROM applications WHERE id = ?", (app["id"],))
                conn.commit()
                st.rerun()

    questions_panel(app, "interview")

# --- Results ------------------------------------------------------------

elif page == "Results":
    head("Results", "Results")

    rows = agency.rows(conn)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in agency.STATUSES}
    applied = sum(counts[s] for s in SUBMITTED)
    replied = sum(counts[s] for s in REPLIED)

    c1, c2, c3 = st.columns(3)
    c1.metric("Applied", applied)
    c2.metric("Heard back", replied,
              f"{replied / applied:.0%}" if applied else None, delta_color="off")
    c3.metric("Interview or better", counts["interview"] + counts["offer"])
    note("Heard back counts any decision, yes or no. The rest are still silent.")

    st.divider()
    st.html('<div class="eyebrow">By status</div>')
    st.bar_chart(
        {LABEL[s]: counts[s] for s in agency.STATUSES if counts[s]},
        horizontal=True, height=280,
    )

    st.divider()
    st.html('<div class="eyebrow">Follow up</div>')
    overdue = agency.stale(conn)
    if not overdue:
        st.success("Nothing to chase.")
    else:
        st.caption(f"{len(overdue)} applications with no movement in "
                   f"{agency.STALE_DAYS} days.")
        st.dataframe(
            [{"Company": r["company"], "Role": r["role"], "Status": LABEL[r["status"]],
              "Applied": r["created_at"][:10], "Days waiting": days_since(r["created_at"])}
             for r in overdue],
            hide_index=True, width="stretch",
        )

    declined = [r for r in rows if r["status"] == "discarded"]
    if declined:
        dismissed = agency.dismissed_on(conn)
        declined.sort(key=lambda r: (r["fit_score"] is None, r["fit_score"] or 0))
        st.divider()
        st.html(f'<div class="eyebrow">Declined · {len(declined)}</div>')
        note("Passed on before applying, weakest match first. Check a row to read the "
             "fit diagnosis the call was made on — so the same posting doesn't come "
             "back round and get assessed all over again.")
        picked = st.dataframe(
            [{"Company": r["company"], "Role": r["role"],
              "Match": (f"{r['fit_score']}%" if r["fit_score"] is not None
                        else "Not assessed"),
              "Dismissed": (dismissed.get(r["id"]) or r["updated_at"])[:10]}
             for r in declined],
            hide_index=True, width="stretch",
            on_select="rerun", selection_mode="single-row", key="declined_table",
        )

        if picked and picked.selection and picked.selection.rows:
            d = declined[picked.selection.rows[0]]
            st.html(f'<div class="eyebrow">{html.escape(d["company"])} · '
                    f'{html.escape(d["role"] or "role not set")}</div>')
            if d["url"]:
                st.markdown(f"[Open the posting]({d['url']})")
            if d["fit_text"]:
                st.metric("Match", f"{d['fit_score']}%")
                st.markdown(d["fit_text"])
            else:
                note("Dismissed without a fit check, so there is no diagnosis on "
                     "file — only the fact that you passed on it.")

# --- Master resume ------------------------------------------------------

elif page == "Master resume":
    head("Source", "Master resume")

    if not MASTER_RESUME.exists():
        st.warning(f"No master resume at {MASTER_RESUME}. Copy master-resume.example.md to start.")
        st.stop()

    note(
        "This is the file every tailored resume is built from. The fit and tailoring "
        "agents read it, so editing it here changes "
        "what they produce."
    )
    text = MASTER_RESUME.read_text()

    edited = st.text_area("Master resume", text, height=560,
                          label_visibility="collapsed")
    c1, c2 = st.columns([1, 4])
    if c1.button("Save", type="primary", disabled=edited == text):
        MASTER_RESUME.write_text(edited)
        st.success("Saved.")
        st.rerun()
    c2.caption(str(MASTER_RESUME))

    with st.expander("Preview"):
        st.markdown(text)
