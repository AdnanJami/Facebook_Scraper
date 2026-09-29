"""FB Job Scraper GUI.  Run:  streamlit run app.py"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from fbscraper.config import load_config
from fbscraper.feed import group_id_from_url

ROOT = Path(__file__).resolve().parent
CFG = load_config(str(ROOT / "config.yaml"), {})
OUT = ROOT / CFG.output_dir
DB_PATH = OUT / "scraper.db"
COOKIES = ROOT / CFG.cookies_file
JOB_FILE = OUT / "gui_job.json"          # the background run started from the GUI
LOG_FILE = OUT / "gui_run.log"
SETTINGS_FILE = OUT / "gui_settings.json"

st.set_page_config(page_title="FB Job Scraper", page_icon="💼", layout="wide")


# ================================================================ helpers

def query(sql: str, params=()) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        return pd.read_sql_query(sql, con, params=params)
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return pd.DataFrame()  # table not created yet (nothing processed so far)
    finally:
        con.close()


def txt(x) -> str:
    """Database value as text; NULL/NaN become "" (NaN is truthy, so `if value:` would lie)."""
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def local_file(p) -> Path | None:
    if not p:
        return None
    path = ROOT / str(p).replace("\\", "/")
    return path if path.exists() else None


def search_mask(df: pd.DataFrame, text: str, cols: list[str]) -> pd.Series:
    """Every word must appear somewhere in the given columns (case-insensitive)."""
    hay = df[cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for word in text.lower().split():
        mask &= hay.str.contains(word, regex=False)
    return mask


def date_filter(df: pd.DataFrame, col: str, key: str) -> pd.Series:
    dates = pd.to_datetime(df[col], errors="coerce")
    if dates.notna().sum() == 0:
        return pd.Series(True, index=df.index)
    lo, hi = dates.min().date(), dates.max().date()
    picked = st.date_input("Posted between", (lo, hi), min_value=lo, max_value=hi, key=key)
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        return dates.isna() | ((dates.dt.date >= picked[0]) & (dates.dt.date <= picked[1]))
    return pd.Series(True, index=df.index)


def show_comments(post_id: str) -> None:
    rows = query("SELECT * FROM comments WHERE post_id = ? ORDER BY posted_at", (post_id,))
    if rows.empty:
        st.caption("No comments.")
        return
    replies = rows[rows.parent_id.notna()]

    def reply(r):
        body = txt(r.text).replace("\n", "\n> ")
        st.markdown(f"> ↳ **{txt(r.author) or 'Unknown'}** · {txt(r.posted_at)}  \n> {body}")

    for _, c in rows[rows.parent_id.isna()].iterrows():
        st.markdown(f"**{txt(c.author) or 'Unknown'}** · {txt(c.posted_at)}")
        st.text(txt(c.text))
        for _, r in replies[replies.parent_id == c.comment_id].iterrows():
            reply(r)
    for _, r in replies[~replies.parent_id.isin(rows.comment_id)].iterrows():  # parent not scraped
        reply(r)


def show_post(p: pd.Series, open_comments: bool = False) -> None:
    """Screenshot next to the post's text and comments."""
    left, right = st.columns([1, 1])
    with left:
        shot = local_file(txt(p.get("screenshot")))
        if shot:
            st.image(str(shot), width="stretch")
        else:
            st.caption("No screenshot.")
    with right:
        if txt(p.get("url")):
            st.link_button("Open on Facebook ↗", p["url"])
        st.caption(f"{txt(p.get('group_id'))} · {txt(p.get('author')) or 'Unknown'} · {txt(p.get('posted_at'))}")
        st.text(txt(p.get("text")) or "(no text)")
        count = p.get("comment_count")
        with st.expander(f"Comments ({int(count) if pd.notna(count) else 0} on Facebook)", expanded=open_comments):
            show_comments(p["post_id"])


# ---------------------------------------------------------------- background run

def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_state() -> tuple[str, dict]:
    """('idle' | 'running' | 'finished' | 'failed' | 'stopped', job info)"""
    job = load_json(JOB_FILE, {})
    if not job:
        return "idle", job
    log = LOG_FILE.read_text(encoding="utf-8", errors="replace") if LOG_FILE.exists() else ""
    done = re.search(r"__DONE__ (\d+)", log)
    if done:
        return ("finished" if done.group(1) == "0" else "failed"), job
    return ("running" if pid_alive(job["pid"]) else "stopped"), job


