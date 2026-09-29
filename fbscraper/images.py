"""Downloads post images (their Facebook URLs expire after a few days) and fingerprints them."""
import json
import logging
import urllib.request
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)


def dhash(path: Path, size: int = 16) -> str:
    """Difference hash: survives re-uploads, resizing and recompression of the same flyer.
    256 bits rather than the usual 64, so two flyers on the same template but with
    different text still come out different."""
    img = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def download_post_images(db, post_id: str, image_urls: list[str], out_dir: Path) -> int:
    """Download any not-yet-downloaded images of a post. Returns how many were saved."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for idx, url in enumerate(image_urls):
        row = db.execute("SELECT local_path FROM post_images WHERE post_id = ? AND idx = ?",
                         (post_id, idx)).fetchone()
        if row and row[0]:
            continue
        path = out_dir / f"{post_id}_{idx}.jpg"
        local, fp = None, None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            path.write_bytes(urllib.request.urlopen(req, timeout=30).read())
            local, fp = str(path), dhash(path)
            saved += 1
        except Exception as e:  # expired link, network error, not an image
            log.debug("Image download failed for %s #%d: %s", post_id, idx, e)
        db.execute(
            "INSERT INTO post_images (post_id, idx, source_url, local_path, dhash) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(post_id, idx) DO UPDATE SET source_url = excluded.source_url, "
            "local_path = excluded.local_path, dhash = excluded.dhash",
            (post_id, idx, url, local, fp),
        )
    db.commit()
    return saved


def download_missing(db, out_dir: Path) -> int:
    """Backfill images for posts scraped before image downloading existed."""
    total = 0
    rows = db.execute(
        "SELECT post_id, image_urls FROM posts p WHERE image_urls != '[]' AND NOT EXISTS "
        "(SELECT 1 FROM post_images i WHERE i.post_id = p.post_id AND i.local_path IS NOT NULL)"
    ).fetchall()
    for post_id, urls in rows:
        total += download_post_images(db, post_id, json.loads(urls), out_dir)
    if rows:
        log.info("Downloaded %d images for %d posts", total, len(rows))
    return total
