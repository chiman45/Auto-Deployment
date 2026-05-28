"""
Retry decorators backed by tenacity.

  exponential_retry — for ThrottlingException   (max 5 attempts, 2-30 s back-off)
  linear_retry      — for ServiceUnavailable    (max 3 attempts, 5 s fixed wait)
  aws_retry         — stacks both; apply to any boto3 call
"""
from __future__ import annotations

import functools
from typing import Callable, TypeVar

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
)

from .classifier import ErrorAction, classify

F = TypeVar("F", bound=Callable)


def _is_throttle(exc: BaseException) -> bool:
    return classify(exc) == ErrorAction.RETRY_EXPONENTIAL  # type: ignore[arg-type]


def _is_unavailable(exc: BaseException) -> bool:
    return classify(exc) == ErrorAction.RETRY_LINEAR  # type: ignore[arg-type]


_exponential = retry(
    retry=retry_if_exception(_is_throttle),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)

_linear = retry(
    retry=retry_if_exception(_is_unavailable),
    wait=wait_fixed(5),
    stop=stop_after_attempt(3),
    reraise=True,
)


def aws_retry(fn: F) -> F:
    """Decorator: exponential back-off for throttling, linear for unavailability."""
    @functools.wraps(fn)
    @_exponential
    @_linear
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fn(*args, **kwargs)
    return wrapper  # type: ignore[return-value]
