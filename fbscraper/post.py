"""Everything done to a single post in the group feed: expand, extract, screenshot."""
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Locator, Page

log = logging.getLogger(__name__)

# Marks fixed/sticky layers (top bar, group tab bar, chat, pop-ups) so they can be hidden.
# Otherwise they get painted on top of the post in element screenshots.
_MARK_OVERLAYS_JS = """() => {
  for (const el of document.querySelectorAll('body *')) {
    if (el.hasAttribute('data-fbs-hide') || el.closest('div[role="feed"]')) continue;
    const p = getComputedStyle(el).position;
    if (p === 'fixed' || p === 'sticky') el.setAttribute('data-fbs-hide', '');
  }
}"""
_HIDE_CSS = "[data-fbs-hide]{visibility:hidden !important}"

_EXTRACT_JS = r"""e => {
  const q = s => e.querySelector(s);
  const btnCount = label => q(`[role=button][aria-label="${label}"]`)?.innerText.trim() || null;
  const nameEl = q('[data-ad-rendering-role="profile_name"]');
  const photoLinks = [...e.querySelectorAll('a[href*="/photo"]')];
  return {
    author: nameEl ? nameEl.innerText.split('\n')[0].trim() : null,
    author_url: nameEl?.querySelector('a[href]')?.href || null,
    text: (q('[data-ad-rendering-role="story_message"]') || q('[data-ad-preview="message"]'))?.innerText.trim() || '',
    image_urls: [...new Set(photoLinks.flatMap(a => [...a.querySelectorAll('img')].map(i => i.src)))],
    photo_hrefs: photoLinks.map(a => a.href),
    links: [...e.querySelectorAll('a[href]')].map(a => a.href),
    reactions: btnCount('Like'),
    comment_count: btnCount('Leave a comment'),
    shares: btnCount('Send this to friends or post it on your profile.'),
  };
}"""

_POST_URL_RE = re.compile(r"facebook\.com/groups/([^/]+)/(?:posts|permalink)/(\d+)")


def parse_count(s: str | None) -> int | None:
    """'12' -> 12, '1.2K' -> 1200, '3M' -> 3000000."""
    if not s:
        return None
    m = re.fullmatch(r"([\d.,]+)\s*([KkMm]?)", s.strip())
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1))


_DATE_FORMATS = (
    "%A %d %B %Y at %H:%M",        # Tuesday 29 September 2026 at 21:55  (English UK)
    "%A, %B %d, %Y at %I:%M %p",   # Tuesday, September 29, 2026 at 9:55 PM  (English US)
    "%A, %d %B %Y at %H:%M",
)


def parse_fb_date(s: str | None) -> str | None:
    """Facebook's full date text -> ISO 'YYYY-MM-DDTHH:MM' (local time), so dates sort.
    Unknown formats are kept as-is rather than dropped."""
    if not s:
        return None
    s = " ".join(s.split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat(timespec="minutes")
        except ValueError:
            pass
    return s


def install_overlay_hiding(page: Page) -> None:
    page.add_style_tag(content=_HIDE_CSS)


def is_post(el: Locator) -> bool:
    return el.locator('[data-ad-rendering-role="profile_name"], [aria-label="Leave a comment"]').count() > 0


def expand(el: Locator) -> None:
    """Click the post's own "See more" (not the ones in inline comments)."""
    msg = el.locator('[data-ad-rendering-role="story_message"]')
    btn = (msg if msg.count() else el).get_by_role("button", name="See more", exact=True)
    if btn.count():
        try:
            btn.first.click(timeout=3000)
            el.page.wait_for_timeout(800)
        except Exception as e:
            log.debug("See more click failed: %s", e)


_DATE_TIP_RE = re.compile(r"\b\d{4}\b.*\d{1,2}:\d{2}")  # "Tuesday 29 September 2026 at 21:55"


def _reveal_permalink(el: Locator) -> tuple[str | None, str | None]:
    """Facebook fills in the timestamp link's real href, and shows the full date as a
    tooltip, only after hovering it (the visible timestamp text is obfuscated)."""
    page = el.page
    url = date = None
    # Element handles, not locators: hovering rewrites the href, which would make an
    # href-based locator stop matching the very link it just hovered.
    candidates = el.locator(
        'a[href^="?__cft__"], a[href="#"], a[href*="/posts/"]:not([href*="comment_id"])'
    ).element_handles()[:4]
    for a in candidates:
        try:
            a.hover(timeout=2000)
        except Exception:
            continue
        try:  # the date tooltip only belongs to the timestamp link; others time out quickly
            page.wait_for_function(
                "re => [...document.querySelectorAll('[role=tooltip]')].some(t => new RegExp(re).test(t.innerText))",
                arg=_DATE_TIP_RE.pattern,
                timeout=2500 if not date else 800,
            )
        except Exception:
            pass
        href = a.get_attribute("href") or ""
        if not url and _POST_URL_RE.search(href):
            url = href.split("?")[0]
        if not date:
            tips = [t.strip() for t in page.locator('[role="tooltip"]').all_inner_texts()]
            date = next((t for t in tips if _DATE_TIP_RE.search(t)), None)
        if url and date:
            break
        page.mouse.move(5, page.viewport_size["height"] - 5)
    return url, date


def _post_id(url: str | None, data: dict, group_id: str) -> str:
    if url and (m := _POST_URL_RE.search(url)):
        return m.group(2)
    # Photo links carry the post id as set=gm.<id>
    for h in data["photo_hrefs"]:
        if m := re.search(r"set=(?:gm|pcb)\.(\d+)", h):
            return m.group(1)
    # Last resort: stable hash of author + text
    digest = hashlib.sha1(f"{group_id}|{data['author']}|{data['text']}".encode()).hexdigest()[:16]
    return f"h_{digest}"


def extract(el: Locator, group_id: str) -> dict:
    el.scroll_into_view_if_needed()
    expand(el)
    url, date = _reveal_permalink(el)
    el.page.mouse.move(5, el.page.viewport_size["height"] - 5)  # dismiss hover cards/tooltips
    data = el.evaluate(_EXTRACT_JS)
    post_id = _post_id(url, data, group_id)
    if not url and not post_id.startswith("h_"):
        url = f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/"
    return {
        "post_id": post_id,
        "group_id": group_id,
        "url": url,
        "author": data["author"],
        "author_url": (data["author_url"] or "").split("?")[0] or None,
        "posted_at": parse_fb_date(date),
        "text": data["text"],
        "image_urls": data["image_urls"],
        "reactions": parse_count(data["reactions"]) or 0,
        "comment_count": parse_count(data["comment_count"]) or 0,
        "shares": parse_count(data["shares"]) or 0,
    }


def screenshot(el: Locator, path: Path) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    el.page.wait_for_timeout(400)
    el.page.evaluate(_MARK_OVERLAYS_JS)
    try:
        el.screenshot(path=str(path), animations="disabled", timeout=30_000)
        return str(path)
    except Exception as e:
        log.warning("Screenshot failed for %s: %s", path.name, e)
        return None
