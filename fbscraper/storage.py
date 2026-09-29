"""SQLite storage with upserts, so re-running a scrape updates instead of duplicating."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id        TEXT PRIMARY KEY,
    group_id       TEXT,
    url            TEXT,
    author         TEXT,
    author_url     TEXT,
    posted_at      TEXT,
    text           TEXT,
    image_urls     TEXT,   -- JSON list
    reactions      INTEGER,
    comment_count  INTEGER,
    shares         INTEGER,
    screenshot     TEXT,
    comments_scraped_for INTEGER,  -- comment_count when comments were last scraped
    first_seen     TEXT,
    last_scraped   TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    comment_id  TEXT PRIMARY KEY,
    post_id     TEXT REFERENCES posts(post_id),
    parent_id   TEXT,           -- NULL for top-level comments
    author      TEXT,
    author_url  TEXT,
    posted_at   TEXT,
    text        TEXT,
    reactions   INTEGER,
    url         TEXT,
    scraped_at  TEXT
);
CREATE INDEX IF NOT EXISTS comments_post ON comments(post_id);

-- ---- Job pipeline (python -m fbscraper process) ----
CREATE TABLE IF NOT EXISTS post_images (
    image_id    INTEGER PRIMARY KEY,
    post_id     TEXT REFERENCES posts(post_id),
    idx         INTEGER,        -- order within the post
    source_url  TEXT,
    local_path  TEXT,           -- NULL if the download failed
    dhash       TEXT,           -- image fingerprint, finds the same flyer re-posted
    ocr_text    TEXT,
    ocr_conf    REAL,
    ocr_engine  TEXT,
    ocr_at      TEXT,
    UNIQUE (post_id, idx)
);
CREATE TABLE IF NOT EXISTS post_analysis (
    post_id     TEXT PRIMARY KEY REFERENCES posts(post_id),
    category    TEXT,           -- job_offer | job_seeking | course_ad | question | other
    score       INTEGER,
    reasons     TEXT,           -- why: the judge's reason, or which rules fired
    clean_text  TEXT,           -- post text + OCR + the author's own comments
    text_hash   TEXT,
    analyzed_at TEXT,
    method      TEXT,           -- who judged: "groq:<model>" or "rules"
    judgment    TEXT            -- full JSON result (cached, reused by `process --rebuild`)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id           INTEGER PRIMARY KEY,
    title            TEXT,
    company          TEXT,
    location         TEXT,
    work_mode        TEXT,      -- onsite | remote | hybrid
    employment_type  TEXT,      -- full_time | part_time | internship | contract | freelance
    salary_min       INTEGER,
    salary_max       INTEGER,
    salary_currency  TEXT,
    salary_text      TEXT,      -- as written, e.g. "BDT 25,000 - 30,000/month", "Negotiable"
    experience       TEXT,
    education        TEXT,
    skills           TEXT,      -- JSON list
    deadline         TEXT,      -- ISO date
    apply_email      TEXT,
    apply_phone      TEXT,
    apply_url        TEXT,
    description      TEXT,
    first_posted_at  TEXT,
    last_posted_at   TEXT,
    created_at       TEXT,
    updated_at       TEXT,
    recruiter        TEXT,      -- agency posting on the employer's behalf
    salary_period    TEXT,      -- month | year | hour | project
    summary          TEXT       -- one-sentence description
);
CREATE TABLE IF NOT EXISTS job_posts (   -- every post advertising the job, incl. duplicates
    job_id        INTEGER REFERENCES jobs(job_id),
    post_id       TEXT REFERENCES posts(post_id),
    match_method  TEXT,        -- original | same_image | same_text | same_company_title | same_contact_title | similar_text
    match_score   REAL,
    PRIMARY KEY (job_id, post_id)
);
CREATE INDEX IF NOT EXISTS job_posts_post ON job_posts(post_id);
CREATE VIEW IF NOT EXISTS v_jobs AS
SELECT j.*,
       (SELECT json_group_array(p.url) FROM job_posts jp JOIN posts p USING (post_id)
         WHERE jp.job_id = j.job_id) AS post_urls,
       (SELECT COUNT(*) FROM job_posts jp WHERE jp.job_id = j.job_id) AS times_posted,
       (SELECT COUNT(*) FROM job_posts jp JOIN comments c USING (post_id)
         WHERE jp.job_id = j.job_id) AS comment_count
FROM jobs j;
"""

