"""Command-line entry point."""
from __future__ import annotations

import json
import os
from pathlib import Path

import click

from .analytics import Analytics
from .utils import get_logger, load_config


@click.group()
@click.option("--config", "config_path",
              type=click.Path(dir_okay=False), default=None,
              help="Path to YAML config (defaults to config/config.yaml).")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """facelessfilmpjes — automated faceless short-form video pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = load_config(config_path)


@cli.command("web")
@click.option("--host", default="127.0.0.1", envvar="WEB_HOST")
@click.option("--port", default=8000, type=int, envvar="WEB_PORT")
@click.option("--reload", is_flag=True, default=False,
              help="Enable uvicorn reload (dev only).")
@click.pass_context
def web_cmd(ctx: click.Context, host: str, port: int, reload: bool) -> None:
    """Start the FastAPI web UI + worker (the main production entry point)."""
    import uvicorn
    log = get_logger(__name__)
    log.info("starting web UI on %s:%d", host, port)
    uvicorn.run("src.web.app:create_app", host=host, port=port,
                reload=reload, factory=True)


@cli.command("run")
@click.option("--channel", type=int, required=True,
              help="Channel ID to produce a video for.")
@click.pass_context
def run_cmd(ctx: click.Context, channel: int) -> None:
    """Run one job synchronously for the given channel (useful for debugging)."""
    from .db import init_db
    from .worker.runner import JobRunner
    init_db()
    runner = JobRunner(ctx.obj["cfg"])
    job_id = runner.enqueue(channel_id=channel)
    click.echo(f"job #{job_id} enqueued; running...")
    runner.run(job_id)
    click.echo(f"job #{job_id} finished.")


@cli.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite database + tables."""
    from .db import init_db
    init_db()
    click.echo("database initialised.")


@cli.command("seed")
def seed_cmd() -> None:
    """Seed one example niche so you can create a channel quickly."""
    from .db import Niche, session_scope
    with session_scope() as s:
        existing = s.query(Niche).filter_by(name="self_improvement").one_or_none()
        if existing:
            click.echo("already seeded.")
            return
        niche = Niche(
            name="self_improvement", tone="motivational",
            emotions=["curiosity", "motivation", "urgency"],
            language="en", video_length_seconds=25,
            description="Science-backed self improvement and mindset shifts.",
            prompt_additions=(
                "Cite a research finding or counter-intuitive stat in the body. "
                "Avoid generic hype and cliches."
            ),
        )
        niche.reading_level = "simple"
        s.add(niche)
    click.echo("seeded niche: self_improvement")


@cli.command("ideas")
@click.option("-n", "--count", type=int, default=5)
@click.pass_context
def ideas_cmd(ctx: click.Context, count: int) -> None:
    """Print viral ideas for the first niche in the DB."""
    from .db import Niche, session_scope
    from .idea_generator import IdeaGenerator
    from .llm import build_provider
    with session_scope() as s:
        niche = s.query(Niche).first()
        if not niche:
            click.echo("No niches in DB. Run `seed` or create via web UI.")
            return
        n = {"name": niche.name, "tone": niche.tone,
             "emotions": niche.emotions or [], "weight": 1}
    llm = build_provider(ctx.obj["cfg"])
    cfg = {**ctx.obj["cfg"], "niches": [n], "ideas_per_niche": count}
    ideas = IdeaGenerator(llm, cfg).run()
    for idea in ideas[:count]:
        click.echo(json.dumps(idea.to_dict(), ensure_ascii=False))


@cli.command("analytics")
@click.option("--out", type=click.Path(dir_okay=False), default="analytics.json")
@click.pass_context
def analytics_cmd(ctx: click.Context, out: str) -> None:
    """Dump analytics to JSON."""
    Analytics(ctx.obj["cfg"]).dump_summary(Path(out))
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    cli(obj={})
