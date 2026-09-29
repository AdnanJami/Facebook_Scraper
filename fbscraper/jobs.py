"""Turns scraped posts into a de-duplicated jobs table.

For each post (oldest first) a judge reads the post text, the OCR text of its images and the
author's own comments, and returns a category plus one entry per position offered:
  - GroqJudge (default): an LLM on Groq, see judge.py
  - RulesJudge: the offline keyword/regex rules in rules.py + extract.py
Judgments are cached in post_analysis, so re-running dedupe never re-calls the judge.

Each position is then either linked to an existing job (a duplicate) or becomes a new job.
Duplicate rules, strongest first (among equal-strength matches, the most similar title wins):
  same_image          same flyer image (fingerprint distance <= IMAGE_MAX_DIST), unless titles clearly differ
  same_text           identical post text after normalization, unless titles clearly differ
  same_company_title  same company + similar title + same deadline
                      (or no deadline on one side and posted within WINDOW_DAYS)
  same_contact_title  shares an email / phone / apply link + similar title, deadlines not conflicting
  similar_text        >= SIMILAR_TEXT near-identical text, posted within WINDOW_DAYS, deadlines not conflicting
Same company + title but a *different* deadline is a new job (a new hiring round).
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
from .judge import JudgeError
from .rules import classify
from .textnorm import clean, jaccard, shingles, text_hash, words

log = logging.getLogger(__name__)

IMAGE_MAX_DIST = 20      # of 256 bits
SIMILAR_TEXT = 0.8
WINDOW_DAYS = 30
TITLE_SIMILAR = 0.5

JOB_FIELDS = ("title", "company", "recruiter", "location", "work_mode", "employment_type", "salary_min",
              "salary_max", "salary_currency", "salary_period", "salary_text", "experience", "education",
              "skills", "deadline", "apply_email", "apply_phone", "apply_url", "summary", "description")

_COMPANY_NOISE = r"\b(ltd|limited|plc|inc|llc|pvt|private|company|co|bd|bangladesh|the)\b"


# ---------------------------------------------------------------- judges

class RulesJudge:
    """Offline fallback: keyword rules + regex extraction (one job per post)."""
    name = "rules"

    def judge(self, post_date, post_text, image_text, author_comments) -> dict:
        own = "\n\n".join(p for p in (post_text, author_comments) if p)
        full = "\n\n".join(p for p in (own, image_text) if p)
        category, _, reasons = classify(full)
        jobs = []
        if category == "job_offer":
            # Written text is exact and OCR is noisy: extract from the written text, OCR fills gaps.
            if len(clean(own)) < 80:
                f = extract(full, post_date)
            else:
                f = extract(own, post_date)
                for k, v in extract(full, post_date).items():
                    if v and not f.get(k):
                        f[k] = v
            jobs.append(f)
        return {"category": category, "reason": "; ".join(reasons), "jobs": jobs}


# ---------------------------------------------------------------- matching

def norm_company(c: str | None) -> str | None:
    if not c:
        return None
    c = re.sub(_COMPANY_NOISE, " ", clean(c).lower())
    c = re.sub(r"[\W_]+", "", c)
    return c or None


def title_sim(a: str | None, b: str | None) -> float:
    return jaccard(words(a), words(b)) if a and b else 0.0


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


def contacts_of(f: dict) -> set[str]:
    out = set()
    for e in [f.get("apply_email"), *(f.get("emails") or [])]:
        if e:
            out.add(e.strip().lower())
    for p in [f.get("apply_phone"), *(f.get("phones") or [])]:
        digits = re.sub(r"\D", "", p or "")
        if len(digits) >= 10:
            out.add(digits[-11:])
    if f.get("apply_url"):
        out.add(f["apply_url"].lower().split("?")[0].rstrip("/"))
    return out


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


def _match(c: Candidate, job: KnownJob) -> tuple[int, str, float] | None:
    """Strongest rule linking candidate to job: (strength rank, method, score), lower rank = stronger."""
    f, jf = c.fields, job.fields
    title, jtitle = f.get("title"), jf.get("title")
    conflict = titles_conflict(title, jtitle)
    for m in job.members:
        if c.image_hashes and m.image_hashes and not conflict:
            dist = min(hamming(x, y) for x in c.image_hashes for y in m.image_hashes)
            if dist <= IMAGE_MAX_DIST:
                return 0, "same_image", 1 - dist / 256
    if not conflict and c.text_hash and any(c.text_hash == m.text_hash for m in job.members):
        return 1, "same_text", 1.0
    if _deadlines_conflict(f, jf):
        return None
    if titles_similar(title, jtitle):
        comp = norm_company(f.get("company"))
        if comp and comp == norm_company(jf.get("company")):
            if f.get("deadline") and f.get("deadline") == jf.get("deadline"):
                return 2, "same_company_title", 1.0
            near = [d for d in (_days_apart(c.posted_at, m.posted_at) for m in job.members) if d is not None]
            if near and min(near) <= WINDOW_DAYS:
                return 2, "same_company_title", 0.9
        if c.contacts & (contacts_of(jf) | set().union(*(m.contacts for m in job.members))):
            return 3, "same_contact_title", 0.9
    if not conflict:
        for m in job.members:
            d = _days_apart(c.posted_at, m.posted_at)
            if d is not None and d > WINDOW_DAYS:
                continue
            sim = jaccard(c.shingles, m.shingles)
            if sim >= SIMILAR_TEXT:
                return 4, "similar_text", round(sim, 3)
    return None


def find_duplicate(c: Candidate, jobs: list[KnownJob]) -> tuple[KnownJob, str, float] | None:
    best = None
    for job in jobs:
        if any(m.post_id == c.post_id for m in job.members):
            continue  # a post listing two positions must not merge them with each other
        hit = _match(c, job)
        if hit:
            key = (hit[0], -title_sim(c.fields.get("title"), job.fields.get("title")))
            if best is None or key < best[0]:
                best = (key, job, hit[1], hit[2])
    return (best[1], best[2], best[3]) if best else None


# ---------------------------------------------------------------- pipeline

class JobPipeline:
    def __init__(self, db, judge):
        self.db = db
        self.judge = judge
        self.jobs: list[KnownJob] = []

    def _candidate(self, post_id, posted_at, post_text, full_text, fields) -> Candidate:
        hashes = [r[0] for r in self.db.execute(
            "SELECT dhash FROM post_images WHERE post_id = ? AND dhash IS NOT NULL", (post_id,))]
        # Compare on the post's own text when it has enough; OCR noise lowers similarity.
        basis = post_text if len(clean(post_text)) >= 200 else full_text
        return Candidate(post_id, posted_at, fields, text_hash(post_text), shingles(basis), hashes, contacts_of(fields))

    def load(self) -> None:
        cols = ", ".join(JOB_FIELDS)
        for row in self.db.execute(f"SELECT job_id, {cols} FROM jobs"):
            fields = dict(zip(JOB_FIELDS, row[1:]))
            fields["skills"] = json.loads(fields["skills"] or "[]")
            self.jobs.append(KnownJob(row[0], fields))
        by_id = {j.job_id: j for j in self.jobs}
        for job_id, post_id, posted_at, text in self.db.execute(
            "SELECT jp.job_id, p.post_id, p.posted_at, p.text FROM job_posts jp JOIN posts p USING (post_id)"
        ):
            job = by_id[job_id]
            ocr, _ = self.texts(post_id, text or "", None)
            full = "\n\n".join(p for p in (text, ocr) if p)
            job.members.append(self._candidate(post_id, posted_at, text or "", full, job.fields))

    def texts(self, post_id: str, text: str, author: str | None) -> tuple[str, str]:
        """(OCR text of the images, author's own comments)"""
        ocr = "\n".join(r[0] for r in self.db.execute(
            "SELECT ocr_text FROM post_images WHERE post_id = ? AND ocr_text != '' ORDER BY idx", (post_id,)))
        own = "\n".join(r[0] for r in self.db.execute(
            "SELECT text FROM comments WHERE post_id = ? AND author = ? ORDER BY posted_at", (post_id, author)))
        return ocr, own

    def _judgment(self, post_id, posted_at, text, ocr, own) -> dict:
        row = self.db.execute("SELECT method, judgment FROM post_analysis WHERE post_id = ?", (post_id,)).fetchone()
        if row and row[0] == self.judge.name and row[1]:
            return json.loads(row[1])
        if len(clean(text)) >= 600:
            ocr = ocr[:1500]  # long written posts: the flyer mostly repeats them; saves API tokens
        return self.judge.judge(posted_at, text, ocr, own)

    def process_post(self, post_id, posted_at, text, author) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text = text or ""
        ocr, own = self.texts(post_id, text, author)
        if not clean(text) and not clean(ocr):
            result = {"category": "other", "reason": "no text or readable image", "jobs": []}
        else:
            result = self._judgment(post_id, posted_at, text, ocr, own)
        category = result.get("category") or "other"
        full = "\n\n".join(p for p in (text, ocr, own) if p)
        self.db.execute(
            "INSERT OR REPLACE INTO post_analysis (post_id, category, score, reasons, clean_text, text_hash, "
            "analyzed_at, method, judgment) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (post_id, category, result.get("reason"), full, text_hash(text), now, self.judge.name,
             json.dumps(result, ensure_ascii=False)),
        )
        if category != "job_offer" or not result.get("jobs"):
            self.db.commit()
            return category
        outcomes = []
        for job_fields in result["jobs"]:
            fields = {k: job_fields.get(k) for k in JOB_FIELDS}
            fields["skills"] = job_fields.get("skills") or []
            fields["emails"], fields["phones"] = job_fields.get("emails"), job_fields.get("phones")
            fields["description"] = text
            cand = self._candidate(post_id, posted_at, text, full, fields)
            match = find_duplicate(cand, self.jobs)
            if match:
                job, method, score = match
                self._link(job.job_id, post_id, method, score)
                self._merge(job, cand, now)
                job.members.append(cand)
                outcomes.append(f"duplicate ({method}) of job {job.job_id}")
            else:
                job_id = self._create(fields, posted_at, now)
                self._link(job_id, post_id, "original", 1.0)
                self.jobs.append(KnownJob(job_id, {k: fields.get(k) for k in JOB_FIELDS}, [cand]))
                outcomes.append(f"new job {job_id}: {fields.get('title')}")
        self.db.commit()
        return "; ".join(outcomes)

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


