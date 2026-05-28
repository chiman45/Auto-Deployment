"""Restore a service to its last known-good state from the SQLite snapshot."""
from __future__ import annotations

from rich.console import Console

from ..aws.cfn import CFNClient
from ..aws.ecs import ECSClient
from ..state.store import Snapshot, get_last_good, update_status

console = Console()


def rollback(service: str, steps: int = 1, region: str = "us-east-1") -> None:
    """
    Re-apply the Nth-last successful snapshot for `service`.
    steps=1 → most recent good deploy; steps=2 → one before that, etc.
    """
    snapshot: Snapshot | None = get_last_good(service, steps)
    if snapshot is None:
        console.print(
            f"[bold red]Rollback failed:[/] No successful deployment found "
            f"for service '{service}' (steps={steps})."
        )
        return

    console.print(
        f"\n[bold yellow]Rolling back[/] '{service}' → snapshot "
        f"[cyan]#{snapshot.id}[/] ({snapshot.timestamp[:19]} UTC)"
    )

    ecs = ECSClient(region)
    cfn = CFNClient(region)

    try:
        # Re-register the snapshotted task definition
        td = snapshot.task_definition
        if td:
            console.print("  → Re-registering task definition…")
            new_td_arn = ecs.register_task_definition(td)

            ecs_meta = snapshot.resource_arns.get("ecs", {})
            cluster = ecs_meta.get("cluster", "")
            svc_name = ecs_meta.get("service_name", service)
            desired = td.get("containerDefinitions", [{}])[0].get("desiredCount", 1)

            if cluster and svc_name:
                console.print(f"  → Updating ECS service '{svc_name}' to previous task def…")
                ecs.update_service(cluster, svc_name, new_td_arn, desired)

        # Re-apply the snapshotted CloudFormation parameters
        sp = snapshot.stack_parameters
        cfn_meta = snapshot.resource_arns.get("cfn", {})
        stack_name = cfn_meta.get("stack_name", "")
        if sp and stack_name:
            console.print(f"  → Reverting CloudFormation stack '{stack_name}'…")
            cfn.update_stack(stack_name, sp)
            cfn.wait_complete(stack_name)

        update_status(snapshot.id, "rolled_back")
        console.print("[bold green]Rollback complete.[/]\n")

    except Exception as exc:
        console.print(f"[bold red]Rollback error:[/] {exc}")
        raise