POST_FIELDS = ("post_id", "group_id", "url", "author", "author_url", "posted_at", "text",
               "image_urls", "reactions", "comment_count", "shares", "screenshot")
COMMENT_FIELDS = ("comment_id", "post_id", "parent_id", "author", "author_url", "posted_at",
                  "text", "reactions", "url")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        added = {
            "post_analysis": {"method": "TEXT", "judgment": "TEXT"},
            "jobs": {"recruiter": "TEXT", "salary_period": "TEXT", "summary": "TEXT"},
        }
        for table, cols in added.items():
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()

    def get_post(self, post_id: str):
        return self.db.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()

    def upsert_post(self, post: dict) -> None:
        row = {k: post.get(k) for k in POST_FIELDS}
        row["image_urls"] = json.dumps(post.get("image_urls") or [])
        now = _now()
        cols = ", ".join(POST_FIELDS)
        marks = ", ".join(f":{k}" for k in POST_FIELDS)
        # Keep existing values when the new scrape didn't find them (e.g. no screenshot this run).
        updates = ", ".join(f"{k} = COALESCE(excluded.{k}, {k})" for k in POST_FIELDS if k != "post_id")
        self.db.execute(
            f"INSERT INTO posts ({cols}, first_seen, last_scraped) VALUES ({marks}, :now, :now) "
            f"ON CONFLICT(post_id) DO UPDATE SET {updates}, last_scraped = :now",
            {**row, "now": now},
        )
        self.db.commit()

    def save_comments(self, post_id: str, comments: list[dict], comment_count: int | None) -> None:
        now = _now()
        cols = ", ".join(COMMENT_FIELDS)
        marks = ", ".join(f":{k}" for k in COMMENT_FIELDS)
        updates = ", ".join(f"{k} = excluded.{k}" for k in COMMENT_FIELDS if k != "comment_id")
        for c in comments:
            self.db.execute(
                f"INSERT INTO comments ({cols}, scraped_at) VALUES ({marks}, :now) "
                f"ON CONFLICT(comment_id) DO UPDATE SET {updates}, scraped_at = :now",
                {**{k: c.get(k) for k in COMMENT_FIELDS}, "post_id": post_id, "now": now},
            )
        self.db.execute("UPDATE posts SET comments_scraped_for = ? WHERE post_id = ?",
                        (comment_count, post_id))
        self.db.commit()

    def comment_tree(self, post_id: str) -> list[dict]:
        """A post's comments with replies nested under their parent comment."""
        rows = [dict(c) for c in self.db.execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY posted_at", (post_id,))]
        by_id = {c["comment_id"]: {**c, "replies": []} for c in rows}
        top = []
        for c in by_id.values():
            parent = by_id.get(c["parent_id"]) if c["parent_id"] else None
            (parent["replies"] if parent else top).append(c)
        return top

    def export(self) -> list[dict]:
        """All posts with their comments nested."""
        posts = []
        for p in self.db.execute("SELECT * FROM posts ORDER BY posted_at DESC, first_seen DESC"):
            post = dict(p)
            post["image_urls"] = json.loads(post["image_urls"] or "[]")
            post["comments"] = self.comment_tree(p["post_id"])
            posts.append(post)
        return posts

    def export_jobs(self) -> list[dict]:
        """One entry per job: extracted fields + every post that advertised it (link,
        screenshot, how it was matched) with that post's comments."""
        jobs = []
        for j in self.db.execute("SELECT * FROM jobs ORDER BY first_posted_at DESC"):
            job = dict(j)
            job["skills"] = json.loads(job["skills"] or "[]")
            job["posts"] = []
            for p in self.db.execute(
                "SELECT p.post_id, p.url, p.group_id, p.author, p.author_url, p.posted_at, p.screenshot, "
                "p.reactions, jp.match_method, jp.match_score FROM job_posts jp JOIN posts p USING (post_id) "
                "WHERE jp.job_id = ? ORDER BY p.posted_at", (j["job_id"],)
            ):
                post = dict(p)
                post["comments"] = self.comment_tree(p["post_id"])
                job["posts"].append(post)
            jobs.append(job)
        return jobs

    def close(self) -> None:
        self.db.close()
