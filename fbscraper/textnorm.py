"""Text normalization shared by classification, extraction and dedupe."""
import hashlib
import re
import unicodedata

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_BANGLA = re.compile(r"[ঀ-৿]")


def clean(s: str | None) -> str:
    """NFKC turns decorative Unicode (𝐔𝐫𝐠𝐞𝐧𝐭, 𝗪𝗘'𝗥𝗘) into plain letters; Bangla digits become ASCII."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).translate(_BN_DIGITS)
    s = re.sub(r"[​-‏⁠﻿]", "", s).replace(" ", " ")  # zero-width / direction marks
    return re.sub(r"[ \t]+", " ", s).strip()


def squash(s: str) -> str:
    """Letters and digits only, lowercased: for exact-duplicate hashing."""
    return re.sub(r"[\W_]+", "", clean(s).lower())


def text_hash(s: str) -> str | None:
    sq = squash(s)
    return hashlib.sha1(sq.encode()).hexdigest() if len(sq) >= 50 else None


def bangla_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    return sum(1 for c in letters if _BANGLA.match(c)) / len(letters) if letters else 0.0


def words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9ঀ-৿]+", clean(s).lower()))


def shingles(s: str, k: int = 5) -> set[str]:
    sq = squash(s)
    return {sq[i:i + k] for i in range(max(len(sq) - k + 1, 0))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0
