"""Offline tests for job classification, field extraction and duplicate rules.
Examples are shortened versions of real posts. Run: python -m unittest discover tests"""
import unittest

from fbscraper.extract import extract
from fbscraper.jobs import Candidate, KnownJob, find_duplicate, norm_company
from fbscraper.rules import classify
from fbscraper.textnorm import clean, shingles, text_hash


class ClassifyTest(unittest.TestCase):
    def assertCategory(self, text, expected):
        self.assertEqual(classify(text)[0], expected, classify(text))

    def test_job_offers(self):
        self.assertCategory("WE ARE HIRING | Senior Cloud Engineer\nSalary: Negotiable\nSend your CV to hr@x.com", "job_offer")
        self.assertCategory("𝐖𝐞’𝐫𝐞 𝐇𝐢𝐫𝐢𝐧𝐠: 𝐅𝐥𝐮𝐭𝐭𝐞𝐫 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫\nStreams Tech is hiring a Flutter Developer", "job_offer")
        self.assertCategory("নিয়োগ বিজ্ঞপ্তি\nNirman Steel-এ একজন Female Marketing Executive নিয়োগ দেওয়া হবে।", "job_offer")
        self.assertCategory("পোস্ট :সহকারী সুপার ভাইজার\n★বেতন: 17,500 টাকা\nযোগাযোগ করেন।", "job_offer")
        self.assertCategory("We’re Looking for Curious People\nHawkInspect is building a small team. Internship.", "job_offer")

    def test_not_job_offers(self):
        self.assertCategory("Is there any opening for an entry level frontend developer?", "job_seeking")
        self.assertCategory("I am a student of 9th semester. I am looking for an internship! how can i find it?", "job_seeking")
        self.assertCategory("Free digital marketing course! Enroll now, limited seats. Certificate included.", "course_ad")
        self.assertCategory("Let's welcome our new members! Rafi, Nazim, Robiul", "other")
        self.assertCategory("AI-এর যুগে নতুনদের CSE/SWE/IT field-এ আসা উচিত ?", "question")


class ExtractTest(unittest.TestCase):
    def test_structured_post(self):
        f = extract(
            "We’re Hiring: Digital Marketing Executive (Full-Time)\n"
            "Company: KuiperZ\nLocation: Lalmatia, Dhaka\nSalary: BDT 20,000–25,000/month\n"
            "Experience: 1 year of experience\nDeadline: 15 October 2026\n"
            "Apply: https://kuiperz.io/careers/digital-marketing\nEmail: hr@kuiperz.io\nCall 01712-508458",
            "2026-09-20T10:00",
        )
        self.assertEqual(f["title"], "Digital Marketing Executive (Full-Time)")
        self.assertEqual(f["company"], "KuiperZ")
        self.assertEqual(f["location"], "Lalmatia, Dhaka")
        self.assertEqual((f["salary_min"], f["salary_max"], f["salary_currency"]), (20000, 25000, "BDT"))
        self.assertEqual(f["deadline"], "2026-10-15")
        self.assertEqual(f["apply_email"], "hr@kuiperz.io")
        self.assertEqual(f["apply_phone"], "01712508458")
        self.assertEqual(f["apply_url"], "https://kuiperz.io/careers/digital-marketing")
        self.assertEqual(f["employment_type"], "full_time")

    def test_headline_and_company_sentence(self):
        f = extract("WE’RE HIRING | MANAGER – AI & DIGITAL TRANSFORMATION A leading diversified group is looking for...", None)
        self.assertEqual(f["title"], "MANAGER – AI & DIGITAL TRANSFORMATION")
        self.assertIsNone(f["company"])  # "A leading diversified group" is anonymous
        f = extract("Sokrio Technologies Limited is seeking a detail-oriented and motivated Junior QA Engineer to join", None)
        self.assertEqual(f["title"], "Junior QA Engineer")
        self.assertEqual(f["company"], "Sokrio Technologies Limited")

    def test_ocr_quirks(self):
        f = extract("SEND YOUR CV HERE doctorspropertiesltd@gmail com 01712-508458 ENDDATE: 15.10.2026", "2026-09-29T21:00")
        self.assertEqual(f["apply_email"], "doctorspropertiesltd@gmail.com")
        self.assertEqual(f["deadline"], "2026-10-15")

    def test_deadline_without_year_rolls_forward(self):
        self.assertEqual(extract("Deadline: 5 January", "2026-12-20T10:00")["deadline"], "2027-01-05")

    def test_salary_units(self):
        f = extract("Salary: 70k-200k", None)
        self.assertEqual((f["salary_min"], f["salary_max"]), (70000, 200000))
        f = extract("Annual remuneration: 2.16 – 2.40 lakh BDT", None)
        self.assertEqual((f["salary_min"], f["salary_max"]), (216000, 240000))

    def test_company_experience_is_ignored(self):
        self.assertIsNone(extract("ATI has 29 years of experience in the industry.", None)["experience"])

    def test_maps_link_is_not_apply_url(self):
        self.assertIsNone(extract("Office: https://maps.app.goo.gl/VMg8NZKUF1", None)["apply_url"])


