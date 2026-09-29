"""Offline tests for the text parsing helpers. Run: python -m unittest discover tests"""
import unittest

from fbscraper.comments import _EXPAND_RE, _parse
from fbscraper.post import parse_count, parse_fb_date

GROUP_POST = "https://www.facebook.com/groups/818281132525134/posts/1789653588721212/"


class ParseCountTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(parse_count("12"), 12)
        self.assertEqual(parse_count("1,234"), 1234)
        self.assertEqual(parse_count("1.2K"), 1200)
        self.assertEqual(parse_count("3M"), 3_000_000)
        self.assertIsNone(parse_count(""))
        self.assertIsNone(parse_count("Like"))


class ParseDateTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_fb_date("Tuesday 29 September 2026 at 21:55"), "2026-09-29T21:55")
        self.assertEqual(parse_fb_date("Tuesday, September 29, 2026 at 9:55 PM"), "2026-09-29T21:55")
        self.assertEqual(parse_fb_date("Friday 4 September 2026 at 08:03"), "2026-09-04T08:03")

    def test_unknown_format_kept(self):
        self.assertEqual(parse_fb_date("29/09/2026"), "29/09/2026")
        self.assertIsNone(parse_fb_date(None))


class ExpandButtonTest(unittest.TestCase):
    def test_matches_load_more_buttons(self):
        for text in ("View more comments", "View previous comments", "View all 5 replies",
                     "View 1 reply", "View 12 more replies", "Mohammad Al Amin replied · 3 Replies",
                     "See more", "View more replies"):
            self.assertRegex(text, _EXPAND_RE, text)

    def test_ignores_other_buttons(self):
        for text in ("Reply", "Share", "Like", "Most relevant", "Leave a comment", "Follow", "See less"):
            self.assertNotRegex(text, _EXPAND_RE, text)


class ParseCommentTest(unittest.TestCase):
    def test_top_level_comment(self):
        c = _parse({
            "label": "Comment by Anonymous participant 356 3 hours ago",
            "href": GROUP_POST + "?comment_id=1798714574481780&__cft__[0]=AZabc&__tn__=R]-R",
            "date": "Tuesday 29 September 2026 at 18:52",
            "text": "Salary?",
            "reactions": "2 reactions; see who reacted to this",
            "author_url": "https://www.facebook.com/groups/818281132525134/user/100/?__cft__[0]=x",
        })
        self.assertEqual(c["comment_id"], "1798714574481780")
        self.assertIsNone(c["parent_id"])
        self.assertEqual(c["author"], "Anonymous participant 356")
        self.assertEqual(c["reactions"], 2)
        self.assertEqual(c["posted_at"], "2026-09-29T18:52")
        self.assertEqual(c["url"], GROUP_POST + "?comment_id=1798714574481780")
        self.assertEqual(c["author_url"], "https://www.facebook.com/groups/818281132525134/user/100/")

    def test_reply_links_to_parent(self):
        c = _parse({
            "label": "Reply by Mohammad Al Amin to Masuda Khondokar Munni's comment a week ago",
            "href": GROUP_POST + "?comment_id=1790599125293325&reply_comment_id=1790602435292994&__cft__[0]=x",
            "date": None, "text": "inbox", "reactions": None, "author_url": None,
        })
        self.assertEqual(c["comment_id"], "1790602435292994")
        self.assertEqual(c["parent_id"], "1790599125293325")
        self.assertEqual(c["author"], "Mohammad Al Amin")

    def test_without_link_is_skipped(self):
        self.assertIsNone(_parse({"label": "Comment by X 1 day ago", "href": None}))


if __name__ == "__main__":
    unittest.main()
