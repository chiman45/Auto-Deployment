"""boto3 wrappers for Application Load Balancer (ALB), Target Groups, and Listeners."""
from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from ..errors.retry import aws_retry


class ALBClient:
    def __init__(self, region: str):
        self._client = boto3.client("elbv2", region_name=region)

    # ── Target Group ──────────────────────────────────────────────────────────

    @aws_retry
    def find_target_group(self, name: str) -> dict | None:
        """Return existing target group by name, or None."""
        if not name:
            return None
        try:
            resp = self._client.describe_target_groups(Names=[name])
            tgs = resp.get("TargetGroups", [])
            return tgs[0] if tgs else None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in (
                "TargetGroupNotFoundException",
                "TargetGroupNotFound",
            ):
                return None
            raise

    @aws_retry
    def create_target_group(
        self,
        name: str,
        port: int,
        vpc_id: str,
        health_check_path: str = "/health",
        protocol: str = "HTTP",
    ) -> dict:
        """Create a new target group and return it."""
        resp = self._client.create_target_group(
            Name=name,
            Protocol=protocol,
            Port=port,
            VpcId=vpc_id,
            TargetType="ip",
            HealthCheckProtocol=protocol,
            HealthCheckPath=health_check_path,
            HealthCheckIntervalSeconds=30,
            HealthCheckTimeoutSeconds=5,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=3,
            Matcher={"HttpCode": "200-299"},
        )
        return resp["TargetGroups"][0]

    # ── Load Balancer ─────────────────────────────────────────────────────────

    @aws_retry
    def find_alb(self, name: str) -> dict | None:
        """Return existing ALB by name, or None."""
        if not name:
            return None
        try:
            resp = self._client.describe_load_balancers(Names=[name])
            lbs = resp.get("LoadBalancers", [])
            return lbs[0] if lbs else None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in (
                "LoadBalancerNotFoundException",
                "LoadBalancerNotFound",
            ):
                return None
            raise

    @aws_retry
    def create_alb(
        self,
        name: str,
        subnets: list[str],
        security_groups: list[str],
        internal: bool = False,
    ) -> dict:
        """Create an ALB and return it."""
        resp = self._client.create_load_balancer(
            Name=name,
            Subnets=subnets,
            SecurityGroups=security_groups,
            Scheme="internal" if internal else "internet-facing",
            Type="application",
            IpAddressType="ipv4",
        )
        return resp["LoadBalancers"][0]

    @aws_retry
    def wait_alb_active(self, alb_arn: str, timeout_seconds: int = 300) -> None:
        """Block until the ALB is in the active state."""
        waiter = self._client.get_waiter("load_balancer_available")
        delay = 15
        waiter.wait(
            LoadBalancerArns=[alb_arn],
            WaiterConfig={"Delay": delay, "MaxAttempts": timeout_seconds // delay},
        )

    # ── Listener ──────────────────────────────────────────────────────────────

    @aws_retry
    def find_listener(self, alb_arn: str, port: int = 80) -> dict | None:
        """Return existing listener on this ALB + port, or None."""
        resp = self._client.describe_listeners(LoadBalancerArn=alb_arn)
        for l in resp.get("Listeners", []):
            if l.get("Port") == port:
                return l
        return None

    @aws_retry
    def create_listener(
        self,
        alb_arn: str,
        target_group_arn: str,
        port: int = 80,
        protocol: str = "HTTP",
    ) -> dict:
        """Create an HTTP listener that forwards to the target group."""
        resp = self._client.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol=protocol,
            Port=port,
            DefaultActions=[
                {"Type": "forward", "TargetGroupArn": target_group_arn}
            ],
        )
        return resp["Listeners"][0]

    @aws_retry
    def update_listener(self, listener_arn: str, target_group_arn: str) -> None:
        """Point an existing listener at a (possibly new) target group."""
        self._client.modify_listener(
            ListenerArn=listener_arn,
            DefaultActions=[
                {"Type": "forward", "TargetGroupArn": target_group_arn}
            ],
        )

    def get_dns_name(self, alb_arn: str) -> str | None:
        """Return the DNS name of an ALB."""
        try:
            resp = self._client.describe_load_balancers(LoadBalancerArns=[alb_arn])
            lbs = resp.get("LoadBalancers", [])
            return lbs[0].get("DNSName") if lbs else None
        except ClientError:
            return None


def ensure_alb(
    region: str,
    alb_name: str,
    tg_name: str,
    port: int,
    vpc_id: str,
    subnets: list[str],
    security_groups: list[str],
    health_check_path: str = "/health",
    listener_port: int = 80,
) -> tuple[str, str, str]:
    """
    Idempotent helper: create or reuse ALB + target group + listener.
    Returns (alb_arn, target_group_arn, dns_name).
    """
    client = ALBClient(region)

    # Target group
    tg = client.find_target_group(tg_name)
    if tg is None:
        tg = client.create_target_group(
            name=tg_name,
            port=port,
            vpc_id=vpc_id,
            health_check_path=health_check_path,
        )
    tg_arn = tg["TargetGroupArn"]

    # ALB
    alb = client.find_alb(alb_name)
    if alb is None:
        alb = client.create_alb(
            name=alb_name,
            subnets=subnets,
            security_groups=security_groups,
        )
        client.wait_alb_active(alb["LoadBalancerArn"])
    alb_arn = alb["LoadBalancerArn"]
    dns_name = alb.get("DNSName", "")

    # Listener
    listener = client.find_listener(alb_arn, port=listener_port)
    if listener is None:
        client.create_listener(alb_arn, tg_arn, port=listener_port)
    else:
        client.update_listener(listener["ListenerArn"], tg_arn)

    return alb_arn, tg_arn, dns_name
