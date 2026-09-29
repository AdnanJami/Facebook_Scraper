"""Settings: defaults, overridden by config.yaml, overridden by CLI flags."""
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml


@dataclass
class Config:
    groups: list[str] = field(default_factory=list)
    cookies_file: str = "cookies.json"
    output_dir: str = "output"
    max_posts: int = 50             # per group
    stop_after_known: int = 0       # stop a group after N consecutive already-saved posts (0 = never)
    sort: str = "chronological"     # chronological | recent_activity | top | default
    comments: bool = True
    max_comment_rounds: int = 40    # "View more comments/replies" click rounds per post
    screenshots: bool = True
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
