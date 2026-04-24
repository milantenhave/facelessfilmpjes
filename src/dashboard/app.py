"""Minimal Flask dashboard to preview generated videos and analytics."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template_string, send_from_directory

from ..analytics import Analytics
from ..utils import Paths

INDEX_TEMPLATE = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>facelessfilmpjes dashboard</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #0b0b10; color: #f5f5fa; }
    h1 { margin-top: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
    .card { background: #14141d; border-radius: 12px; padding: 1rem; }
    video { width: 100%; border-radius: 8px; }
    code, pre { background: #1c1c28; padding: 2px 4px; border-radius: 4px; }
    a { color: #8fb1ff; }
  </style>
</head>
<body>
  <h1>facelessfilmpjes</h1>
  <p>
    <a href=\"/api/videos\">/api/videos</a> ·
    <a href=\"/api/analytics\">/api/analytics</a>
  </p>
  <div class=\"grid\">
  {% for v in videos %}
    <div class=\"card\">
      <video controls preload=\"metadata\" src=\"/media/{{ v.rel }}\"></video>
      <div><strong>{{ v.name }}</strong></div>
      <div><small>{{ v.size_mb }} MB · {{ v.day }}</small></div>
    </div>
  {% else %}
    <p>No videos yet. Run <code>python -m src run</code>.</p>
  {% endfor %}
  </div>
</body>
</html>
"""


def _iter_videos(root: Path):
    for mp4 in sorted(root.rglob("*.mp4"), reverse=True):
        rel = mp4.relative_to(root)
        size = mp4.stat().st_size / (1024 * 1024)
        yield {
            "name": mp4.name,
            "rel": rel.as_posix(),
            "day": rel.parts[0] if rel.parts else "",
            "size_mb": f"{size:.1f}",
        }


def create_app(cfg: dict) -> Flask:
    paths = Paths.from_config(cfg)
    analytics = Analytics(cfg)
    app = Flask(__name__)

    @app.route("/")
    def index():
        videos = list(_iter_videos(paths.output))
        return render_template_string(INDEX_TEMPLATE, videos=videos)

    @app.route("/media/<path:rel>")
    def media(rel: str):
        return send_from_directory(paths.output, rel)

    @app.route("/api/videos")
    def api_videos():
        return jsonify(list(_iter_videos(paths.output)))

    @app.route("/api/analytics")
    def api_analytics():
        patterns = analytics.winning_patterns()
        return jsonify({"winning_patterns": patterns})

    return app
