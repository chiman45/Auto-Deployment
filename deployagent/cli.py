"""deployagent — CLI entry point."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

load_dotenv()  # loads .env from cwd or any parent directory
from rich import box
from rich.console import Console
from rich.table import Table

from .engine.deployer import deploy
from .engine.health import check_health
from .engine.planner import build_plan, print_plan
from .errors.classifier import HealthCheckFailed
from .errors.rollback import rollback as do_rollback
from .parser.yaml_loader import load_config
from .state.store import list_deployments

app = typer.Typer(
    name="deployagent",
    help="CLI auto-deployment agent for AWS ECS / ECR / CloudFormation.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
console = Console()


# ── plan ──────────────────────────────────────────────────────────────────────

@app.command()
def plan(
    config: Path = typer.Argument(..., help="Path to deploy.yaml"),
) -> None:
    """Dry-run: show what would change. Makes no AWS mutations."""
    _require_file(config)

    with console.status("[dim]Querying AWS for current state…[/]"):
        try:
            deploy_plan = build_plan(config)
        except Exception as exc:
            console.print(f"[bold red]Plan failed:[/] {exc}")
            raise typer.Exit(1)

    print_plan(deploy_plan)


# ── apply ─────────────────────────────────────────────────────────────────────

@app.command()
def apply(
    config: Path = typer.Argument(..., help="Path to deploy.yaml"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    skip_validate: bool = typer.Option(False, "--skip-validate", help="Skip Dockerfile/K8s validation"),
) -> None:
    """Validate Dockerfile/K8s files with Gemini, then deploy to AWS."""
    _require_file(config)

    with console.status("[dim]Building plan…[/]"):
        try:
            deploy_plan = build_plan(config)
        except Exception as exc:
            console.print(f"[bold red]Plan failed:[/] {exc}")
            raise typer.Exit(1)

    print_plan(deploy_plan)

    if not deploy_plan.has_changes:
        console.print("[bold green]Nothing to deploy.[/]")
        raise typer.Exit(0)

    if not yes:
        confirmed = typer.confirm("Apply these changes?", default=False)
        if not confirmed:
            console.print("[dim]Aborted.[/]")
            raise typer.Exit(0)

    try:
        deploy(deploy_plan.config, deploy_plan.config_hash, skip_validate=skip_validate)
    except HealthCheckFailed as exc:
        console.print(f"[bold red]Health check failed post-deploy:[/] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[bold red]Deploy failed:[/] {exc}")
        raise typer.Exit(1)

    try:
        check_health(deploy_plan.config)
    except HealthCheckFailed as exc:
        console.print(f"[bold yellow]Warning — health check:[/] {exc}")
        raise typer.Exit(1)


# ── rollback ──────────────────────────────────────────────────────────────────

@app.command()
def rollback(
    config: Path = typer.Argument(..., help="Path to deploy.yaml"),
    steps: int = typer.Option(1, "--steps", help="Number of deployments to roll back"),
) -> None:
    """Revert to a previous known-good deployment snapshot."""
    _require_file(config)

    try:
        cfg, _ = load_config(config)
    except Exception as exc:
        console.print(f"[bold red]Config error:[/] {exc}")
        raise typer.Exit(1)

    do_rollback(cfg.service, steps=steps, region=cfg.region)


# ── status ────────────────────────────────────────────────────────────────────

@app.command()
def status(
    config: Path = typer.Argument(..., help="Path to deploy.yaml"),
    service: Optional[str] = typer.Option(None, "--service", help="Override service name"),
    history: bool = typer.Option(False, "--history", "-H", help="Show deploy history"),
) -> None:
    """Live health check via ECS + CloudWatch. Optionally show deploy history."""
    _require_file(config)

    try:
        cfg, _ = load_config(config)
    except Exception as exc:
        console.print(f"[bold red]Config error:[/] {exc}")
        raise typer.Exit(1)

    if history:
        _print_history(service or cfg.service)
        return

    try:
        check_health(cfg)
        console.print("[bold green]Service is healthy.[/]\n")
    except HealthCheckFailed as exc:
        console.print(f"[bold red]Unhealthy:[/] {exc}\n")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[bold red]Status check error:[/] {exc}\n")
        raise typer.Exit(1)


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_file(path: Path) -> None:
    if not path.exists():
        console.print(f"[bold red]File not found:[/] {path}")
        raise typer.Exit(1)


def _print_history(service: str) -> None:
    snapshots = list_deployments(service=service)
    if not snapshots:
        console.print(f"[dim]No deploy history for '{service}'.[/]")
        return

    _STATUS_COLOUR = {
        "success":     "green",
        "failed":      "red",
        "pending":     "yellow",
        "rolled_back": "magenta",
    }

    table = Table(box=box.SIMPLE, header_style="bold white")
    table.add_column("#",           style="dim", min_width=4)
    table.add_column("Timestamp",                min_width=20)
    table.add_column("Service",                  min_width=14)
    table.add_column("Config Hash",              min_width=18)
    table.add_column("Status",                   min_width=12)

    for snap in snapshots:
        colour = _STATUS_COLOUR.get(snap.status, "white")
        table.add_row(
            str(snap.id),
            snap.timestamp[:19].replace("T", " "),
            snap.service,
            snap.config_hash,
            f"[{colour}]{snap.status}[/{colour}]",
        )

    console.print(f"\n[bold]Deploy history[/] — {service}\n")
    console.print(table)


if __name__ == "__main__":
    app()
