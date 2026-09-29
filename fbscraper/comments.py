"""Scrapes all comments and replies of a post from its own page."""
import logging
import re

from playwright.sync_api import Page

from .post import parse_count, parse_fb_date

log = logging.getLogger(__name__)

COMMENT_SEL = '[role="article"][aria-label^="Comment by"], [role="article"][aria-label^="Reply by"]'

# Buttons that load more comments or replies, e.g. "View more comments", "View all 5 replies",
# "Mohammad Al Amin replied · 1 Reply", "View 1 reply", "See more" (long comment text).
_EXPAND_RE = re.compile(
    r"^(view|see) (\d+ )?(more|previous|all)? ?(\d+ )?(comments?|repl(y|ies))\b"
    r"|\b\d+ repl(y|ies)$"
    r"|^see more$",
    re.I,
)

_EXTRACT_JS = r"""(sel) => [...document.querySelectorAll(sel)].map(a => {
  const ts = a.querySelector('a[href*="comment_id="]');
  const label = a.getAttribute('aria-label') || '';
  const body = a.querySelector('span[lang]') || a.querySelector('div[dir="auto"]');
  const react = [...a.querySelectorAll('[aria-label]')].map(x => x.getAttribute('aria-label'))
                   .find(l => /reactions?;/i.test(l));
  const author = a.querySelector('a[href*="/user/"], a[role="link"][href]:not([href*="comment_id="])');
  return {
    label,
    href: ts?.href || null,
    date: ts?.getAttribute('aria-label') || null,
    text: body?.innerText.trim() || '',
    reactions: react || null,
    author_url: author?.href || null,
  };
})"""

_LABEL_RE = re.compile(r"^(?:Comment|Reply) by (.+?)(?: to .+?'s comment)? (?:\S+ \S+ ago|just now|yesterday)$", re.I)


def _set_all_comments(page: Page) -> None:
    """Switch the sort from "Most relevant" to "All comments" so nothing is filtered out."""
    try:
        page.get_by_role("button", name=re.compile(r"^(Most relevant|Newest|Top comments)$")).first.click(timeout=4000)
        page.get_by_role("menuitem").filter(has_text="All comments").first.click(timeout=4000)
        page.wait_for_timeout(2500)
    except Exception:
        log.debug("Could not switch comment sort (post may have few comments)")


def _expand_all(page: Page, max_rounds: int) -> None:
    for _ in range(max_rounds):
        buttons = [
            b for b in page.get_by_role("button").all()
            if _EXPAND_RE.search((b.text_content() or "").strip())
        ]
        clicked = 0
        for b in buttons:
            try:
                b.click(timeout=2000)
                clicked += 1
                page.wait_for_timeout(600)
            except Exception:
                pass
        # Comments also lazy-load when the last one scrolls into view.
        # (The page keeps hidden duplicate copies, so pick the last *visible* one.)
        page.evaluate(
            "sel => [...document.querySelectorAll(sel)].filter(e => e.offsetParent).at(-1)"
            "?.scrollIntoView({block: 'center'})",
            COMMENT_SEL,
        )
        page.wait_for_timeout(1200)
        if not clicked:
            break


def _parse(raw: dict) -> dict | None:
    href = raw["href"]
    if not href:
        return None
    ids = dict(re.findall(r"(comment_id|reply_comment_id)=(\d+)", href))
    if "comment_id" not in ids:
        return None
    m = _LABEL_RE.match(raw["label"])
    reply_id = ids.get("reply_comment_id")
    return {
        "comment_id": reply_id or ids["comment_id"],
        "parent_id": ids["comment_id"] if reply_id else None,
        "author": m.group(1) if m else re.sub(r"^(Comment|Reply) by ", "", raw["label"]),
        "author_url": (raw["author_url"] or "").split("?")[0] or None,
        "posted_at": parse_fb_date(raw["date"]),
        "text": raw["text"],
        "reactions": parse_count((raw["reactions"] or "").split(" ")[0]) or 0,
        "url": re.sub(r"&__cft__.*$", "", href),
    }


def scrape_comments(page: Page, post_url: str, max_rounds: int = 40) -> list[dict]:
    page.goto(post_url, wait_until="domcontentloaded", timeout=90_000)
    try:
        page.wait_for_selector(COMMENT_SEL, timeout=15_000)
    except Exception:
        log.info("No comments rendered on %s", post_url)
        return []
    _set_all_comments(page)
    _expand_all(page, max_rounds)
    # The permalink page renders some comments twice; dedupe by id.
    comments = {}
    for raw in page.evaluate(_EXTRACT_JS, COMMENT_SEL):
        c = _parse(raw)
        if c and (c["comment_id"] not in comments or len(c["text"]) > len(comments[c["comment_id"]]["text"])):
            comments[c["comment_id"]] = c
    return list(comments.values())
