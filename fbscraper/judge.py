"""Groq LLM judge: decides whether a post is a job offer and extracts each position's details.

The model only reads what the post says (post text, OCR of its images, the author's own
comments) and returns strict JSON. Results are cached in post_analysis, so re-running
dedupe (`process --rebuild`) never calls the API again unless `--rejudge` is given.
"""
import json
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"

CATEGORIES = ["job_offer", "job_seeking", "course_ad", "question", "other"]


def _nullable(t):
    return {"type": [t, "null"]}


JOB_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "company", "recruiter", "location", "work_mode", "employment_type",
                 "salary_min", "salary_max", "salary_currency", "salary_period", "salary_text",
                 "experience", "education", "skills", "deadline", "apply_email", "apply_phone",
                 "apply_url", "summary"],
    "properties": {
        "title": {"type": "string"},
        "company": _nullable("string"),
        "recruiter": _nullable("string"),
        "location": _nullable("string"),
        "work_mode": {"type": ["string", "null"], "enum": ["onsite", "remote", "hybrid", None]},
        "employment_type": {"type": ["string", "null"],
                            "enum": ["full_time", "part_time", "internship", "contract", "freelance", None]},
        "salary_min": _nullable("integer"),
        "salary_max": _nullable("integer"),
        "salary_currency": _nullable("string"),
        "salary_period": {"type": ["string", "null"], "enum": ["month", "year", "hour", "project", None]},
        "salary_text": _nullable("string"),
        "experience": _nullable("string"),
        "education": _nullable("string"),
        "skills": {"type": "array", "items": {"type": "string"}},
        "deadline": _nullable("string"),
        "apply_email": _nullable("string"),
        "apply_phone": _nullable("string"),
        "apply_url": _nullable("string"),
        "summary": {"type": "string"},
    },
}

RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category", "reason", "jobs"],
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "reason": {"type": "string"},
        "jobs": {"type": "array", "items": JOB_SCHEMA},
    },
}

SYSTEM_PROMPT = """You review posts from Bangladeshi Facebook job groups (English, Bangla or mixed).
Each post comes with: the post date, the post text, OCR text read from the post's images (noisy:
letters may be wrong or missing), and comments the post's author wrote under it.

1. category:
   - job_offer: someone is offering one or more jobs/internships/paid positions.
   - job_seeking: a person looking for a job or introducing themselves for work.
   - course_ad: a course, training, bootcamp, webinar, study material or other promotion.
   - question: a question or discussion, not offering or seeking a specific job.
   - other: anything else (welcome posts, news, memes, spam).
   reason: one short sentence explaining the category.

2. jobs: only for job_offer, otherwise []. One entry per distinct position offered. A post
   listing "Executive" and "Sr. Executive" is two jobs.
   - Use only information stated in the post, image text or author comments. Never guess.
     Use null when something is not stated.
   - title: the role, clean and short, in English if the post gives it in English
     (e.g. "Senior QA Engineer"). No "We're hiring", no company name.
   - company: the employer's name. null when hidden ("A well-known company", "our client").
     recruiter: the agency/consultancy posting on the employer's behalf, if any.
   - location: city/area as written, e.g. "Banani, Dhaka".
   - salary_min/salary_max: plain integers ("25k" = 25000, "2.4 lakh" = 240000); equal when a
     single figure. salary_currency like "BDT" or "USD". salary_period: month/year/hour/project.
     salary_text: the salary as written ("Negotiable", "BDT 25,000-30,000/month").
   - deadline: YYYY-MM-DD. If the year is missing, use the first such date on/after the post date.
   - apply_email / apply_phone / apply_url: how to apply. Fix obvious OCR slips only when
     certain ("name@gmail com" -> "name@gmail.com"). Phone as digits, e.g. "01712508458".
     apply_url must not be a Facebook link or a map link.
   - experience: as written, e.g. "2-4 years", "Fresher". education: short, e.g. "BSc in CSE".
   - skills: up to 10 short lowercase skill names mentioned for the role.
   - summary: one English sentence describing the job.
Return only the JSON object."""


def load_api_key(env_file: str = ".env") -> str | None:
    key = os.environ.get("GROQ_API_KEY")
    if key:
        return key
    path = Path(env_file)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


class JudgeError(RuntimeError):
    pass


class GroqJudge:
    def __init__(self, api_key: str, model: str, reasoning_effort: str = "low", max_retries: int = 6):
        if not api_key:
            raise JudgeError("No Groq API key: set GROQ_API_KEY or put it in .env")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_retries = max_retries
        self.client = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {api_key}"})

    @property
    def name(self) -> str:
        return f"groq:{self.model}"

    def judge(self, post_date: str | None, post_text: str, image_text: str, author_comments: str) -> dict:
        user = json.dumps({
            "post_date": (post_date or "")[:10] or None,
            "post_text": post_text[:6000],
            "image_text_ocr": image_text[:6000],
            "author_comments": author_comments[:2000],
        }, ensure_ascii=False)
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "post_judgment", "strict": True, "schema": RESULT_SCHEMA}},
        }
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = self.reasoning_effort
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.client.post(API_URL, json=body)
            except httpx.HTTPError as e:
                wait = min(2 ** attempt, 60)
                log.warning("Groq request failed (%s); retrying in %ds", e, wait)
                time.sleep(wait)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("retry-after") or min(2 ** attempt, 60))
                log.warning("Groq %d (rate limit/server); waiting %.0fs", r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code != 200:
                raise JudgeError(f"Groq {r.status_code}: {r.text[:300]}")
            content = r.json()["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                log.warning("Groq returned invalid JSON (attempt %d)", attempt)
        raise JudgeError(f"Groq failed after {self.max_retries} attempts")
