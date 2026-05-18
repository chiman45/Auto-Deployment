"""boto3 wrappers for AWS CloudFormation — drift detection, change sets, updates."""
from __future__ import annotations

import time

import boto3
from botocore.exceptions import ClientError

from ..errors.retry import aws_retry


class CFNClient:
    def __init__(self, region: str):
        self._client = boto3.client("cloudformation", region_name=region)

    @aws_retry
    def describe_stack(self, stack_name: str) -> dict | None:
        """Return the stack dict, or None if it does not exist."""
        try:
            resp = self._client.describe_stacks(StackName=stack_name)
            stacks = resp.get("Stacks", [])
            return stacks[0] if stacks else None
        except ClientError as exc:
            if "does not exist" in exc.response["Error"]["Message"]:
                return None
            raise

    @aws_retry
    def detect_drift(self, stack_name: str) -> str:
        """
        Trigger drift detection and wait for results.
        Returns the StackDriftStatus string (e.g. 'IN_SYNC', 'DRIFTED').
        """
        resp = self._client.detect_stack_drift(StackName=stack_name)
        detection_id = resp["StackDriftDetectionId"]

        for _ in range(20):
            status_resp = self._client.describe_stack_drift_detection_status(
                StackDriftDetectionId=detection_id
            )
            if status_resp["DetectionStatus"] != "DETECTION_IN_PROGRESS":
                return status_resp.get("StackDriftStatus", "NOT_CHECKED")
            time.sleep(3)

        return "UNKNOWN"

    @aws_retry
    def create_change_set(
        self, stack_name: str, template_body: str, parameters: dict[str, str]
    ) -> str:
        """Create a change set and return its name."""
        cs_name = f"deployagent-{int(time.time())}"
        self._client.create_change_set(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()],
            ChangeSetName=cs_name,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )
        return cs_name

    @aws_retry
    def describe_change_set(self, stack_name: str, change_set_name: str) -> dict:
        """Poll until the change set is ready and return its description."""
        for _ in range(20):
            resp = self._client.describe_change_set(
                StackName=stack_name, ChangeSetName=change_set_name
            )
            if resp["Status"] not in ("CREATE_IN_PROGRESS", "CREATE_PENDING"):
                return resp
            time.sleep(3)
        return resp  # type: ignore[return-value]

    @aws_retry
    def execute_change_set(self, stack_name: str, change_set_name: str) -> None:
        self._client.execute_change_set(
            StackName=stack_name, ChangeSetName=change_set_name
        )

    @aws_retry
    def update_stack(self, stack_name: str, parameters: dict[str, str]) -> None:
        """Re-apply the current template with new parameter values (used by rollback)."""
        self._client.update_stack(
            StackName=stack_name,
            UsePreviousTemplate=True,
            Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()],
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )

    @aws_retry
    def wait_complete(self, stack_name: str, timeout_seconds: int = 600) -> None:
        waiter = self._client.get_waiter("stack_update_complete")
        delay = 10
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": delay, "MaxAttempts": timeout_seconds // delay},
        )
