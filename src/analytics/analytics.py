"""SQLite-backed analytics + feedback loop for hooks and scripts."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logger import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    niche TEXT,
    topic TEXT,
    hook TEXT,
    variant TEXT,
    angle TEXT,
    video_path TEXT,
    meta_path TEXT,
    duration REAL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    recorded_at TEXT NOT NULL,
    platform TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    watch_ratio REAL DEFAULT 0
);
"""


@dataclass
class VideoRecord:
    niche: str
    topic: str
    hook: str
    variant: str
    angle: str
    video_path: str
    meta_path: str
    duration: float
    id: int | None = None
    extras: dict = field(default_factory=dict)


class Analytics:
    def __init__(self, cfg: dict) -> None:
        db_path = cfg.get("analytics", {}).get("db_path", "./analytics.db")
        self.db = Path(db_path)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.feedback_loop = bool(cfg.get("analytics", {}).get("feedback_loop", True))
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def record_video(self, rec: VideoRecord) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO videos(created_at, niche, topic, hook, variant,
                                      angle, video_path, meta_path, duration)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    rec.niche, rec.topic, rec.hook, rec.variant,
                    rec.angle, rec.video_path, rec.meta_path, rec.duration,
                ),
            )
            return int(cur.lastrowid)

    def record_metrics(self, video_id: int, platform: str,
                       views: int = 0, likes: int = 0, comments: int = 0,
                       shares: int = 0, watch_ratio: float = 0.0) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO metrics(video_id, recorded_at, platform,
                                       views, likes, comments, shares, watch_ratio)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    video_id, datetime.now(timezone.utc).isoformat(), platform,
                    views, likes, comments, shares, watch_ratio,
                ),
            )

    # -- feedback loop --------------------------------------------------
    def winning_patterns(self, limit: int = 10) -> list[str]:
        """Return short human-readable patterns from the best videos.

        We combine the latest metrics per video and rank by a simple
        views * (1 + like_rate) * watch_ratio score, then surface hooks and
        angles that correlate with the top bucket.
        """
        if not self.feedback_loop:
            return []
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT v.hook, v.angle, v.niche, v.variant,
                       COALESCE(MAX(m.views), 0)        AS views,
                       COALESCE(MAX(m.likes), 0)        AS likes,
                       COALESCE(MAX(m.watch_ratio), 0)  AS watch
                FROM videos v
                LEFT JOIN metrics m ON m.video_id = v.id
                GROUP BY v.id
                HAVING views > 0
                ORDER BY (views * (1.0 + (likes * 1.0 / MAX(views, 1))) *
                          (0.2 + watch)) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        patterns: list[str] = []
        for r in rows:
            patterns.append(
                f"[{r['niche']}/{r['angle']}] {r['hook']} "
                f"(views={r['views']}, watch={r['watch']:.0%})"
            )
        return patterns

    def dump_summary(self, out_path: Path) -> None:
        with self._conn() as c:
            videos = [dict(r) for r in c.execute("SELECT * FROM videos").fetchall()]
            metrics = [dict(r) for r in c.execute("SELECT * FROM metrics").fetchall()]
        out_path.write_text(
            json.dumps({"videos": videos, "metrics": metrics},
                       ensure_ascii=False, indent=2),
            "utf-8",
        )
