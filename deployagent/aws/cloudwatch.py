"""boto3 wrappers for CloudWatch Logs — tail log groups, surface errors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import boto3

from ..errors.retry import aws_retry


class CloudWatchClient:
    def __init__(self, region: str):
        self._logs = boto3.client("logs", region_name=region)
        self._cw = boto3.client("cloudwatch", region_name=region)

    @aws_retry
    def tail_logs(self, log_group: str, minutes: int = 5) -> list[str]:
        """
        Return recent log lines from the given log group, newest streams first.
        Returns an empty list (not an error) if the group doesn't exist yet.
        """
        start_ms = int(
            (datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp() * 1000
        )
        lines: list[str] = []

        try:
            streams = self._logs.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=3,
            ).get("logStreams", [])

            for stream in streams:
                events = self._logs.get_log_events(
                    logGroupName=log_group,
                    logStreamName=stream["logStreamName"],
                    startTime=start_ms,
                    limit=50,
                    startFromHead=False,
                ).get("events", [])

                for ev in events:
                    ts = datetime.fromtimestamp(
                        ev["timestamp"] / 1000, tz=timezone.utc
                    ).strftime("%H:%M:%S")
                    lines.append(f"[{ts}] {ev['message'].rstrip()}")

        except Exception:
            pass  # log group doesn't exist yet — not an error during plan

        return sorted(lines)

    def get_service_errors(self, log_group: str, minutes: int = 5) -> list[str]:
        """Return log lines that look like errors from the last N minutes."""
        _error_keywords = ("ERROR", "EXCEPTION", "FATAL", "CRITICAL", "PANIC")
        all_lines = self.tail_logs(log_group, minutes)
        return [ln for ln in all_lines if any(kw in ln.upper() for kw in _error_keywords)]