def cand(post_id, posted_at, text="", images=(), **fields):
    return Candidate(post_id, posted_at, fields, text_hash(text), shingles(text), list(images),
                     set(fields.pop("contacts", [])))


class DuplicateTest(unittest.TestCase):
    def setUp(self):
        self.job = KnownJob(1, {"title": "Mid-Level QA Engineer", "company": "Sokrio Technologies Limited",
                                "deadline": "2026-10-05"})
        self.job.members.append(cand("p1", "2026-09-01T10:00", "long text " * 30, images=["0" * 64],
                                     contacts=["hr@sokrio.com"]))

    def test_same_company_title_deadline(self):
        c = cand("p2", "2026-09-20T10:00", title="Mid Level QA Engineer", company="Sokrio Technologies Ltd.",
                 deadline="2026-10-05")
        self.assertEqual(find_duplicate(c, [self.job])[1], "same_company_title")

    def test_new_deadline_is_a_new_job(self):
        c = cand("p2", "2026-09-20T10:00", title="Mid-Level QA Engineer", company="Sokrio Technologies Limited",
                 deadline="2026-11-30")
        self.assertIsNone(find_duplicate(c, [self.job]))

    def test_same_title_different_company_is_not_duplicate(self):
        c = cand("p2", "2026-09-02T10:00", title="Mid-Level QA Engineer", company="Other Corp")
        self.assertIsNone(find_duplicate(c, [self.job]))

    def test_same_flyer_image_by_other_poster(self):
        c = cand("p2", "2026-09-03T10:00", images=["0" * 63 + "3"])  # 2 bits apart
        self.assertEqual(find_duplicate(c, [self.job])[1], "same_image")

    def test_same_template_different_role_is_not_duplicate(self):
        c = cand("p2", "2026-09-03T10:00", images=["0" * 64], title="Senior Accountant")
        self.assertIsNone(find_duplicate(c, [self.job]))

    def test_same_contact_and_title(self):
        c = cand("p2", "2026-09-03T10:00", title="QA Engineer (Mid-Level)", contacts=["hr@sokrio.com"])
        self.assertEqual(find_duplicate(c, [self.job])[1], "same_contact_title")

    def test_same_text(self):
        c = cand("p2", "2026-12-30T10:00", "long text " * 30)
        self.assertEqual(find_duplicate(c, [self.job])[1], "same_text")

    def test_two_positions_in_one_post_stay_separate(self):
        exec_job = KnownJob(2, {"title": "Executive - Sales & Marketing"})
        exec_job.members.append(cand("p9", "2026-09-01T10:00", images=["f" * 64]))
        c = cand("p9", "2026-09-01T10:00", images=["f" * 64], title="Sr. Executive - Sales & Marketing")
        self.assertIsNone(find_duplicate(c, [exec_job]))  # same post, same flyer: not a duplicate

    def test_repost_of_two_position_flyer_matches_each_position(self):
        a = KnownJob(2, {"title": "Executive - Sales & Marketing"})
        b = KnownJob(3, {"title": "Sr. Executive - Sales & Marketing"})
        for j in (a, b):
            j.members.append(cand("p9", "2026-09-01T10:00", images=["f" * 64]))
        c1 = cand("p10", "2026-09-02T10:00", images=["f" * 64], title="Sr. Executive - Sales & Marketing")
        c2 = cand("p10", "2026-09-02T10:00", images=["f" * 64], title="Executive - Sales & Marketing")
        self.assertEqual(find_duplicate(c1, [a, b])[0].job_id, 3)
        self.assertEqual(find_duplicate(c2, [a, b])[0].job_id, 2)

    def test_company_normalization(self):
        self.assertEqual(norm_company("Sokrio Technologies Ltd."), norm_company("SOKRIO Technologies Limited"))
        self.assertEqual(clean("𝐒𝐨𝐤𝐫𝐢𝐨"), "Sokrio")


if __name__ == "__main__":
    unittest.main()
