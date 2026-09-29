"""Rule-based post classification: is this a job offer, or something else?

Each rule adds or subtracts points and is recorded in `reasons`, so a wrong decision
can be traced back to the rule that caused it (see post_analysis.reasons).
"""
import re
import unicodedata

from .textnorm import clean

# (points, name, pattern)
OFFER_RULES = [
    (3, "hiring", r"\b(we\s*'?\s*re|we\s+are|is|are|now|urgent(ly)?)\s+hiring\b|\bhiring\s*(!|:|for\b)|^\s*hiring\b"
                  r"|\bwe\s*'?\s*a?r?e?\s*h[il1]{0,2}r[il1]{0,2}n[gc]\b"),  # OCR slips: "WE HRING", "HIIRIING"
    (3, "vacancy", r"\b(job\s+(vacancy|circular|opening|opportunity|post)|vacancy|vacancies|recruitment|career\s+opportunit)"),
    (3, "bn_job", r"নিয়োগ|নিয়োগ|চাকরি|চাকুরী|চাকরী|কর্মী\s*(আবশ্যক|প্রয়োজন|নেওয়া)|লোক\s*(লাগবে|নেওয়া)"),
    (3, "send_cv", r"\b(send|submit|drop|share|email)\s+(your\s+|us\s+your\s+|an?\s+)?(updated\s+)?(cv|resume)\b|\bcv\s+(to|at)\b"),
    (2, "apply", r"\bapply\s+(now|here|through|via|using|at|to|before|by|link|online)\b|\bhow\s+to\s+apply\b|\binterested\s+candidates?\b"),
    (2, "looking_for_role", r"\b(looking\s+for|seeking|we\s+need)\s+(an?\s+|the\s+|some\s+)?([\w/&-]+\s+){0,4}"
                            r"(developer|engineer|executive|manager|intern|officer|designer|specialist|assistant|"
                            r"instructor|teacher|trainee|associate|analyst|lead|marketer|writer|accountant|people|candidates?)"),
    (2, "deadline", r"\b(deadline|last\s+date|apply\s+before|closing\s+date|end\s*date)\b|শেষ\s*তারিখ"),
    (2, "title_label", r"^\W*(position|post|designation|job\s*title|vacancy|পদের\s*নাম|পদ|পোস্ট)\s*(name)?\s*[:：\-–]"),
    (2, "job_type",r"\b(job\s+type|employment\s+type|job\s+location|job\s+responsibilities|key\s+responsibilities|job\s+description|position\s*:|designation\s*:)"),
    (1, "salary", r"\b(salary|remuneration|compensation)\b|বেতন|স্যালারি"),
    (1, "requirements", r"\b(requirements?|qualifications?|eligibility|responsibilities|benefits)\b|যোগ্যতা"),
    (1, "work_terms", r"\b(full[\s-]?time|part[\s-]?time|internship|remote|on[\s-]?site|hybrid|work\s+from\s+home)\b"),
    (1, "bn_apply", r"আবেদন|যোগাযোগ"),
]

SEEKING = r"\b(i\s*'?\s*a?m|i\s+am)\s+(currently\s+)?(looking|searching|seeking)\s+for\b|\blooking\s+for\s+(an?\s+)?(job|internship|intern\s+position)\b" \
          r"|\b(is|are)\s+there\s+any\s+(opening|vacanc|job|internship|opportunit)|\bany\s+(opening|vacancy|vacancies)\b" \
          r"|#?open\s*to\s*work|\bneed\s+a\s+job\b|\bhire\s+me\b|\bhow\s+can\s+i\s+(find|get)\b" \
          r"|\bopen\s+to\s+(new\s+)?(opportunit|roles|positions)|\bmy\s+(cv|resume|portfolio)\b"
# Someone introducing themselves ("Hi, I'm Arif. I'm a fresher web developer...") is job seeking
# no matter how many job words the post contains.
SELF_INTRO = r"^\W*(hi|hello|hey)\b[^\n]{0,20}\b(i\s*'?\s*a?m|my\s+name\s+is)\b|\bi\s*'?\s*a?m\s+an?\s+(fresher|fresh\s+graduate|recent\s+graduate|student)\b"
COURSE = r"\b(course|training|workshop|webinar|bootcamp|masterclass|enroll|enrol|admission|certificat|class(es)?\s+start|batch|seminar|study\s+hub|tutorials?)\b|কোর্স|ট্রেনিং|ফ্রি\s*ক্লাস"
COMMUNITY = r"\b(welcome\s+our\s+new\s+members|let'?s\s+welcome|new\s+members)\b"


# Bangla letters like য় have two Unicode spellings; normalize patterns the same way as text.
OFFER_RULES = [(p, n, re.compile(unicodedata.normalize("NFKC", pat), re.I | re.M)) for p, n, pat in OFFER_RULES]
SEEKING, SELF_INTRO, COURSE, COMMUNITY = (unicodedata.normalize("NFKC", p) for p in (SEEKING, SELF_INTRO, COURSE, COMMUNITY))


def classify(text: str) -> tuple[str, int, list[str]]:
    """Returns (category, score, reasons)."""
    t = clean(text).lower()
    score, reasons = 0, []
    for points, name, pattern in OFFER_RULES:
        if pattern.search(t):
            score += points
            reasons.append(f"+{points} {name}")
    strong = sum(1 for r in reasons if r.startswith("+3") or r.startswith("+2"))

    if re.search(COMMUNITY, t):
        return "other", score, reasons + ["community post"]
    intro = re.search(SELF_INTRO, t)
    if intro and not re.search(r"\bwe\s*('?\s*re|\s+are)\s+hiring\b", t):
        return "job_seeking", score, reasons + [f"self-introduction: {intro.group(0)!r}"]
    seeking = re.search(SEEKING, t)
    if seeking and score < 6:
        return "job_seeking", score, reasons + [f"seeking: {seeking.group(0)!r}"]
    course = len(re.findall(COURSE, t))
    if course and course * 2 >= strong + 1 and not re.search(r"\b(salary|send\s+(your\s+)?cv)\b|বেতন", t):
        return "course_ad", score, reasons + [f"course words x{course}"]
    if score >= 3 and strong >= 1:
        return "job_offer", score, reasons
    if "?" in t and len(t) < 400:
        return "question", score, reasons + ["short text with question"]
    return "other", score, reasons
