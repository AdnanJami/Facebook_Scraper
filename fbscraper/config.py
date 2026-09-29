"""Settings: defaults, overridden by config.yaml, overridden by CLI flags."""
from dataclasses import dataclass, fields
from pathlib import Path

import yaml


@dataclass
class Config:
    cookies_file: str = "cookies.json"
    output_dir: str = "output"
    max_posts: int = 50             # per group
    stop_after_known: int = 0       # stop a group after N consecutive already-saved posts (0 = never)
    sort: str = "chronological"     # chronological | recent_activity | top | default
    comments: bool = True
    max_comment_rounds: int = 40    # "View more comments/replies" click rounds per post
    screenshots: bool = True
    ocr_gpu: bool = False           # needs a CUDA build of PyTorch in this environment
    judge: str = "groq"             # groq | rules  (who decides job/not-job and extracts fields)
    groq_model: str = "openai/gpt-oss-120b"
    groq_reasoning_effort: str = "low"   # low | medium | high (gpt-oss models only)
    headless: bool = False
    viewport_width: int = 1366
    viewport_height: int = 900
    delay_min: float = 1.0          # random pause between posts (seconds)
    delay_max: float = 2.5

    @property
    def db_path(self) -> Path:
        return Path(self.output_dir) / "scraper.db"

    @property
    def screenshot_dir(self) -> Path:
        return Path(self.output_dir) / "screenshots"

    @property
    def image_dir(self) -> Path:
        return Path(self.output_dir) / "images"


def load_config(path: str | None, overrides: dict) -> Config:
    data = {}
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown config keys in {path}: {', '.join(sorted(unknown))}")
    data.update({k: v for k, v in overrides.items() if v is not None})
    return Config(**data)
