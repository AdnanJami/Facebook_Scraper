"""Browser startup and cookie-based login."""
import json
import logging
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

log = logging.getLogger(__name__)

_SAMESITE = {"lax": "Lax", "strict": "Strict", "none": "None", "no_restriction": "None"}


class NotLoggedIn(RuntimeError):
    pass


def _to_playwright(c: dict) -> dict:
    """Accept cookies saved by Selenium, Playwright or browser extensions."""
    d = {k: c[k] for k in ("name", "value", "domain", "path", "secure", "httpOnly") if k in c}
    d.setdefault("path", "/")
    exp = c.get("expiry", c.get("expires", c.get("expirationDate")))
    if exp and exp > 0:
        d["expires"] = float(exp)
    d["sameSite"] = _SAMESITE.get(str(c.get("sameSite", "lax")).lower(), "Lax")
    return d


def load_cookies(context: BrowserContext, path: Path) -> None:
    if not path.exists():
        raise NotLoggedIn(f"{path} not found. Run `python -m fbscraper login` once to create it.")
    cookies = json.loads(path.read_text(encoding="utf-8"))
    context.add_cookies([_to_playwright(c) for c in cookies])
    log.info("Loaded %d cookies from %s", len(cookies), path)


def _has_session(context: BrowserContext) -> bool:
    # Facebook deletes c_user as soon as it rejects a session, so it's the reliable signal.
    return any(c["name"] == "c_user" for c in context.cookies("https://www.facebook.com"))


def save_cookies(context: BrowserContext, path: Path) -> None:
    """Save in Selenium-compatible form so the legacy scripts can still read the file."""
    if not _has_session(context):
        log.warning("Not saving cookies: the browser is logged out (kept the existing %s)", path)
        return
    out = []
    for c in context.cookies("https://www.facebook.com"):
        d = {k: c[k] for k in ("name", "value", "domain", "path", "secure", "httpOnly")}
        d["sameSite"] = c.get("sameSite", "Lax")
        if c.get("expires", -1) > 0:
            d["expiry"] = int(c["expires"])
        out.append(d)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("Saved %d refreshed cookies to %s", len(out), path)


def is_logged_in(page: Page) -> bool:
    if "/login" in page.url or "/checkpoint" in page.url:
        return False
    if not _has_session(page.context):
        return False
    return page.locator('input[name="email"], input[name="pass"]').count() == 0


@contextmanager
def open_browser(cfg, load=True):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=cfg.headless,
            args=["--disable-blink-features=AutomationControlled", "--disable-notifications"],
        )
        context = browser.new_context(
            viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
            locale="en-US",
        )
        context.set_default_timeout(10_000)
        if load:
            load_cookies(context, Path(cfg.cookies_file))
        try:
            yield context
        finally:
            browser.close()


def interactive_login(cfg) -> None:
    """Open a window, let the user log in by hand once, then save the cookies."""
    with open_browser(cfg, load=False) as context:
        page = context.new_page()
        page.goto("https://www.facebook.com/login")
        print("Log in to Facebook in the opened window. Waiting up to 5 minutes...")
        page.wait_for_function(
            "() => document.cookie.includes('c_user=')", timeout=300_000, polling=1000
        )
        page.wait_for_timeout(3000)
        save_cookies(context, Path(cfg.cookies_file))
