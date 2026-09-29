"""Command line entry point: python -m fbscraper {scrape,login,export}."""
import argparse
import json
import logging
import random
import sys
from pathlib import Path

from . import comments as comments_mod
from . import post as post_mod
from .config import Config, load_config
from .feed import group_id_from_url, iter_posts, open_group
from .session import NotLoggedIn, interactive_login, is_logged_in, open_browser, save_cookies
from .storage import Storage

log = logging.getLogger("fbscraper")


def scrape_group(cfg: Config, context, store: Storage, group_url: str) -> dict:
    group_id = group_id_from_url(group_url)
    stats = {"group": group_id, "posts": 0, "new": 0, "comments": 0, "failed": 0}
    feed_page = context.new_page()
    comment_page = context.new_page() if cfg.comments else None
    feed_page.bring_to_front()

    open_group(feed_page, group_url, cfg.sort)
    if not is_logged_in(feed_page):
        raise NotLoggedIn("Facebook shows a login page: cookies expired. Run `python -m fbscraper login`.")
    post_mod.install_overlay_hiding(feed_page)
    log.info("Scraping group %s (%s)", group_id, feed_page.title())

    known_streak = 0
    for el in iter_posts(feed_page):
        try:
            data = post_mod.extract(el, group_id)
        except Exception as e:
            stats["failed"] += 1
            log.warning("Failed to read a post: %s", e)
            continue

        existing = store.get_post(data["post_id"])
        if existing:
            known_streak += 1
        else:
            known_streak = 0
            stats["new"] += 1
            if cfg.screenshots:
                shot = cfg.screenshot_dir / group_id / f"{data['post_id']}.png"
                data["screenshot"] = post_mod.screenshot(el, shot)
        store.upsert_post(data)
        stats["posts"] += 1
        log.info("[%d/%d] %s %s | %s | %d comments",
                 stats["posts"], cfg.max_posts, "new " if not existing else "seen", data["post_id"],
                 (data["author"] or "?")[:30], data["comment_count"])

        needs_comments = (
            comment_page and data["comment_count"] > 0 and data["url"]
            and (not existing or existing["comments_scraped_for"] != data["comment_count"])
        )
        if needs_comments:
            try:
                found = comments_mod.scrape_comments(comment_page, data["url"], cfg.max_comment_rounds)
                store.save_comments(data["post_id"], found, data["comment_count"])
                stats["comments"] += len(found)
                log.info("      saved %d comments/replies", len(found))
            except Exception as e:
                log.warning("      comments failed for %s: %s", data["url"], e)
            feed_page.bring_to_front()

        if stats["posts"] >= cfg.max_posts:
            break
        if cfg.stop_after_known and known_streak >= cfg.stop_after_known:
            log.info("Stopping: %d posts in a row were already saved", known_streak)
            break
        feed_page.wait_for_timeout(random.uniform(cfg.delay_min, cfg.delay_max) * 1000)

    feed_page.close()
    if comment_page:
        comment_page.close()
    return stats


def cmd_scrape(cfg: Config) -> int:
    if not cfg.groups:
        log.error("No groups given. Add them to config.yaml or pass --group URL.")
        return 2
    store = Storage(cfg.db_path)
    all_stats = []
    try:
        with open_browser(cfg) as context:
            for group_url in cfg.groups:
                all_stats.append(scrape_group(cfg, context, store, group_url))
            save_cookies(context, Path(cfg.cookies_file))
    except NotLoggedIn as e:
        log.error("%s", e)
        return 1
    finally:
        store.close()
    print("\nSummary")
    for s in all_stats:
        print(f"  group {s['group']}: {s['posts']} posts ({s['new']} new), "
              f"{s['comments']} comments, {s['failed']} failed")
    print(f"  database: {cfg.db_path}")
    return 0


def cmd_export(cfg: Config, out: str) -> int:
    store = Storage(cfg.db_path)
    posts = store.export()
    store.close()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(posts)} posts to {out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fbscraper", description="Facebook group post + comment scraper")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scrape", help="scrape posts (and comments) from groups")
    sc.add_argument("--group", action="append", dest="groups", help="group URL (repeatable)")
    sc.add_argument("--max-posts", type=int)
    sc.add_argument("--stop-after-known", type=int)
    sc.add_argument("--sort", choices=["chronological", "recent_activity", "top", "default"])
    sc.add_argument("--no-comments", dest="comments", action="store_const", const=False)
    sc.add_argument("--no-screenshots", dest="screenshots", action="store_const", const=False)
    sc.add_argument("--headless", action="store_const", const=True)

    sub.add_parser("login", help="log in by hand once and save cookies")

    ex = sub.add_parser("export", help="export the database to JSON")
    ex.add_argument("--out", default="output/posts.json")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("asyncio",):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    overrides = {k: getattr(args, k, None) for k in
                 ("groups", "max_posts", "stop_after_known", "sort", "comments", "screenshots", "headless")}
    cfg = load_config(args.config, overrides)

    if args.cmd == "scrape":
        return cmd_scrape(cfg)
    if args.cmd == "login":
        interactive_login(cfg)
        return 0
    return cmd_export(cfg, args.out)


if __name__ == "__main__":
    sys.exit(main())
