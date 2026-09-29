# Facebook_Scraper

Scrapes posts **and their comments** from Facebook groups, saving each post's data plus a clean screenshot of the whole post.

- Logs in with saved cookies (`cookies.json`), so no username or password.
- Per post: id, URL, author, date, full text ("See more" expanded), image URLs, reaction/comment/share counts, and a screenshot of the entire post.
- Per comment: id, parent (for replies), author, date, text, reactions, URL. Uses the "All comments" sort and expands "View more comments / replies".
- SQLite storage with upserts. Re-runs update existing posts, only fetch comments again when the count changed, and can stop early once they reach posts already saved.

## Setup

```bash
python -m venv scrapenv
scrapenv\Scripts\activate            # Windows (source scrapenv/bin/activate elsewhere)
pip install -r requirements.txt
playwright install chromium
```

### Cookies

Put your Facebook cookies in `cookies.json`, or create the file by logging in by hand once:

```bash
python -m fbscraper login
```

The file is refreshed after every run. **It is git-ignored: never commit it**, because it gives full access to the account.

## GUI

```bash
streamlit run app.py        # opens http://localhost:8501
```

| Tab | What you can do |
|---|---|
| 🔎 **Scrape** | group links, posts per group, "stop after N saved posts", comments/screenshots/hidden browser, "process after scraping"; start/stop a run and watch its live log. **Cookies:** status, *Check login*, *Log in with browser*, or upload/paste cookie JSON (it's checked before saving; the old file is kept as `cookies.json.bak`) |
| 💼 **Jobs** | unique jobs with search and filters (group, type, work mode, deadline not passed, salary, date range); sort by any column; CSV download. Click a job to see all its details and every post that advertised it (screenshot, text, comments) |
| 📰 **Posts** | every scraped post, including non-jobs, filtered by category/group/date. Click one to see Groq's verdict and reason, the OCR text and the comments, which makes wrong verdicts easy to spot |

Runs started from the GUI go through `python -m fbscraper.runner` in the background, logging to `output/gui_run.log`. They keep going if you close the browser tab.

## Command line

Pass one or more group links. Limits and delays are set in `config.yaml`:

```bash
python -m fbscraper scrape https://www.facebook.com/groups/<id>
python -m fbscraper scrape <group-url> <another-group-url> --max-posts 20
python -m fbscraper scrape <group-url> --stop-after-known 10   # incremental: stop at already-saved posts
python -m fbscraper scrape <group-url> --no-comments --no-screenshots --headless
python -m fbscraper export --out output/posts.json  # posts with comments/replies nested
```

Output:

```
output/
  scraper.db                          # all tables (see below)
  screenshots/<group_id>/<post_id>.png
  images/<post_id>_<n>.jpg            # post images, downloaded at scrape time (Facebook links expire)
  posts.json                          # after `export`
  jobs.json                           # after `jobs`
```

## Jobs: OCR, filtering and duplicates

```bash
python -m fbscraper process            # OCR new images, judge new posts, merge duplicates
python -m fbscraper process --rebuild  # redo dedupe for every post, reusing saved judgments (no API calls)
python -m fbscraper process --rejudge  # ask the judge again for every post (uses API quota)
python -m fbscraper jobs               # output/jobs.json: one entry per job, with every link + comments
```

`process` only handles posts it hasn't seen, so run it after every `scrape`.

1. **OCR:** each post's downloaded images are read with EasyOCR, English only by default, because adding Bangla makes it misread English text. Bangla is added for posts whose text is mostly Bangla. If an image couldn't be downloaded, the screenshot is OCR'd instead.
2. **Judge** (`fbscraper/judge.py`): an LLM on **Groq** (`openai/gpt-oss-120b` by default) reads the post text, the OCR text and the author's own comments. It returns strict JSON:
   - a category: `job_offer`, `job_seeking`, `course_ad`, `question` or `other`, with a one-line reason;
   - for job offers, **one entry per position**, with title, company, recruiter, location, salary (min/max/currency/period), deadline, experience, education, skills, how to apply and a summary.

   The API key comes from `GROQ_API_KEY` or `.env` (`GROQ_API_KEY=...`), which is git-ignored. Judgments are saved in `post_analysis.judgment`, so each post is judged only once. The free tier allows about 8k tokens a minute (roughly 2–3 posts a minute) and 1,000 requests a day. When Groq asks it to slow down, the pipeline waits and continues. If the quota runs out, it stops and the next `process` carries on.
   Set `judge: rules` in `config.yaml` to use the offline keyword/regex rules (`rules.py`, `extract.py`) instead.
3. **Dedupe** (`fbscraper/jobs.py`): a job offer is a duplicate of an existing job if, checked in order:

   | Rule | Meaning |
   |---|---|
   | `same_image` | same flyer image, even if someone else posted it (unless the job titles clearly differ) |
   | `same_text` | identical post text |
   | `same_company_title` | same company + similar title + same deadline, or no deadline on one side and posted within 30 days |
   | `same_contact_title` | same apply email/phone/link + similar title |
   | `similar_text` | 80%+ identical text posted within 30 days |

   Same company + title but a **different deadline** counts as a **new** job (a new hiring round). A duplicate doesn't create a job. It is only linked to the existing one, which gets every Facebook link and every post's comments, plus any details it was missing, such as a deadline stated only in the repost. Positions from the same post are never merged with each other, and a repost of a multi-position flyer is matched position by position.

Tables added by `process`:

| Table | Holds |
|---|---|
| `post_images` | downloaded image, fingerprint, OCR text/confidence |
| `post_analysis` | category, the judge's reason, which judge was used, the full saved judgment |
| `jobs` | one row per real job, with the extracted fields |
| `job_posts` | which posts advertise which job, and how the duplicate was detected |
| `v_jobs` (view) | each job with all its post links, times posted and comment count |

## How it works

| Module | Job |
|---|---|
| `fbscraper/session.py` | Launch Chromium (Playwright), load/save cookies, detect expired login |
| `fbscraper/feed.py` | Open the group (sorted), walk the virtualized feed, yield each post once |
| `fbscraper/post.py` | Expand, extract fields, hover the timestamp for the real link/date, take the screenshot |
| `fbscraper/comments.py` | Open the post page, switch to "All comments", expand everything, parse comments/replies |
| `fbscraper/storage.py` | SQLite schema, upserts and JSON export |
| `fbscraper/images.py` | Image download and fingerprints |
| `fbscraper/ocr.py` | EasyOCR on post images |
| `fbscraper/judge.py` | Groq LLM judge: job or not, one entry per position |
| `fbscraper/rules.py`, `extract.py` | Offline rules judge (`judge: rules`) |
| `fbscraper/jobs.py` | Runs the judge, dedupe, jobs table |
| `fbscraper/cli.py` | `scrape` / `process` / `jobs` / `login` / `export` commands |

Details worth knowing:
- **Screenshots:** Facebook's sticky top bar, group tab bar and pop-ups get painted over a post in element screenshots. They are hidden while capturing, so posts of any height come out as one clean image without scrolling and stitching.
- **Post link and date:** the visible timestamp text is obfuscated, and the real `/posts/<id>` link and full date only appear when the timestamp is hovered, so the scraper hovers it.
- **Comment totals** can be slightly higher than Facebook's counter, because "All comments" also includes comments Facebook flags as potential spam.

## Tests

```bash
python -m unittest discover tests
```

`legacy/` holds the earlier Selenium/Puppeteer experiments for reference.

Scraping Facebook is against Meta's Terms of Service, and heavy use can get an account checkpointed. Keep the delays and limits modest.
