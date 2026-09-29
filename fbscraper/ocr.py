"""OCR of downloaded post images with EasyOCR.

English-only by default: adding Bangla makes EasyOCR misread English letters as Bangla
and roughly doubles the time. Bangla is added only for posts whose own text is mostly Bangla.
"""
import logging
from datetime import datetime, timezone

from .textnorm import bangla_ratio

log = logging.getLogger(__name__)

_readers: dict[tuple, object] = {}


def _reader(langs: tuple, gpu: bool):
    if langs not in _readers:
        import easyocr  # slow import; only when OCR actually runs
        log.info("Loading OCR model (%s)...", "+".join(langs))
        _readers[langs] = easyocr.Reader(list(langs), gpu=gpu, verbose=False)
    return _readers[langs]


def ocr_image(path: str, langs: tuple, gpu: bool) -> tuple[str, float]:
    results = _reader(langs, gpu).readtext(path, detail=1, paragraph=False)
    text = " ".join(r[1] for r in results)
    conf = sum(r[2] for r in results) / len(results) if results else 0.0
    return text, conf


def run_ocr(db, gpu: bool = False) -> int:
    """OCR every image not yet processed. Falls back to the post screenshot when the
    image itself could not be downloaded (e.g. its link had already expired)."""
    rows = db.execute(
        "SELECT i.image_id, i.post_id, i.local_path, p.text, p.screenshot FROM post_images i "
        "JOIN posts p USING (post_id) WHERE i.ocr_at IS NULL ORDER BY i.post_id, i.idx"
    ).fetchall()
    screenshot_done = set()
    for n, (image_id, post_id, path, post_text, screenshot) in enumerate(rows, 1):
        source = path
        if not source:
            if not screenshot or post_id in screenshot_done:
                source = None
            else:
                source = screenshot
                screenshot_done.add(post_id)
        langs = ("en", "bn") if bangla_ratio(post_text or "") > 0.3 else ("en",)
        text, conf = ("", 0.0)
        if source:
            try:
                text, conf = ocr_image(source, langs, gpu)
            except Exception as e:
                log.warning("OCR failed for %s: %s", source, e)
        engine = f"easyocr:{'+'.join(langs)}" + (":screenshot" if source and source == screenshot else "")
        db.execute(
            "UPDATE post_images SET ocr_text = ?, ocr_conf = ?, ocr_engine = ?, ocr_at = ? WHERE image_id = ?",
            (text, round(conf, 3), engine, datetime.now(timezone.utc).isoformat(timespec="seconds"), image_id),
        )
        db.commit()
        log.info("OCR %d/%d  post %s  (%d chars, conf %.2f)", n, len(rows), post_id, len(text), conf)
    return len(rows)
