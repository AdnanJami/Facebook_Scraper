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

    def export(self) -> list[dict]:
        """All posts with their comments nested (replies under their parent comment)."""
        posts = []
        for p in self.db.execute("SELECT * FROM posts ORDER BY posted_at DESC, first_seen DESC"):
            post = dict(p)
            post["image_urls"] = json.loads(post["image_urls"] or "[]")
            rows = [dict(c) for c in self.db.execute(
                "SELECT * FROM comments WHERE post_id = ? ORDER BY posted_at", (p["post_id"],))]
            by_id = {c["comment_id"]: {**c, "replies": []} for c in rows}
            top = []
            for c in by_id.values():
                parent = by_id.get(c["parent_id"]) if c["parent_id"] else None
                (parent["replies"] if parent else top).append(c)
            post["comments"] = top
            posts.append(post)
        return posts

    def close(self) -> None:
        self.db.close()
