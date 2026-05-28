"""boto3 wrappers for Amazon ECS."""
from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from ..errors.retry import aws_retry


class ECSClient:
    def __init__(self, region: str):
        self._client = boto3.client("ecs", region_name=region)

    @aws_retry
    def describe_service(self, cluster: str, service_name: str) -> dict | None:
        """Return the current ECS service dict, or None if it does not exist."""
        try:
            resp = self._client.describe_services(cluster=cluster, services=[service_name])
            svcs = resp.get("services", [])
            if svcs and svcs[0]["status"] != "INACTIVE":
                return svcs[0]
            return None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("ClusterNotFoundException", "ResourceNotFoundException"):
                return None
            raise

    @aws_retry
    def describe_task_definition(self, family_or_arn: str) -> dict | None:
        """Return the current task definition dict, or None if not found."""
        try:
            resp = self._client.describe_task_definition(taskDefinition=family_or_arn)
            return resp["taskDefinition"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise

    @aws_retry
    def register_task_definition(self, td: dict) -> str:
        """Register a new revision of a task definition. Returns the new ARN."""
        _keep = {
            "family", "taskRoleArn", "executionRoleArn", "networkMode",
            "containerDefinitions", "volumes", "cpu", "memory",
            "requiresCompatibilities", "tags",
        }
        payload = {k: v for k, v in td.items() if k in _keep}
        resp = self._client.register_task_definition(**payload)
        return resp["taskDefinition"]["taskDefinitionArn"]

    @aws_retry
    def create_service(
        self,
        cluster: str,
        service_name: str,
        task_def_arn: str,
        desired_count: int,
    ) -> dict:
        return self._client.create_service(
            cluster=cluster,
            serviceName=service_name,
            taskDefinition=task_def_arn,
            desiredCount=desired_count,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "assignPublicIp": "ENABLED",
                    "subnets": [
                            "subnet-08cf6725be89eb536",
                            "subnet-0c1c42c0f03a1e851",
                        ],
                    "securityGroups": ["sg-085d20aa23c9ff9a9"],
                }
            },
        )

    @aws_retry
    def update_service(
        self,
        cluster: str,
        service_name: str,
        task_def_arn: str,
        desired_count: int,
    ) -> dict:
        return self._client.update_service(
            cluster=cluster,
            service=service_name,
            taskDefinition=task_def_arn,
            desiredCount=desired_count,
        )

    @aws_retry
    def wait_stable(self, cluster: str, service_name: str, timeout_seconds: int = 300) -> None:
        """Block until the service reaches a stable state (all tasks running)."""
        waiter = self._client.get_waiter("services_stable")
        delay = 10
        waiter.wait(
            cluster=cluster,
            services=[service_name],
            WaiterConfig={"Delay": delay, "MaxAttempts": timeout_seconds // delay},
        )
