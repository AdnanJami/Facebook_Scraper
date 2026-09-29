"""Walks a group feed, yielding each post element once."""
import logging
import re
from typing import Iterator

from playwright.sync_api import Locator, Page

from .post import is_post

log = logging.getLogger(__name__)

FEED_ITEMS = 'div[role="feed"] > div'
_SORT_PARAM = {
    "chronological": "CHRONOLOGICAL",
    "recent_activity": "RECENT_ACTIVITY",
    "top": "TOP_POSTS",
}


def group_id_from_url(url: str) -> str:
    m = re.search(r"/groups/([^/?#]+)", url)
    if not m:
        raise ValueError(f"Not a Facebook group URL: {url}")
    return m.group(1)


def open_group(page: Page, group_url: str, sort: str) -> None:
    url = f"https://www.facebook.com/groups/{group_id_from_url(group_url)}/"
    if sort in _SORT_PARAM:
        url += f"?sorting_setting={_SORT_PARAM[sort]}"
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_selector('div[role="feed"]', timeout=60_000)
    page.wait_for_timeout(3000)


def iter_posts(page: Page, max_idle_scrolls: int = 6) -> Iterator[Locator]:
    """Yield unseen feed items in order, scrolling for more until the feed stops growing.

    Facebook virtualizes the feed (removes items far off-screen), so items are marked
    as seen in the DOM instead of being tracked by index.
    """
    unseen = page.locator(f"{FEED_ITEMS}:not([data-fbs-id])")
    idle = 0
    n = 0
    while idle < max_idle_scrolls:
        if unseen.count() == 0:
            page.mouse.wheel(0, 2000)
            try:
                page.wait_for_function(
                    f"() => document.querySelector('{FEED_ITEMS}:not([data-fbs-id])')",
                    timeout=8000,
                )
                idle = 0
            except Exception:
                idle += 1
                log.debug("No new feed items after scroll (%d/%d)", idle, max_idle_scrolls)
            continue
        # Tag the item with a fixed id and yield a locator for that id: a locator like
        # "first unseen item" would silently move on to the next post once this one is tagged.
        n += 1
        unseen.first.evaluate("(e, n) => e.setAttribute('data-fbs-id', n)", n)
        item = page.locator(f'{FEED_ITEMS}[data-fbs-id="{n}"]')
        # Freshly inserted items are often empty placeholders; give them a moment to render.
        if not is_post(item):
            page.wait_for_timeout(1000)
            if not is_post(item):
                continue
        yield item
    log.info("Reached the end of the feed (no new posts after %d scrolls)", max_idle_scrolls)
