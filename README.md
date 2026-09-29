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

## Usage

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
  scraper.db                          # tables: posts, comments
  screenshots/<group_id>/<post_id>.png
  posts.json                          # after `export`
```

## How it works

| Module | Job |
|---|---|
| `fbscraper/session.py` | Launch Chromium (Playwright), load/save cookies, detect expired login |
| `fbscraper/feed.py` | Open the group (sorted), walk the virtualized feed, yield each post once |
| `fbscraper/post.py` | Expand, extract fields, hover the timestamp for the real link/date, take the screenshot |
| `fbscraper/comments.py` | Open the post page, switch to "All comments", expand everything, parse comments/replies |
| `fbscraper/storage.py` | SQLite upserts and JSON export |
| `fbscraper/cli.py` | `scrape` / `login` / `export` commands |

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