def start_run(steps: list[list[str]], title: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, "-u", "-m", "fbscraper.runner", json.dumps(steps)],
                            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
                            creationflags=flags, start_new_session=os.name != "nt")
    JOB_FILE.write_text(json.dumps({"pid": proc.pid, "title": title, "started": datetime.now().isoformat(timespec="seconds")}),
                        encoding="utf-8")


def stop_run(pid: int) -> None:
    if os.name == "nt":  # kill the whole tree: runner, fbscraper and its browser
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.killpg(os.getpgid(pid), signal.SIGTERM)


# ---------------------------------------------------------------- cookies

def cookie_status() -> dict:
    if not COOKIES.exists():
        return {"ok": False, "msg": "No cookies.json yet."}
    try:
        cookies = json.loads(COOKIES.read_text(encoding="utf-8"))
    except ValueError:
        return {"ok": False, "msg": "cookies.json is not valid JSON."}
    by_name = {c.get("name"): c for c in cookies if isinstance(c, dict)}
    saved = datetime.fromtimestamp(COOKIES.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    if "c_user" not in by_name or "xs" not in by_name:
        return {"ok": False, "msg": f"cookies.json (saved {saved}) has no login cookie (c_user / xs)."}
    exp = by_name["xs"].get("expiry") or by_name["xs"].get("expirationDate") or by_name["xs"].get("expires")
    exp_txt = datetime.fromtimestamp(exp).strftime("%Y-%m-%d") if exp and exp > 0 else "end of session"
    return {"ok": True, "msg": f"Account ID {by_name['c_user'].get('value')} · saved {saved} · expires {exp_txt}"}


def validate_cookies(raw: str) -> tuple[list | None, str]:
    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, f"Not valid JSON: {e}"
    if isinstance(data, dict):  # some exporters wrap the list: {"cookies": [...]}
        data = data.get("cookies", data)
    if not isinstance(data, list) or not all(isinstance(c, dict) and "name" in c and "value" in c for c in data):
        return None, "Expected a list of cookies, each with a name and a value."
    names = {c["name"] for c in data}
    missing = {"c_user", "xs"} - names
    if missing:
        return None, f"Missing Facebook login cookie(s): {', '.join(sorted(missing))}. Export them while logged in."
    fb = [c for c in data if "facebook.com" in str(c.get("domain", ".facebook.com"))]
    return fb, f"{len(fb)} Facebook cookies look good."


def save_cookie_list(cookies: list) -> None:
    if COOKIES.exists():
        COOKIES.with_name(COOKIES.name + ".bak").write_text(COOKIES.read_text(encoding="utf-8"), encoding="utf-8")
    COOKIES.write_text(json.dumps(cookies, indent=2), encoding="utf-8")


# ================================================================ page

st.title("💼 FB Job Scraper")
stats = query(
    "SELECT (SELECT COUNT(*) FROM posts) AS posts, (SELECT COUNT(*) FROM jobs) AS jobs, "
    "(SELECT COUNT(*) FROM job_posts) - (SELECT COUNT(*) FROM jobs) AS reposts, "
    "(SELECT COUNT(*) FROM comments) AS comments, (SELECT MAX(last_scraped) FROM posts) AS last_scrape"
)
if not stats.empty:
    s = stats.iloc[0]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Posts", int(s.posts or 0))
    c2.metric("Unique jobs", int(s.jobs or 0))
    c3.metric("Reposts merged", int(s.reposts or 0))
    c4.metric("Comments", int(s.comments or 0))
    last = pd.to_datetime(s.last_scrape, errors="coerce", utc=True)  # stored in UTC; show local time
    c5.metric("Last scrape", last.tz_convert(datetime.now().astimezone().tzinfo).strftime("%Y-%m-%d %H:%M")
              if pd.notna(last) else "—")

tab_scrape, tab_jobs, tab_posts = st.tabs(["🔎 Scrape", "💼 Jobs", "📰 Posts"])

# ---------------------------------------------------------------- Scrape tab
with tab_scrape:
    settings = load_json(SETTINGS_FILE, {})
    state, job = run_state()
    busy = state == "running"
    form_col, log_col = st.columns([2, 3])

    with form_col:
        st.subheader("Scrape groups")
        groups_txt = st.text_area("Group links (one per line)", value="\n".join(settings.get("groups", [])),
                                  height=110, placeholder="https://www.facebook.com/groups/CSEJobBangladesh")
        a, b = st.columns(2)
        max_posts = a.number_input("Posts per group", 1, 1000, int(settings.get("max_posts", 20)))
        stop_known = b.number_input("Stop after N saved posts in a row", 0, 200, int(settings.get("stop_known", 0)),
                                    help="Stops a group early once it reaches posts already in the database. 0 = off.")
        c, d = st.columns(2)
        do_comments = c.checkbox("Scrape comments", settings.get("comments", True))
        do_shots = c.checkbox("Take screenshots", settings.get("screenshots", True))
        headless = d.checkbox("Hide browser window", settings.get("headless", False))
        do_process = d.checkbox("Process after scraping", settings.get("process", True),
                                help="OCR the images, let Groq judge the new posts, merge duplicates.")

        urls = [u.strip() for u in groups_txt.splitlines() if u.strip()]
        bad = []
        for u in urls:
            try:
                group_id_from_url(u)
            except ValueError:
                bad.append(u)
        if bad:
            st.error("Not a group link: " + ", ".join(bad))

        b1, b2 = st.columns(2)
        if b1.button("▶ Start scraping", type="primary", disabled=busy or not urls or bool(bad), width="stretch"):
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({"groups": urls, "max_posts": max_posts, "stop_known": stop_known,
                                                 "comments": do_comments, "screenshots": do_shots,
                                                 "headless": headless, "process": do_process}), encoding="utf-8")
            cmd = ["scrape", *urls, "--max-posts", str(max_posts)]
            if stop_known:
                cmd += ["--stop-after-known", str(stop_known)]
            if not do_comments:
                cmd.append("--no-comments")
            if not do_shots:
                cmd.append("--no-screenshots")
            if headless:
                cmd.append("--headless")
            start_run([cmd, ["process"]] if do_process else [cmd], f"Scrape {len(urls)} group(s)")
            st.rerun()
        if b2.button("⚙ Process only", disabled=busy, width="stretch",
                     help="OCR + Groq judging + duplicate merge for posts not processed yet."):
            start_run([["process"]], "Process")
            st.rerun()

        st.divider()
        st.subheader("Cookies")
        status = cookie_status()
        (st.success if status["ok"] else st.warning)(status["msg"])
        st.caption("The file can look fine and still be rejected by Facebook. **Check login** tries it for real.")
        k1, k2 = st.columns(2)
        if k1.button("Check login", disabled=busy, width="stretch"):
            start_run([["check-login", "--headless"]], "Check login")
            st.rerun()
        if k2.button("Log in with browser", disabled=busy, width="stretch",
                     help="Opens a browser window. Log in there; the cookies are saved automatically."):
            start_run([["login"]], "Log in with browser")
            st.rerun()
        with st.expander("Replace cookies (upload or paste)"):
            st.caption("Export cookies from your logged-in browser (e.g. the Cookie-Editor extension → Export → JSON).")
            up = st.file_uploader("cookies.json", type=["json"])
            pasted = st.text_area("…or paste the JSON here", height=100)
            raw = up.getvalue().decode("utf-8", errors="replace") if up else pasted
            if raw.strip():
                cookies, msg = validate_cookies(raw)
                if cookies is None:
                    st.error(msg)
                else:
                    st.info(msg)
                    if st.button("Save cookies", type="primary", disabled=busy):
                        save_cookie_list(cookies)
                        st.success("Saved (old file kept as cookies.json.bak). Click 'Check login' to test.")

    with log_col:
        @st.fragment(run_every=2)
        def run_panel():
            state, job = run_state()
            # Only this panel refreshes every 2 s; when a run ends, refresh the whole page once
            # so the buttons (disabled while running) and the numbers update.
            if state == "running":
                st.session_state["was_running"] = True
            elif st.session_state.pop("was_running", False):
                st.rerun(scope="app")
            if state == "idle":
                st.info("Nothing has run from here yet.")
                return
            icon = {"running": "⏳", "finished": "✅", "failed": "❌", "stopped": "⏹"}[state]
            st.subheader(f"{icon} {job.get('title', 'Run')} · {state}")
            st.caption(f"Started {job.get('started', '').replace('T', ' ')}")
            log = LOG_FILE.read_text(encoding="utf-8", errors="replace") if LOG_FILE.exists() else ""
            steps = re.findall(r"=== Step (\d+)/(\d+): (\w[\w-]*)", log)
            prog = re.findall(r"\[(\d+)/(\d+)\]", log.split("=== Step")[-1]) if steps else []
            if state == "running":
                label = f"Step {steps[-1][0]}/{steps[-1][1]} ({steps[-1][2]})" if steps else "Starting…"
                if prog:
                    n, total = map(int, prog[-1])
                    st.progress(min(n / max(total, 1), 1.0), text=f"{label}: {n}/{total}")
                else:
                    st.progress(0.0, text=label)
                if st.button("⏹ Stop", type="secondary"):
                    stop_run(job["pid"])
                    st.rerun()
            lines = [l for l in log.splitlines() if not l.startswith("__DONE__") and "warnings.warn" not in l
                     and "UserWarning" not in l and not l.startswith("HTTP Request")]
            st.code("\n".join(lines[-80:]) or "(no output yet)", language=None, height=520)
        run_panel()

