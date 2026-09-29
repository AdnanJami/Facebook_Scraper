"""Rule-based extraction of job fields from post text + OCR text.

Every extractor is best-effort: it returns None rather than guessing when nothing matches.
"""
import re
from datetime import date, datetime

from .textnorm import clean

FREE_MAIL = {"gmail", "yahoo", "hotmail", "outlook", "live", "icloud", "proton", "protonmail", "ymail"}

# OCR often drops the dot: "name@gmail com"
_EMAIL = re.compile(r"\b([a-z0-9][\w.+-]*)\s?@\s?([a-z0-9-]+(?:\.[a-z0-9-]+)*)(?:\s?[.,]\s?|\s)(com|net|org|io|co|bd|edu|info|biz|ai|dev|tech)(\.bd)?\b", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?88[\s-]?)?(01[3-9]\d{2})[\s-]?(\d{3})[\s-]?(\d{3})(?!\d)")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+|\b(?:forms\.gle|bit\.ly|lnkd\.in|tinyurl\.com|rb\.gy)/[\w-]+", re.I)
_APPLY_URL_HINT = re.compile(r"forms\.gle|docs\.google\.com/forms|bit\.ly|lnkd\.in|career|jobs?\b|apply|recruit|bdjobs|linkedin\.com/jobs", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_BN_MONTHS = {"জানুয়ারি": 1, "ফেব্রুয়ারি": 2, "মার্চ": 3, "এপ্রিল": 4, "মে": 5, "জুন": 6, "জুলাই": 7,
              "আগস্ট": 8, "সেপ্টেম্বর": 9, "অক্টোবর": 10, "নভেম্বর": 11, "ডিসেম্বর": 12}
_DEADLINE_KEY = re.compile(r"deadline|last\s+date|apply\s+(before|by|within)|closing\s+date|end\s*date|"
                           r"application\s+ends|শেষ\s*তারিখ|আবেদনের\s*শেষ", re.I)

ROLE_WORDS = (r"developer|engineer|executive|manager|intern|officer|designer|specialist|assistant|instructor|"
              r"teacher|trainee|associate|analyst|lead|marketer|writer|accountant|consultant|coordinator|"
              r"administrator|architect|programmer|tester|sqa|qa|devops|representative|supervisor|"
              r"head|director|operator|technician|agent|scientist|editor|researcher|advisor|counselor|"
              r"receptionist|cashier|driver|guard|staff|trainer|tutor|buyer|merchandiser|caller|setter|owner|"
              r"photographer|videographer|nurse|chef|visualizer|animator|strategist|recruiter|ভাইজার|অফিসার|এক্সিকিউটিভ")
_ADJECTIVES = re.compile(r"(?i)^((experienced|motivated|skilled|talented|passionate|dedicated|detail[\s-]oriented|driven|"
                         r"ambitious|dynamic|energetic|highly|and|a|an|the|confident|creative|curious|proactive|"
                         r"self[\s-]motivated|results[\s-]driven|smart|hard[\s-]?working|enthusiastic|qualified|"
                         r"competent|reliable|technically|capable|dependable|organized|communication[\s-]focused)[\s,]+)+")
_HEADLINE = re.compile(r"(?i)\bhiring\s*(?:for\s+)?[:!|–—-]+\s*([^\n]{3,90})|\bhiring\s+for\s+([^\n]{3,90})")
# End of the role part of a headline: a new sentence ("A leading...", "We are...") or a full stop
# that isn't an abbreviation like "Sr." / "Jr.".
_HEADLINE_END = re.compile(r"\s(?=(?:A|An|We|Our|Join|At|Is|Are|Location|Company|Please|Who|If)\b)|(?<![A-Z][a-z])(?<![A-Z])[.!](?:\s|$)")
_NOT_A_TITLE = re.compile(r"(?i)^(apply|deadline|send|email|location|salary|join|we\b|we’re|we're|our|urgent|about|"
                          r"please|interested|click|dm|inbox|contact)")
_TITLE_TAIL = re.compile(r"(?i)\s+(?:and\s+(?:build|gain|help|grow|work|be|get)|to\s+(?:join|build|help|work|lead|drive)|"
                         r"based\s+(?:in|at)|who\s)\b.*$|\s+(?:company|location|salary|job\s+type|deadline)$")
_TITLE_AS = re.compile(r"(?i)\bas\s+an?\s+([^\n.,;!]{3,60}?\b(?:" + ROLE_WORDS + r")s?\b(?:\s+interns?)?)")
_TITLE_LABEL = re.compile(r"(?im)^\W*(?:[\w-]+\s+){0,2}?(?:position(?:\s*title)?|job\s*title|post|designation|role|opening|opportunity|পদের\s*নাম|পদ|পোস্ট)"
                          r"\s*(?:name)?\s*[:：\-–—]\s*(.{3,90})$")
_TITLE_HIRING = re.compile(r"(?i)\b(?:hiring|looking\s+for|seeking|we\s+need|urgently\s+need)\s*[:!\-–—|]*\s*"
                           r"(?:an?\s+|the\s+|some\s+)?(?:\d+\s+)?([^\n.!,;|]{3,80}?(?:" + ROLE_WORDS + r")s?\b[^\n.!,;|]{0,40}?)"
                           r"(?=\s*(?:\n|$|[.!,;|(]|\s+(?:at|for|to|in|with|who|position|role)\b))")
_TITLE_LINE = re.compile(r"(?im)^\W*([^\n]{0,60}\b(?:" + ROLE_WORDS + r")s?\b[^\n]{0,40})$")
_COMPANY_LABEL = re.compile(r"(?im)^\W*(?:company(?:\s*name)?|organi[sz]ation|employer|প্রতিষ্ঠান(?:ের\s*নাম)?)\s*[:：\-–—]\s*(.{2,80})$")
_COMPANY_HIRING = re.compile(r"(?m)^\W*([A-Z][\w&.,'\- ]{1,60}?)\s+(?:is|are)\s+(?:now\s+|urgently\s+|actively\s+)?"
                             r"(?:hiring|looking\s+for|seeking|offering)\b")
_COMPANY_AT = re.compile(r"(?i:hiring|join(?:ing)?)\b[^\n.]{0,80}?\bat\s+((?:[A-Z][\w&\-]*(?:\.(?:com|io|ai|net))?[ \t]*){1,4})")
_COMPANY_SUFFIX = re.compile(r"\b((?:(?:[A-Z][\w&.\-]*|&)[ \t]+){1,4}(?:Ltd\.?|Limited|PLC|Inc\.?|LLC|Technologies|"
                             r"Solutions|Group|Corporation|Consultancy|Consulting|Agency|Labs?))(?![\w])")
_COMPANY_JOIN = re.compile(r"\b[Jj]oin\s+(?!us\b|our\b|the\b|a\b|an\b)((?:[A-Z][\w&\-]*(?:\.(?:com|io|ai|net))?[ \t]*){1,4})")
_COMPANY_ABOUT = re.compile(r"(?m)^\W*[Aa]bout\s+(?:us\s*[:\-–]\s*)?([A-Z][\w&.\- ]{2,50})")
_LOCATION_LABEL = re.compile(r"(?im)^\W*(?:job\s*)?(?:location|address|office|work\s*place|workplace|কর্মস্থল|লোকেশন|ঠিকানা)"
                             r"\s*[:：\-–—]\s*(.{2,120})$")
PLACES = ["Gulshan", "Banani", "Baridhara", "Bashundhara", "Dhanmondi", "Mirpur", "Uttara", "Mohakhali", "Motijheel",
          "Badda", "Tejgaon", "Farmgate", "Mohammadpur", "Niketon", "Panthapath", "Karwan Bazar", "Kawran Bazar",
          "Rampura", "Khilgaon", "Malibagh", "Shyamoli", "Savar", "Gazipur", "Narayanganj", "Chattogram", "Chittagong",
          "Sylhet", "Khulna", "Rajshahi", "Barishal", "Rangpur", "Mymensingh", "Cumilla", "Comilla", "Dhaka"]
SKILLS = ["python", "java", "javascript", "typescript", "react", "next.js", "vue", "angular", "node.js", "nodejs",
          "express", "laravel", "php", "django", "flask", "fastapi", "spring", "flutter", "dart", "kotlin", "swift",
          "android", "ios", "react native", "c#", ".net", "asp.net", "c++", "golang", "rust", "sql", "mysql",
          "postgresql", "mongodb", "redis", "aws", "azure", "gcp", "docker", "kubernetes", "linux", "git",
          "html", "css", "tailwind", "wordpress", "shopify", "figma", "photoshop", "illustrator", "seo",
          "machine learning", "deep learning", "nlp", "llm", "pytorch", "tensorflow", "power bi", "tableau",
          "excel", "erp", "sap", "tally", "autocad", "sketchup", "selenium", "jira", "graphql", "rest api"]


_HIRING_PREFIX = re.compile(r"(?i)^\W*((we\s*'?\s*re|we\s+are|now|urgent(ly)?|fast-track)\s+)?hiring\b\W*")
_NEXT_LABEL = re.compile(r"\s+(?=(?:[A-Z][\w/&-]*\s){0,2}[A-Z][\w/&-]*\s?:)")  # "... Dhaka Job Type: Full-Time"
_GENERIC_COMPANY = re.compile(r"(?i)^(an?\s|the\s)|\b(leading|reputed|well[\s-]known|renowned|established|"
                              r"diversified|fast[\s-]growing|our\s+client|confidential)\b")
_BAD_URL = re.compile(r"(?i)maps\.app\.goo\.gl|goo\.gl/maps|google\.[a-z.]+/maps|youtu|instagram\.com|"
                      r"tiktok\.com|linkedin\.com/company|wa\.me|whatsapp")


def _first(pattern, text, group=1):
    m = pattern.search(text)
    return _tidy(m.group(group)) if m else None


def _tidy(s: str | None) -> str | None:
    if not s:
        return None
    s = _NEXT_LABEL.split(s, maxsplit=1)[0]
    # "WE'RE HIRING | Jr. Visualizer | Dhaka" -> first part that isn't just the hiring phrase
    parts = [p.strip() for p in s.split("|") if p.strip() and not _HIRING_PREFIX.fullmatch(p.strip())]
    s = parts[0] if parts else ""
    s = re.sub(r"[\s*_#•★✔✅📍💼🔹🔸➡️👉]+", " ", s).strip(" :-–—,;!").rstrip(".").strip()  # keep ".NET"
    # Trim a stray bracket at either end, then close any bracket left open: "Jr. Visualizer (Intern)"
    if s.endswith(")") and s.count(")") > s.count("("):
        s = s[:-1].rstrip()
    if s.startswith("(") and s.count("(") > s.count(")"):
        s = s[1:].lstrip()
    if s.count("(") > s.count(")"):
        s += ")"
    return s[:120] or None


def emails(text: str) -> list[str]:
    out = []
    for user, domain, tld, bd in _EMAIL.findall(text):
        e = f"{user}@{domain}.{tld}{bd}".lower()
        if e not in out:
            out.append(e)
    return out


def phones(text: str) -> list[str]:
    return list(dict.fromkeys("".join(m) for m in _PHONE.findall(text)))


def urls(text: str) -> list[str]:
    out = []
    for u in _URL.findall(text):
        u = u.rstrip(".,;:!")
        if "facebook.com" in u or "fb.com" in u or "fbcdn" in u:
            continue
        if u not in out:
            out.append(u)
    return out


def _parse_date(s: str, ref: date) -> date | None:
    s = s.lower()
    for bn, num in _BN_MONTHS.items():
        s = s.replace(bn, f" {num}/ ")
    candidates = []
    # 15.10.2026 / 15/10/26 / 15-10-2026
    for d, m, y in re.findall(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})", s):
        candidates.append((int(d), int(m), int(y)))
    # 5 October 2026 / 5th Oct, 2026 / 5 Oct
    for d, mon, y in re.findall(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s*(\d{4})?", s):
        if mon[:3] in _MONTHS:
            candidates.append((int(d), _MONTHS[mon[:3]], int(y) if y else 0))
    # October 5, 2026 / Oct 5
    for mon, d, y in re.findall(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", s):
        if mon[:3] in _MONTHS:
            candidates.append((int(d), _MONTHS[mon[:3]], int(y) if y else 0))
    # Bangla month converted above: "5 10/ 2026"
    for d, m, y in re.findall(r"(\d{1,2})\s+(\d{1,2})/\s*,?\s*(\d{4})?", s):
        candidates.append((int(d), int(m), int(y) if y else 0))
    for d, m, y in candidates:
        if y and y < 100:
            y += 2000
        if not y:  # no year written: the next occurrence on/after the posting date
            y = ref.year if (m, d) >= (ref.month, ref.day) else ref.year + 1
        try:
            return date(y, m, d)
        except ValueError:
            continue
    return None


def deadline(text: str, posted: date) -> str | None:
    for m in _DEADLINE_KEY.finditer(text):
        found = _parse_date(text[m.end():m.end() + 60], posted)
        if found and found >= posted.replace(year=posted.year - 1):
            return found.isoformat()
    return None


def _amount(num: str, unit: str) -> int | None:
    try:
        n = float(num.replace(",", ""))
    except ValueError:
        return None
    unit = (unit or "").lower()
    if unit.startswith("k"):
        n *= 1_000
    elif unit.startswith("lakh") or unit.startswith("lac") or unit == "l":
        n *= 100_000
    return int(n)


def salary(text: str) -> dict:
    m = re.search(r"(?i)(salary|remuneration|compensation|pay\s*scale|বেতন|স্যালারি)[^\n]{0,80}", text)
    if not m:
        return {}
    chunk = m.group(0)
    out = {"salary_text": _tidy(re.sub(r"(?i)^(salary|remuneration|compensation|pay\s*scale|বেতন|স্যালারি)\s*(range)?\s*[:：\-–]?", "", chunk))}
    if re.search(r"(?i)negotiable|আলোচনা", chunk):
        out["salary_text"] = out["salary_text"] or "Negotiable"
    nums = re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b|lakh|lac|l\b)?", chunk, re.I)
    # "2.16 – 2.40 lakh": a unit written once applies to the small unit-less numbers of the range
    shared = next((u for _, u in nums if u), "")
    nums = [(n, u or (shared if float(n.replace(",", "") or 0) < 1000 else "")) for n, u in nums]
    values = [v for v in (_amount(n, u) for n, u in nums) if v and v >= 1000]
    if values:
        out["salary_min"], out["salary_max"] = min(values[:2]), max(values[:2])
        out["salary_currency"] = "BDT" if re.search(r"(?i)bdt|tk|taka|৳|টাকা|/-", chunk) or values[0] < 1_000_000 else None
    return out


def _title_ok(t: str | None) -> str | None:
    if not t:
        return None
    t = _HIRING_PREFIX.sub("", t)
    t = re.sub(r"(?i)^.{0,60}?\b(?:is|are)\s+(?:now\s+)?(?:hiring|offering|looking\s+for|seeking)\s+(?:an?\s+|the\s+)?", "", t)
    t = re.sub(r"(?i)^.{0,60}?\bjoin\s+(?:us|our\s+team)\s+as\s+(?:an?\s+)?", "", t)
    t = _tidy(_TITLE_TAIL.sub("", _ADJECTIVES.sub("", t.strip(" |"))))
    if not t or _NOT_A_TITLE.match(t) or not re.search(r"[A-Za-zঀ-৿]{2}", t):
        return None
    return t


def _headline(text: str) -> str | None:
    """"WE'RE HIRING | MANAGER – AI & DIGITAL TRANSFORMATION A leading group is..." -> the role part.
    Only trusted if it names a role or is an all-caps heading (otherwise it's usually a sentence)."""
    m = _HEADLINE.search(text)
    if not m:
        return None
    t = _title_ok(_HEADLINE_END.split(m.group(1) or m.group(2), maxsplit=1)[0])
    if t and (re.search(rf"(?i)\b(?:{ROLE_WORDS})s?\b", t) or (t.isupper() and len(t.split()) >= 2)):
        return t
    return None


def title(text: str) -> str | None:
    t = (_title_ok(_first(_TITLE_LABEL, text)) or _headline(text)
         or _title_ok(_first(_TITLE_HIRING, text)) or _title_ok(_first(_TITLE_AS, text)))
    if t:
        return t
    # "OPEN POSITIONS:" followed by a list
    m = re.search(r"(?i)open\s+positions?\s*[:：]?\s*\n?((?:\W*[^\n]{3,60}\n?){1,4})", text)
    if m:
        items = [_tidy(x) for x in re.split(r"\n|•", m.group(1)) if re.search(ROLE_WORDS, x, re.I)]
        if items:
            return "; ".join(dict.fromkeys(i for i in items if i))
    return _title_ok(_first(_TITLE_LINE, text))


def company(text: str, email_list: list[str]) -> str | None:
    for pat in (_COMPANY_LABEL, _COMPANY_HIRING, _COMPANY_AT, _COMPANY_JOIN, _COMPANY_SUFFIX, _COMPANY_ABOUT):
        c = _first(pat, text)
        if c:
            c = re.sub(r"(?i)^(at|join|with|in)\s+", "", c)
        if c and not _GENERIC_COMPANY.search(c) and c.split(",")[0] not in PLACES and not re.fullmatch(
            r"(?i)(we|our\s+team|us|the\s+team|now|urgent(ly)?|dhaka|bangladesh|e-?commerce)", c
        ):
            return c
    for e in email_list:  # hr@acme.com -> acme
        domain = e.split("@")[1].split(".")[0]
        if domain not in FREE_MAIL:
            return domain.capitalize()
    return None


def location(text: str) -> str | None:
    loc = _first(_LOCATION_LABEL, text)
    if loc and not re.fullmatch(r"(?i)(on[\s-]?site|remote|hybrid|work\s+(at|from)\s+(office|home)|office|wfh)", loc):
        return loc
    found = [p for p in PLACES if re.search(rf"\b{p}\b", text, re.I)]
    return ", ".join(found[:2]) if found else None


def _experience(text: str) -> str | None:
    for pat in (r"(?i)experience\s*(?:required)?\s*[:：\-–]\s*([^\n]{2,40})",
                r"(?i)(\d+(?:\.\d+)?\s*(?:\+|\s*(?:-|–|to)\s*\d+(?:\.\d+)?)?\s*\+?\s*(?:years?|yrs?)(?:\s+of)?"
                r"(?:\s+(?:relevant|professional|hands-on|proven|industry|work))?(?:\s+experience)?)"):
        for m in re.finditer(pat, text):
            first = re.search(r"\d+", m.group(1))
            if first and int(first.group()) > 20:  # "29 years of experience" is about the company, not the role
                continue
            if not first and not re.search(r"(?i)fresher|month|year|বছর|মাস", m.group(1)):
                continue
            return _tidy(re.sub(r"(?i)\s+of$", "", m.group(1).strip()))
    return None


def extract(text: str, posted_at: str | None) -> dict:
    text = re.sub(r"\*+|#+|__+", " ", clean(text))  # markdown emphasis from copy-pasted posts
    text = re.sub(r"[ \t]+", " ", text)
    low = text.lower()
    try:
        posted = datetime.fromisoformat(posted_at).date() if posted_at else date.today()
    except ValueError:
        posted = date.today()
    email_list, phone_list, url_list = emails(text), phones(text), urls(text)
    url_list = [u for u in url_list if not _BAD_URL.search(u)]
    apply_urls = [u for u in url_list if _APPLY_URL_HINT.search(u)] or url_list
    exp = _experience(text)
    edu = re.search(r"(?im)^[^\n]{0,40}\b(b\.?\s?sc|m\.?\s?sc|bachelor|master|mba|bba|bbs|phd|diploma|hsc|ssc|graduat)[^\n]{0,80}", text)
    data = {
        "title": title(text),
        "company": company(text, email_list),
        "location": location(text),
        "work_mode": ("remote" if re.search(r"\bremote\b|work\s+from\s+home|\bwfh\b", low)
                      else "hybrid" if "hybrid" in low
                      else "onsite" if re.search(r"on[\s-]?site|in[\s-]office", low) else None),
        "employment_type": ("internship" if re.search(r"\bintern(ship)?\b", low)
                            else "part_time" if re.search(r"part[\s-]?time", low)
                            else "contract" if re.search(r"\bcontract(ual)?\b", low)
                            else "freelance" if "freelanc" in low
                            else "full_time" if re.search(r"full[\s-]?time", low) else None),
        "experience": exp or ("Fresher" if re.search(r"\bfreshers?\b", low) else None),
        "education": _tidy(edu.group(0)) if edu else None,
        "skills": [s for s in SKILLS if re.search(rf"(?<![\w.+#]){re.escape(s)}(?![\w+#])", low)],
        "deadline": deadline(text, posted),
        "apply_email": email_list[0] if email_list else None,
        "apply_phone": phone_list[0] if phone_list else None,
        "apply_url": apply_urls[0] if apply_urls else None,
        "emails": email_list,
        "phones": phone_list,
    }
    data.update(salary(text))
    return data
