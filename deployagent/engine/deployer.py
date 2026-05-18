"""Orchestrate the full deployment pipeline — ECR push → task def → ECS → CFN."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..aws.cfn import CFNClient
from ..aws.ecr import ECRClient
from ..aws.ecs import ECSClient
from ..errors.classifier import ErrorAction, classify, handle
from ..parser.yaml_loader import DeployConfig
from ..state.store import save_snapshot, update_status

console = Console()


def deploy(config: DeployConfig, config_hash: str) -> None:
    """Execute a full deployment. Raises DeployError on unrecoverable failure."""
    region = config.region
    ecs = ECSClient(region)
    ecr = ECRClient(region)
    cfn = CFNClient(region) if config.cloudformation else None

    deploy_id: int | None = None

    def _rollback() -> None:
        from ..errors.rollback import rollback
        rollback(config.service, steps=1, region=region)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:

            # 1. Build + push image
            t = progress.add_task("Building and pushing Docker image…", total=None)
            image_uri = ecr.build_and_push(
                config.image.repository,
                config.image.tag,
                config.image.dockerfile,
                config.image.build_context,
            )
            progress.update(t, description=f"[green]Image ready:[/] {image_uri}")

            # 2. Register new task definition revision
            t = progress.add_task("Registering ECS task definition…", total=None)
            td_payload = _build_task_definition(config, image_uri)
            td_arn = ecs.register_task_definition(td_payload)
            progress.update(t, description=f"[green]Task def:[/] {td_arn.split('/')[-1]}")

            # 3. Snapshot intent to SQLite *before* any mutations
            deploy_id = save_snapshot(
                service=config.service,
                config_hash=config_hash,
                resource_arns={
                    "ecs": {
                        "cluster": config.cluster,
                        "service_name": config.ecs.service_name,
                    },
                    **(
                        {"cfn": {"stack_name": config.cloudformation.stack_name}}
                        if config.cloudformation
                        else {}
                    ),
                },
                task_definition=td_payload,
                stack_parameters=(
                    config.cloudformation.parameters if config.cloudformation else {}
                ),
                status="pending",
            )

            # 4. Update ECS service
            t = progress.add_task("Updating ECS service…", total=None)
            ecs.update_service(
                config.cluster,
                config.ecs.service_name,
                td_arn,
                config.ecs.desired_count,
            )
            progress.update(t, description="[green]ECS service updated[/]")

            # 5. CloudFormation change set + execute
            if config.cloudformation and cfn:
                t = progress.add_task("Applying CloudFormation changes…", total=None)
                template_body = Path(config.cloudformation.template_file).read_text(encoding="utf-8")
                cs_name = cfn.create_change_set(
                    config.cloudformation.stack_name,
                    template_body,
                    config.cloudformation.parameters,
                )
                cs = cfn.describe_change_set(config.cloudformation.stack_name, cs_name)
                if cs.get("Changes"):
                    cfn.execute_change_set(config.cloudformation.stack_name, cs_name)
                    cfn.wait_complete(config.cloudformation.stack_name)
                    progress.update(t, description="[green]CloudFormation applied[/]")
                else:
                    progress.update(t, description="[dim]CloudFormation — no changes[/]")

            # 6. Wait for ECS stability
            t = progress.add_task("Waiting for ECS service to stabilise…", total=None)
            ecs.wait_stable(config.cluster, config.ecs.service_name)
            progress.update(t, description="[green]Service stable[/]")

        if deploy_id:
            update_status(deploy_id, "success")

        console.print("\n[bold green]Deployment complete![/]\n")

    except Exception as exc:
        if deploy_id:
            update_status(deploy_id, "failed")
        action = classify(exc)
        if action == ErrorAction.ROLLBACK:
            handle(exc, on_rollback=_rollback)
        else:
            handle(exc)


def _build_task_definition(config: DeployConfig, image_uri: str) -> dict:
    """Construct the ECS task definition payload from deploy config."""
    td: dict = {
        "family": config.ecs.task_family,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": str(config.ecs.cpu),
        "memory": str(config.ecs.memory),
        "containerDefinitions": [
            {
                "name": config.ecs.container_name,
                "image": image_uri,
                "essential": True,
                "portMappings": [
                    {"containerPort": config.ecs.container_port, "protocol": "tcp"}
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": f"/ecs/{config.ecs.task_family}",
                        "awslogs-region": config.region,
                        "awslogs-stream-prefix": "ecs",
                    },
                },
            }
        ],
    }
    if config.ecs.execution_role_arn:
        td["executionRoleArn"] = config.ecs.execution_role_arn
    return td
