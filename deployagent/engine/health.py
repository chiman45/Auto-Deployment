"""Post-deploy health checks — ECS service state + CloudWatch error scan."""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.table import Table

from ..aws.cloudwatch import CloudWatchClient
from ..aws.ecs import ECSClient
from ..errors.classifier import HealthCheckFailed
from ..parser.yaml_loader import DeployConfig

console = Console()


def check_health(config: DeployConfig) -> bool:
    """
    Verify that the ECS service is healthy after a deploy.
    Prints a status table, tails recent CloudWatch logs, and raises
    HealthCheckFailed (→ triggers rollback) if anything looks wrong.
    Returns True when healthy.
    """
    ecs = ECSClient(config.region)
    cw = CloudWatchClient(config.region)

    svc = ecs.describe_service(config.cluster, config.ecs.service_name)
    if svc is None:
        console.print(f"[bold red]Service '{config.ecs.service_name}' not found in cluster '{config.cluster}'[/]")
        raise HealthCheckFailed(f"Service '{config.ecs.service_name}' not found")

    running = svc.get("runningCount", 0)
    desired = svc.get("desiredCount", 0)
    pending = svc.get("pendingCount", 0)

    deployments = svc.get("deployments", [])
    rollout_state = deployments[0].get("rolloutState", "UNKNOWN") if deployments else "UNKNOWN"

    log_group = f"/ecs/{config.ecs.task_family}"
    error_lines = cw.get_service_errors(log_group, minutes=5)

    _print_status_table(config, running, desired, pending, rollout_state, error_lines)

    if error_lines:
        console.print("\n[bold red]Recent error logs:[/]")
        for line in error_lines[:10]:
            console.print(f"  [red]{line}[/]")
        console.print()

    healthy = (
        running == desired
        and running > 0
        and rollout_state in ("COMPLETED", "UNKNOWN")
        and not error_lines
    )

    if not healthy:
        raise HealthCheckFailed(
            f"Health check failed: running={running}/{desired}, "
            f"state={rollout_state}, errors={len(error_lines)}"
        )

    return True


def _print_status_table(
    config: DeployConfig,
    running: int,
    desired: int,
    pending: int,
    rollout_state: str,
    error_lines: list[str],
) -> None:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Metric", style="dim", min_width=20)
    table.add_column("Value")

    def _colour(val: str, ok: bool) -> str:
        colour = "green" if ok else "red"
        return f"[{colour}]{val}[/{colour}]"

    table.add_row("Service",        config.ecs.service_name)
    table.add_row("Cluster",        config.cluster)
    table.add_row("Desired tasks",  str(desired))
    table.add_row("Running tasks",  _colour(str(running), running == desired))
    table.add_row("Pending tasks",  str(pending))
    table.add_row(
        "Rollout state",
        _colour(rollout_state, rollout_state in ("COMPLETED", "UNKNOWN")),
    )
    table.add_row(
        "Recent errors",
        _colour(f"{len(error_lines)} found", not error_lines),
    )

    console.print(table)
