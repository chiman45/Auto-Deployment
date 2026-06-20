"""
Deployment planner — compares desired config against current AWS state
and produces a colour-coded change plan without making any mutations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from ..aws.alb import ALBClient
from ..aws.cfn import CFNClient
from ..aws.ecs import ECSClient
from ..parser.yaml_loader import DeployConfig, load_config

console = Console()


@dataclass
class Change:
    resource: str
    resource_type: str
    action: str       # "CREATE" | "UPDATE" | "NO_CHANGE" | "REMOVE"
    current: str = ""
    desired: str = ""


@dataclass
class Plan:
    config: DeployConfig
    config_hash: str
    changes: list[Change] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(c.action != "NO_CHANGE" for c in self.changes)


def build_plan(config_path: Path) -> Plan:
    """
    Query current AWS state (read-only) and diff against the desired config.
    Raises on config parse errors or unexpected AWS failures.
    """
    config, config_hash = load_config(config_path)
    ecs = ECSClient(config.region)

    plan = Plan(config=config, config_hash=config_hash)

    _plan_ecs(plan, config, ecs)
    _plan_alb(plan, config)
    _plan_cfn(plan, config)

    return plan


# ── ECS diff ──────────────────────────────────────────────────────────────────

def _plan_ecs(plan: Plan, config: DeployConfig, ecs: ECSClient) -> None:
    desired_image = f"{config.image.repository}:{config.image.tag}"

    # Task definition
    current_td = ecs.describe_task_definition(config.ecs.task_family)
    if current_td is None:
        plan.changes.append(Change(
            resource=config.ecs.task_family,
            resource_type="Task Definition",
            action="CREATE",
            current="(none)",
            desired=f"cpu={config.ecs.cpu}, mem={config.ecs.memory}, image={desired_image}",
        ))
    else:
        current_image = ""
        containers = current_td.get("containerDefinitions", [])
        if containers:
            current_image = containers[0].get("image", "")
        current_cpu = current_td.get("cpu", "?")
        current_mem = current_td.get("memory", "?")

        if (
            current_image != desired_image
            or str(current_cpu) != str(config.ecs.cpu)
            or str(current_mem) != str(config.ecs.memory)
        ):
            plan.changes.append(Change(
                resource=config.ecs.task_family,
                resource_type="Task Definition",
                action="UPDATE",
                current=f"image={current_image}, cpu={current_cpu}, mem={current_mem}",
                desired=f"image={desired_image}, cpu={config.ecs.cpu}, mem={config.ecs.memory}",
            ))
        else:
            plan.changes.append(Change(
                resource=config.ecs.task_family,
                resource_type="Task Definition",
                action="NO_CHANGE",
                current=f"revision={current_td.get('revision', '?')}",
                desired="(same)",
            ))

    # ECS service
    current_svc = ecs.describe_service(config.cluster, config.ecs.service_name)
    if current_svc is None:
        plan.changes.append(Change(
            resource=config.ecs.service_name,
            resource_type="ECS Service",
            action="CREATE",
            current="(none)",
            desired=f"cluster={config.cluster}, count={config.ecs.desired_count}",
        ))
    else:
        current_count = current_svc.get("desiredCount", 0)
        current_td_arn = current_svc.get("taskDefinition", "")
        current_svc_image = ""
        if current_td_arn:
            svc_td = ecs.describe_task_definition(current_td_arn)
            if svc_td:
                containers = svc_td.get("containerDefinitions", [])
                current_svc_image = containers[0].get("image", "") if containers else ""

        if current_svc_image != desired_image or current_count != config.ecs.desired_count:
            plan.changes.append(Change(
                resource=config.ecs.service_name,
                resource_type="ECS Service",
                action="UPDATE",
                current=f"image={current_svc_image}, count={current_count}",
                desired=f"image={desired_image}, count={config.ecs.desired_count}",
            ))
        else:
            plan.changes.append(Change(
                resource=config.ecs.service_name,
                resource_type="ECS Service",
                action="NO_CHANGE",
                current=f"count={current_count}",
                desired=f"count={config.ecs.desired_count}",
            ))


# ── ALB diff ──────────────────────────────────────────────────────────────────

def _plan_alb(plan: Plan, config) -> None:
    if not config.alb:
        return

    client = ALBClient(config.region)
    alb = client.find_alb(config.alb.name)
    tg  = client.find_target_group(config.alb.target_group_name)

    if alb is None:
        plan.changes.append(Change(
            resource=config.alb.name,
            resource_type="ALB",
            action="CREATE",
            current="(none)",
            desired=f"internet-facing, port={config.alb.listener_port}",
        ))
    else:
        dns = alb.get("DNSName", "?")
        plan.changes.append(Change(
            resource=config.alb.name,
            resource_type="ALB",
            action="NO_CHANGE",
            current=f"dns={dns}",
            desired="(same)",
        ))

    if tg is None:
        plan.changes.append(Change(
            resource=config.alb.target_group_name,
            resource_type="Target Group",
            action="CREATE",
            current="(none)",
            desired=f"port={config.ecs.container_port}, type=ip",
        ))
    else:
        plan.changes.append(Change(
            resource=config.alb.target_group_name,
            resource_type="Target Group",
            action="NO_CHANGE",
            current="exists",
            desired="(same)",
        ))


# ── CloudFormation diff ────────────────────────────────────────────────────────

def _plan_cfn(plan: Plan, config: DeployConfig) -> None:
    if not config.cloudformation:
        return

    cfn = CFNClient(config.region)
    current_stack = cfn.describe_stack(config.cloudformation.stack_name)

    if current_stack is None:
        plan.changes.append(Change(
            resource=config.cloudformation.stack_name,
            resource_type="CFN Stack",
            action="CREATE",
            current="(none)",
            desired=f"template={config.cloudformation.template_file}",
        ))
        return

    current_params = {
        p["ParameterKey"]: p.get("ParameterValue", "")
        for p in current_stack.get("Parameters", [])
    }
    desired_params = config.cloudformation.parameters
    changed_keys = [k for k in desired_params if desired_params.get(k) != current_params.get(k)]
    added_keys = [k for k in desired_params if k not in current_params]
    removed_keys = [k for k in current_params if k not in desired_params]

    if changed_keys or added_keys or removed_keys:
        summary = ", ".join(
            [f"+{k}" for k in added_keys]
            + [f"~{k}" for k in changed_keys]
            + [f"-{k}" for k in removed_keys]
        )
        plan.changes.append(Change(
            resource=config.cloudformation.stack_name,
            resource_type="CFN Stack",
            action="UPDATE",
            current=f"params: {list(current_params.keys())}",
            desired=f"changes: {summary}",
        ))
    else:
        plan.changes.append(Change(
            resource=config.cloudformation.stack_name,
            resource_type="CFN Stack",
            action="NO_CHANGE",
            current="in sync",
            desired="in sync",
        ))


# ── Rich output ────────────────────────────────────────────────────────────────

_ACTION_STYLE: dict[str, tuple[str, str]] = {
    "CREATE":    ("green",  "+ create"),
    "UPDATE":    ("yellow", "~ update"),
    "REMOVE":    ("red",    "- remove"),
    "NO_CHANGE": ("dim",    "  no-op "),
}


def print_plan(plan: Plan) -> None:
    console.print(
        f"\n[bold]Deploy Plan[/]  "
        f"service=[cyan]{plan.config.service}[/]  "
        f"region=[cyan]{plan.config.region}[/]  "
        f"cluster=[cyan]{plan.config.cluster}[/]\n"
    )

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold white")
    table.add_column("Resource",      style="white",   min_width=22)
    table.add_column("Type",          style="dim",     min_width=16)
    table.add_column("Action",                         min_width=10)
    table.add_column("Current",       style="dim",     min_width=30)
    table.add_column("Desired",                        min_width=30)

    for change in plan.changes:
        colour, label = _ACTION_STYLE.get(change.action, ("white", change.action))
        table.add_row(
            change.resource,
            change.resource_type,
            f"[{colour}]{label}[/{colour}]",
            change.current,
            change.desired,
        )

    console.print(table)

    creates  = sum(1 for c in plan.changes if c.action == "CREATE")
    updates  = sum(1 for c in plan.changes if c.action == "UPDATE")
    removes  = sum(1 for c in plan.changes if c.action == "REMOVE")
    no_ops   = sum(1 for c in plan.changes if c.action == "NO_CHANGE")

    console.print(
        f"\nPlan: [green]{creates} to create[/], "
        f"[yellow]{updates} to update[/], "
        f"[red]{removes} to destroy[/], "
        f"[dim]{no_ops} unchanged[/]\n"
    )

    if not plan.has_changes:
        console.print("[bold green]Infrastructure is up to date — nothing to deploy.[/]\n")