# ---------------------------------------------------------------- Jobs tab
with tab_jobs:
    jobs = query(
        "SELECT v.*, (SELECT group_concat(DISTINCT p.group_id) FROM job_posts jp JOIN posts p USING (post_id) "
        "WHERE jp.job_id = v.job_id) AS groups FROM v_jobs v"
    )
    if jobs.empty:
        st.info("No jobs yet. Scrape a group with 'Process after scraping' ticked.")
    else:
        jobs["skills"] = jobs["skills"].fillna("[]").map(lambda s: ", ".join(json.loads(s or "[]")))
        jobs["link"] = jobs["post_urls"].fillna("[]").map(lambda s: (json.loads(s) or [None])[0])
        jobs["posted"] = pd.to_datetime(jobs["last_posted_at"], errors="coerce")

        def salary_str(r):
            if pd.notna(r.salary_min):
                s = f"{int(r.salary_min):,}"
                if pd.notna(r.salary_max) and r.salary_max != r.salary_min:
                    s += f"–{int(r.salary_max):,}"
                return f"{s} {txt(r.salary_currency)}/{txt(r.salary_period) or '?'}".strip()
            return txt(r.salary_text)
        jobs["salary"] = jobs.apply(salary_str, axis=1)

        f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
        text = f1.text_input("Search", placeholder="e.g. react remote, Mediusware, QA intern", key="job_search")
        all_groups = sorted({g for gs in jobs["groups"].dropna() for g in gs.split(",")})
        sel_groups = f2.multiselect("Group", all_groups, key="job_groups")
        sel_types = f3.multiselect("Type", sorted(jobs["employment_type"].dropna().unique()), key="job_types")
        sel_modes = f4.multiselect("Work mode", sorted(jobs["work_mode"].dropna().unique()), key="job_modes")
        g1, g2, g3, g4 = st.columns([2, 2, 2, 3])
        open_only = g1.checkbox("Deadline not passed", key="job_open",
                                help="Hides jobs whose stated deadline is before today. Jobs without a deadline stay.")
        salary_only = g2.checkbox("Has salary", key="job_salary")
        min_salary = g3.number_input("Min salary", 0, step=5000, key="job_min_salary")
        with g4:
            mask = date_filter(jobs, "last_posted_at", "job_dates")

        if text:
            mask &= search_mask(jobs, text, ["title", "company", "recruiter", "location", "skills", "summary",
                                             "description", "experience", "education"])
        if sel_groups:
            mask &= jobs["groups"].fillna("").map(lambda gs: any(g in gs.split(",") for g in sel_groups))
        if sel_types:
            mask &= jobs["employment_type"].isin(sel_types)
        if sel_modes:
            mask &= jobs["work_mode"].isin(sel_modes)
        if open_only:  # jobs without a stated deadline stay visible
            mask &= jobs["deadline"].fillna("9999-12-31").astype(str) >= date.today().isoformat()
        if salary_only:
            mask &= jobs["salary_min"].notna() | (jobs["salary_text"].fillna("") != "")
        if min_salary:
            mask &= jobs["salary_max"].fillna(jobs["salary_min"]).fillna(0) >= min_salary

        view = jobs[mask].sort_values("posted", ascending=False).reset_index(drop=True)
        cols = ["title", "company", "recruiter", "location", "salary", "salary_min", "deadline", "posted",
                "times_posted", "comment_count", "employment_type", "work_mode", "experience", "link"]
        text_cols = ["title", "company", "recruiter", "location", "salary", "deadline", "employment_type",
                     "work_mode", "experience"]
        table = view[cols].copy()
        table[text_cols] = table[text_cols].fillna("")  # blank cells instead of grey "None"
        st.caption(f"{len(view)} of {len(jobs)} jobs · click a column header to sort · click a row to open it")
        event = st.dataframe(
            table, hide_index=True, width="stretch", height=min(420, 38 + 35 * max(len(table), 1)),
            on_select="rerun", selection_mode="single-row", key="job_table",
            column_config={
                "title": st.column_config.TextColumn("Title", width="large"),
                "company": "Company", "recruiter": "Recruiter", "location": "Location", "salary": "Salary",
                "salary_min": st.column_config.NumberColumn("Min salary", format="%d"),
                "deadline": "Deadline",
                "posted": st.column_config.DatetimeColumn("Posted", format="YYYY-MM-DD HH:mm"),
                "times_posted": st.column_config.NumberColumn("Posted ×", help="How many times it was posted"),
                "comment_count": st.column_config.NumberColumn("💬"),
                "employment_type": "Type", "work_mode": "Mode", "experience": "Experience",
                "link": st.column_config.LinkColumn("Facebook", display_text="open ↗"),
            },
        )
        st.download_button("⬇ Download these jobs (CSV)", view.drop(columns=["posted"]).to_csv(index=False).encode("utf-8-sig"),
                           "jobs.csv", "text/csv")

        rows = event.selection.rows if event and event.selection else []
        if rows:
            j = view.iloc[rows[0]]
            v = lambda col: txt(j[col]) or "—"  # noqa: E731
            st.divider()
            st.header(txt(j.title) or "Untitled job")
            who = " · ".join(x for x in [txt(j.company), f"via {txt(j.recruiter)}" if txt(j.recruiter) else "",
                                         txt(j.location)] if x)
            st.markdown(f"**{who}**" if who else "*Company not stated*")
            if txt(j.summary):
                st.write(j.summary)
            d1, d2, d3 = st.columns(3)
            d1.markdown(f"**Salary:** {v('salary')}  \n**Deadline:** {v('deadline')}  \n"
                        f"**Experience:** {v('experience')}  \n**Education:** {v('education')}")
            d2.markdown(f"**Type:** {v('employment_type')}  \n**Work mode:** {v('work_mode')}  \n"
                        f"**Skills:** {v('skills')}")
            d3.markdown(f"**Apply email:** {v('apply_email')}  \n**Apply phone:** {v('apply_phone')}  \n"
                        f"**Apply link:** {v('apply_url')}")
            st.subheader(f"Posted {int(j.times_posted)} time(s)")
            posts = query(
                "SELECT p.*, jp.match_method FROM job_posts jp JOIN posts p USING (post_id) "
                "WHERE jp.job_id = ? ORDER BY p.posted_at", (int(j.job_id),))
            for _, p in posts.iterrows():
                how = ("original post" if p.match_method == "original"
                       else f"repost, matched by {txt(p.match_method).replace('_', ' ')}")
                with st.expander(f"{txt(p.group_id)} · {txt(p.author) or 'Unknown'} · {txt(p.posted_at)} · {how}",
                                 expanded=len(posts) == 1):
                    show_post(p)

