"""Turns scraped posts into a de-duplicated jobs table.

For each post (oldest first): build its full text (post + OCR + the author's own comments),
classify it, and for job offers extract fields and either link it to an existing job
(duplicate) or create a new one.

Duplicate rules, checked in order (first match wins):
  same_image          same flyer image (fingerprint distance <= IMAGE_MAX_DIST), unless titles clearly differ
  same_text           identical post text after normalization
  same_company_title  same company + similar title + same deadline
                      (or no deadline on one side and posted within WINDOW_DAYS)
  same_contact_title  shares an email / phone / apply link + similar title, deadlines not conflicting
  similar_text        >= SIMILAR_TEXT near-identical text, posted within WINDOW_DAYS, deadlines not conflicting
Same company + title but a *different* deadline is treated as a new job (a new hiring round).
Duplicates don't create a job; they're only linked in job_posts, so the job keeps every
Facebook link and every post's comments.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .extract import extract
from .images import hamming
from .rules import classify
from .textnorm import clean, jaccard, shingles, text_hash, words

log = logging.getLogger(__name__)

IMAGE_MAX_DIST = 20      # of 256 bits
SIMILAR_TEXT = 0.8
WINDOW_DAYS = 30
TITLE_SIMILAR = 0.5

JOB_FIELDS = ("title", "company", "location", "work_mode", "employment_type", "salary_min", "salary_max",
              "salary_currency", "salary_text", "experience", "education", "skills", "deadline",
              "apply_email", "apply_phone", "apply_url", "description")

_COMPANY_NOISE = r"\b(ltd|limited|plc|inc|llc|pvt|private|company|co|bd|bangladesh|the)\b"


def norm_company(c: str | None) -> str | None:
    if not c:
        return None
    c = re.sub(_COMPANY_NOISE, " ", clean(c).lower())
    c = re.sub(r"[\W_]+", "", c)
    return c or None


def titles_similar(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return jaccard(wa, wb) >= TITLE_SIMILAR or len(small & big) / len(small) >= 0.8


def titles_conflict(a: str | None, b: str | None) -> bool:
    return bool(a and b and jaccard(words(a), words(b)) < 0.2 and not titles_similar(a, b))


def _days_apart(a: str | None, b: str | None) -> float | None:
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 86400
    except (TypeError, ValueError):
        return None


@dataclass
class Candidate:
    post_id: str
    posted_at: str | None
    fields: dict
    text_hash: str | None
    shingles: set
    image_hashes: list[str]
    contacts: set = field(default_factory=set)


@dataclass
class KnownJob:
    job_id: int
    fields: dict
    members: list[Candidate] = field(default_factory=list)


def _deadlines_conflict(a: dict, b: dict) -> bool:
    return bool(a.get("deadline") and b.get("deadline") and a["deadline"] != b["deadline"])


def find_duplicate(c: Candidate, jobs: list[KnownJob]) -> tuple[KnownJob, str, float] | None:
    f = c.fields
    for job in jobs:
        for m in job.members:
            if c.image_hashes and m.image_hashes:
                dist = min(hamming(x, y) for x in c.image_hashes for y in m.image_hashes)
                if dist <= IMAGE_MAX_DIST and not titles_conflict(f.get("title"), job.fields.get("title")):
                    return job, "same_image", 1 - dist / 256
            if c.text_hash and c.text_hash == m.text_hash:
                return job, "same_text", 1.0
    comp = norm_company(f.get("company"))
    for job in jobs:
        jf = job.fields
        if _deadlines_conflict(f, jf) or not titles_similar(f.get("title"), jf.get("title")):
            continue
        if comp and comp == norm_company(jf.get("company")):
            if f.get("deadline") and f.get("deadline") == jf.get("deadline"):
                return job, "same_company_title", 1.0
            near = [d for d in (_days_apart(c.posted_at, m.posted_at) for m in job.members) if d is not None]
            if near and min(near) <= WINDOW_DAYS:
                return job, "same_company_title", 0.9
        if c.contacts & set().union(*(m.contacts for m in job.members)):
            return job, "same_contact_title", 0.9
    for job in jobs:
        if _deadlines_conflict(f, job.fields):
            continue
        for m in job.members:
            d = _days_apart(c.posted_at, m.posted_at)
            if d is not None and d > WINDOW_DAYS:
                continue
            sim = jaccard(c.shingles, m.shingles)
            if sim >= SIMILAR_TEXT:
                return job, "similar_text", round(sim, 3)
    return None


class JobPipeline:
    def __init__(self, db):
        self.db = db
        self.jobs: list[KnownJob] = []

    # ---------- loading existing state ----------
    def _candidate(self, post_id, posted_at, post_text, full_text, fields) -> Candidate:
        hashes = [r[0] for r in self.db.execute(
            "SELECT dhash FROM post_images WHERE post_id = ? AND dhash IS NOT NULL", (post_id,))]
        contacts = set(fields.get("emails") or []) | set(fields.get("phones") or [])
        if fields.get("apply_url"):
            contacts.add(fields["apply_url"].lower().split("?")[0].rstrip("/"))
        # Compare on the post's own text when it has enough; OCR noise lowers similarity.
        basis = post_text if len(clean(post_text)) >= 200 else full_text
        return Candidate(post_id, posted_at, fields, text_hash(post_text), shingles(basis), hashes, contacts)

    def load(self) -> None:
        cols = ", ".join(JOB_FIELDS)
        for row in self.db.execute(f"SELECT job_id, {cols} FROM jobs"):
            fields = dict(zip(JOB_FIELDS, row[1:]))
            fields["skills"] = json.loads(fields["skills"] or "[]")
            self.jobs.append(KnownJob(row[0], fields))
        by_id = {j.job_id: j for j in self.jobs}
        for job_id, post_id, posted_at, text, author in self.db.execute(
            "SELECT jp.job_id, p.post_id, p.posted_at, p.text, p.author FROM job_posts jp JOIN posts p USING (post_id)"
        ):
            own, full = self.texts(post_id, text or "", author)
            fields = self.fields(own, full, posted_at)
            by_id[job_id].members.append(self._candidate(post_id, posted_at, text or "", full, fields))

    # ---------- per post ----------
    def texts(self, post_id: str, text: str, author: str | None) -> tuple[str, str]:
        """(written text = post + author's own comments, full text = written + OCR of the images)"""
        ocr = [r[0] for r in self.db.execute(
            "SELECT ocr_text FROM post_images WHERE post_id = ? AND ocr_text != '' ORDER BY idx", (post_id,))]
        own_comments = [r[0] for r in self.db.execute(
            "SELECT text FROM comments WHERE post_id = ? AND author = ? ORDER BY posted_at", (post_id, author))]
        own = "\n\n".join(p for p in [text, *own_comments] if p)
        return own, "\n\n".join(p for p in [own, *ocr] if p)

    @staticmethod
    def fields(own: str, full: str, posted_at: str | None) -> dict:
        """Written text is exact while OCR is noisy, so fields come from the written text first
        and OCR only fills what's missing. Image-only posts rely on OCR entirely."""
        if len(clean(own)) < 80:
            return extract(full, posted_at)
        f = extract(own, posted_at)
        for k, v in extract(full, posted_at).items():
            if v and not f.get(k):
                f[k] = v
        return f

    def process_post(self, post_id, posted_at, text, author, description) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        own, full = self.texts(post_id, text or "", author)
        category, score, reasons = classify(full)
        self.db.execute(
            "INSERT OR REPLACE INTO post_analysis (post_id, category, score, reasons, clean_text, text_hash, analyzed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, category, score, json.dumps(reasons, ensure_ascii=False), full, text_hash(text or ""), now),
        )
        if category != "job_offer":
            return category
        fields = self.fields(own, full, posted_at)
        fields["description"] = description
        cand = self._candidate(post_id, posted_at, text or "", full, fields)
        match = find_duplicate(cand, self.jobs)
        if match:
            job, method, score = match
            self._link(job.job_id, post_id, method, score)
            self._merge(job, cand, now)
            job.members.append(cand)
            return f"duplicate ({method}) of job {job.job_id}"
        job_id = self._create(fields, posted_at, now)
        self._link(job_id, post_id, "original", 1.0)
        self.jobs.append(KnownJob(job_id, {k: fields.get(k) for k in JOB_FIELDS}, [cand]))
        return f"new job {job_id}"

    def _create(self, f: dict, posted_at: str | None, now: str) -> int:
        values = [json.dumps(f.get(k) or [], ensure_ascii=False) if k == "skills" else f.get(k) for k in JOB_FIELDS]
        cur = self.db.execute(
            f"INSERT INTO jobs ({', '.join(JOB_FIELDS)}, first_posted_at, last_posted_at, created_at, updated_at) "
            f"VALUES ({', '.join('?' * len(JOB_FIELDS))}, ?, ?, ?, ?)",
            (*values, posted_at, posted_at, now, now),
        )
        return cur.lastrowid

    def _link(self, job_id: int, post_id: str, method: str, score: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO job_posts (job_id, post_id, match_method, match_score) VALUES (?, ?, ?, ?)",
                        (job_id, post_id, method, score))

    def _merge(self, job: KnownJob, c: Candidate, now: str) -> None:
        """A duplicate may state things the original didn't (e.g. a deadline): fill the gaps."""
        missing = {k: c.fields.get(k) for k in JOB_FIELDS
                   if k != "description" and not job.fields.get(k) and c.fields.get(k)}
        for k, v in missing.items():
            job.fields[k] = v
            self.db.execute(f"UPDATE jobs SET {k} = ? WHERE job_id = ?",
                            (json.dumps(v, ensure_ascii=False) if k == "skills" else v, job.job_id))
        self.db.execute(
            "UPDATE jobs SET first_posted_at = MIN(COALESCE(first_posted_at, :p), COALESCE(:p, first_posted_at)), "
            "last_posted_at = MAX(COALESCE(last_posted_at, :p), COALESCE(:p, last_posted_at)), updated_at = :now "
            "WHERE job_id = :id",
            {"p": c.posted_at, "now": now, "id": job.job_id},
        )


def analyze(db, rebuild: bool = False, require_ocr: bool = True) -> dict:
    if rebuild:
        db.executescript("DELETE FROM job_posts; DELETE FROM jobs; DELETE FROM post_analysis;")
    pipe = JobPipeline(db)
    pipe.load()
    # Posts whose images still await OCR are left for a later run, so they're never judged on half their text.
    rows = db.execute(
        "SELECT post_id, posted_at, text, author FROM posts p WHERE NOT EXISTS "
        "(SELECT 1 FROM post_analysis a WHERE a.post_id = p.post_id) "
        f"{'' if not require_ocr else 'AND NOT EXISTS (SELECT 1 FROM post_images i WHERE i.post_id = p.post_id AND i.ocr_at IS NULL)'} "
        "ORDER BY posted_at IS NULL, posted_at, first_seen"
    ).fetchall()
    stats: dict[str, int] = {}
    for post_id, posted_at, text, author in rows:
        result = pipe.process_post(post_id, posted_at, text, author, text)
        key = result.split(" (")[0].split(" job")[0]  # "new", "duplicate", or the category
        stats[key] = stats.get(key, 0) + 1
        log.info("%s  %-10s %s", post_id, (author or "?")[:10], result)
    db.commit()
    return stats
