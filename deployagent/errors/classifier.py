"""
AWS exception classifier — maps botocore ClientError codes to deploy actions.

Action mapping:
  ThrottlingException        → RETRY_EXPONENTIAL (max 5×)
  ServiceUnavailableException→ RETRY_LINEAR      (max 3×)
  InvalidParameterException  → FAIL_FAST
  ResourceNotFoundException  → ROLLBACK
  AccessDeniedException      → FAIL_FAST + IAM hint
  HealthCheckFailed          → ROLLBACK + log tail (raised by engine/health.py)
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Callable

from botocore.exceptions import ClientError


class ErrorAction(Enum):
    RETRY_EXPONENTIAL = auto()
    RETRY_LINEAR = auto()
    FAIL_FAST = auto()
    ROLLBACK = auto()


_CODE_TO_ACTION: dict[str, ErrorAction] = {
    # Throttling
    "ThrottlingException": ErrorAction.RETRY_EXPONENTIAL,
    "Throttling": ErrorAction.RETRY_EXPONENTIAL,
    "RequestThrottled": ErrorAction.RETRY_EXPONENTIAL,
    "TooManyRequestsException": ErrorAction.RETRY_EXPONENTIAL,
    "ProvisionedThroughputExceededException": ErrorAction.RETRY_EXPONENTIAL,
    # Transient unavailability
    "ServiceUnavailableException": ErrorAction.RETRY_LINEAR,
    "ServiceUnavailable": ErrorAction.RETRY_LINEAR,
    "InternalFailure": ErrorAction.RETRY_LINEAR,
    # Config / validation errors — fail immediately
    "InvalidParameterException": ErrorAction.FAIL_FAST,
    "InvalidParameterValue": ErrorAction.FAIL_FAST,
    "ValidationError": ErrorAction.FAIL_FAST,
    "InvalidClientTokenId": ErrorAction.FAIL_FAST,
    # Missing resource — rollback
    "ResourceNotFoundException": ErrorAction.ROLLBACK,
    "NoSuchEntity": ErrorAction.ROLLBACK,
    "ClusterNotFoundException": ErrorAction.ROLLBACK,
    # Access denied — fail fast + print IAM hint
    "AccessDeniedException": ErrorAction.FAIL_FAST,
    "AccessDenied": ErrorAction.FAIL_FAST,
    "AuthFailure": ErrorAction.FAIL_FAST,
    "UnauthorizedOperation": ErrorAction.FAIL_FAST,
}

_IAM_HINT = (
    "\n[bold]IAM fix:[/] Grant the following permission to your AWS principal:\n"
    "  Action  : {action}\n"
    "  Resource: {resource}\n"
    "Run [cyan]aws iam simulate-principal-policy[/] to verify."
)


class DeployError(Exception):
    def __init__(self, message: str, action: ErrorAction = ErrorAction.FAIL_FAST):
        super().__init__(message)
        self.action = action


class HealthCheckFailed(DeployError):
    """Raised by engine/health.py when the service is unhealthy post-deploy."""

    def __init__(self, message: str):
        super().__init__(message, ErrorAction.ROLLBACK)


def classify(exc: Exception) -> ErrorAction:
    """Return the recommended ErrorAction for any exception."""
    if isinstance(exc, HealthCheckFailed):
        return ErrorAction.ROLLBACK
    if isinstance(exc, DeployError):
        return exc.action
    if isinstance(exc, ClientError):
        code = exc.response["Error"]["Code"]
        return _CODE_TO_ACTION.get(code, ErrorAction.FAIL_FAST)
    return ErrorAction.FAIL_FAST


def iam_hint(exc: ClientError) -> str:
    action = exc.operation_name or "unknown:Action"
    resource = exc.response.get("Error", {}).get("Message", "arn:aws:*")
    return _IAM_HINT.format(action=action, resource=resource)


def handle(
    exc: Exception,
    on_rollback: Callable[[], None] | None = None,
) -> None:
    """
    Print a user-friendly error message, optionally trigger rollback,
    then re-raise as DeployError so callers can handle the action.
    """
    from rich.console import Console

    console = Console()
    action = classify(exc)

    if isinstance(exc, ClientError):
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]

        if action == ErrorAction.FAIL_FAST and code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation"):
            console.print(f"[bold red]Access denied:[/] {msg}")
            console.print(iam_hint(exc))
        elif action == ErrorAction.FAIL_FAST:
            console.print(f"[bold red]Config error ({code}):[/] {msg}")
        elif action == ErrorAction.ROLLBACK:
            console.print(f"[bold yellow]Resource not found ({code}):[/] {msg}")
            console.print("[bold yellow]→ Triggering automatic rollback[/]")
            if on_rollback:
                on_rollback()
        else:
            console.print(f"[yellow]AWS error ({code}):[/] {msg}")
    else:
        console.print(f"[bold red]Error:[/] {exc}")
        if action == ErrorAction.ROLLBACK and on_rollback:
            on_rollback()

    raise DeployError(str(exc), action)