# ---------------------------------------------------------------- Posts tab
with tab_posts:
    posts = query(
        "SELECT p.*, COALESCE(a.category, 'not processed') AS category, a.reasons, a.method, a.judgment, "
        "(SELECT group_concat(jp.job_id) FROM job_posts jp WHERE jp.post_id = p.post_id) AS job_ids "
        "FROM posts p LEFT JOIN post_analysis a USING (post_id)"
    )
    if posts.empty:
        st.info("No posts yet. Start a scrape in the Scrape tab.")
    else:
        posts["date"] = pd.to_datetime(posts["posted_at"], errors="coerce")
        posts["preview"] = posts["text"].fillna("").str.replace(r"\s+", " ", regex=True).str.slice(0, 140)

        f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
        text = f1.text_input("Search", placeholder="words in the post, author…", key="post_search")
        sel_groups = f2.multiselect("Group", sorted(posts["group_id"].dropna().unique()), key="post_groups")
        sel_cats = f3.multiselect("Category", sorted(posts["category"].unique()), key="post_cats",
                                  help="job_offer, job_seeking, course_ad, question, other — as judged by Groq")
        with f4:
            mask = date_filter(posts, "posted_at", "post_dates")
        if text:
            mask &= search_mask(posts, text, ["text", "author", "group_id", "reasons"])
        if sel_groups:
            mask &= posts["group_id"].isin(sel_groups)
        if sel_cats:
            mask &= posts["category"].isin(sel_cats)

        view = posts[mask].sort_values("date", ascending=False).reset_index(drop=True)
        st.caption(f"{len(view)} of {len(posts)} posts · click a row to open it")
        table = view[["date", "group_id", "author", "category", "preview", "reactions", "comment_count", "job_ids", "url"]].copy()
        table[["author", "job_ids"]] = table[["author", "job_ids"]].fillna("")
        event = st.dataframe(
            table,
            hide_index=True, width="stretch", height=min(420, 38 + 35 * max(len(table), 1)),
            on_select="rerun", selection_mode="single-row", key="post_table",
            column_config={
                "date": st.column_config.DatetimeColumn("Posted", format="YYYY-MM-DD HH:mm"),
                "group_id": "Group", "author": "Author", "category": "Category",
                "preview": st.column_config.TextColumn("Text", width="large"),
                "reactions": st.column_config.NumberColumn("👍"),
                "comment_count": st.column_config.NumberColumn("💬"),
                "job_ids": "Job #",
                "url": st.column_config.LinkColumn("Facebook", display_text="open ↗"),
            },
        )
        st.download_button("⬇ Download these posts (CSV)",
                           view.drop(columns=["date", "judgment"]).to_csv(index=False).encode("utf-8-sig"),
                           "posts.csv", "text/csv")

        rows = event.selection.rows if event and event.selection else []
        if rows:
            p = view.iloc[rows[0]]
            st.divider()
            st.subheader(f"{txt(p.author) or 'Unknown'} · {txt(p.group_id)}")
            verdict = f"**Category:** `{p.category}`"
            if txt(p.reasons):
                verdict += f" — {p.reasons}"
            if txt(p.method):
                verdict += f"  \n*judged by {p.method}*"
            if txt(p.job_ids):
                verdict += f"  \n**Job(s):** #{txt(p.job_ids).replace(',', ', #')} (see the Jobs tab)"
            st.markdown(verdict)
            show_post(p, open_comments=True)
            ocr = query("SELECT idx, ocr_text, ocr_conf, ocr_engine FROM post_images WHERE post_id = ? ORDER BY idx",
                        (p.post_id,))
            if not ocr.empty:
                with st.expander("Text read from the images (OCR)"):
                    for _, o in ocr.iterrows():
                        st.caption(f"Image {int(o.idx) + 1} · {txt(o.ocr_engine) or 'not read yet'} · "
                                   f"confidence {o.ocr_conf if pd.notna(o.ocr_conf) else '—'}")
                        st.text(txt(o.ocr_text) or "(nothing read)")
            if txt(p.judgment):
                with st.expander("Groq's full answer (JSON)"):
                    st.json(json.loads(p.judgment))
