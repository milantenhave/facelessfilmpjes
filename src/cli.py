"""Command-line entry point."""
from __future__ import annotations

import json
from pathlib import Path

import click

from .analytics import Analytics
from .pipeline import Pipeline
from .scheduler import Scheduler
from .utils import get_logger, load_config


@click.group()
@click.option("--config", "config_path",
              type=click.Path(dir_okay=False), default=None,
              help="Path to YAML config (defaults to config/config.yaml).")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """facelessfilmpjes - automated faceless short-form video pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = load_config(config_path)


@cli.command("run")
@click.option("-n", "--count", type=int, default=None,
              help="Override videos_per_run for this invocation.")
@click.pass_context
def run_cmd(ctx: click.Context, count: int | None) -> None:
    """Produce videos once and exit."""
    log = get_logger(__name__)
    pipeline = Pipeline(ctx.obj["cfg"])
    results = pipeline.run_once(override_n=count)
    for r in results:
        click.echo(f"✓ {r.video_path}")
    log.info("produced %d videos", len(results))


@cli.command("schedule")
@click.pass_context
def schedule_cmd(ctx: click.Context) -> None:
    """Run on a schedule (see posting_frequency in config)."""
    pipeline = Pipeline(ctx.obj["cfg"])
    Scheduler(ctx.obj["cfg"], pipeline.run_once).run_forever()


@cli.command("ideas")
@click.option("-n", "--count", type=int, default=5)
@click.pass_context
def ideas_cmd(ctx: click.Context, count: int) -> None:
    """Print viral ideas only."""
    pipeline = Pipeline(ctx.obj["cfg"])
    ideas = pipeline.idea_gen.run()
    for idea in ideas[:count]:
        click.echo(json.dumps(idea.to_dict(), ensure_ascii=False))


@cli.command("analytics")
@click.option("--out", type=click.Path(dir_okay=False), default="analytics.json")
@click.pass_context
def analytics_cmd(ctx: click.Context, out: str) -> None:
    """Dump analytics to JSON."""
    Analytics(ctx.obj["cfg"]).dump_summary(Path(out))
    click.echo(f"wrote {out}")


@cli.command("dashboard")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
@click.pass_context
def dashboard_cmd(ctx: click.Context, host: str, port: int) -> None:
    """Start the minimal dashboard."""
    from .dashboard.app import create_app
    app = create_app(ctx.obj["cfg"])
    app.run(host=host, port=port)


if __name__ == "__main__":
    cli(obj={})