def analyze(db, judge, rebuild: bool = False, rejudge: bool = False, require_ocr: bool = True) -> dict:
    """Judge + dedupe every post not placed yet. rebuild: redo dedupe for all posts (cached
    judgments reused). rejudge: also ask the judge again for every post."""
    other_method = db.execute("SELECT COUNT(*) FROM post_analysis WHERE method IS NOT ? ", (judge.name,)).fetchone()[0]
    if other_method and not rebuild:
        log.info("%d posts were judged by a different judge; rebuilding everything with %s", other_method, judge.name)
        rebuild = True
    if rebuild or rejudge:
        db.executescript("DELETE FROM job_posts; DELETE FROM jobs;")
    if rejudge:
        db.execute("DELETE FROM post_analysis")
    db.commit()

    pipe = JobPipeline(db, judge)
    pipe.load()
    # Not placed yet = never judged by this judge, or a job offer not linked to any job.
    # Posts whose images still await OCR wait for a later run, so they're never judged on half their text.
    rows = db.execute(
        "SELECT post_id, posted_at, text, author FROM posts p WHERE "
        "(NOT EXISTS (SELECT 1 FROM post_analysis a WHERE a.post_id = p.post_id AND a.method = ?) "
        " OR (EXISTS (SELECT 1 FROM post_analysis a WHERE a.post_id = p.post_id AND a.category = 'job_offer') "
        "     AND NOT EXISTS (SELECT 1 FROM job_posts jp WHERE jp.post_id = p.post_id))) "
        f"{'' if not require_ocr else 'AND NOT EXISTS (SELECT 1 FROM post_images i WHERE i.post_id = p.post_id AND i.ocr_at IS NULL)'} "
        "ORDER BY posted_at IS NULL, posted_at, first_seen",
        (judge.name,),
    ).fetchall()
    stats: dict[str, int] = {}
    log.info("Judging %d posts with %s", len(rows), judge.name)
    for n, (post_id, posted_at, text, author) in enumerate(rows, 1):
        try:
            result = pipe.process_post(post_id, posted_at, text, author)
        except JudgeError as e:
            log.error("Stopped at post %s: %s. Run `process` again later to continue.", post_id, e)
            stats["not judged yet"] = len(rows) - n + 1
            break
        for part in result.split("; "):
            key = "new" if part.startswith("new job") else "duplicate" if part.startswith("duplicate") else part
            stats[key] = stats.get(key, 0) + 1
        log.info("[%d/%d] %s  %-12s %s", n, len(rows), post_id, (author or "?")[:12], result)
    db.commit()
    return stats
