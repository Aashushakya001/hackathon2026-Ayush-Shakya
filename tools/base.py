"""
tools/base.py — Retry logic, timeout handling, and tool call registry.
All tools use @tool_call decorator which enforces:
  - asyncio timeout
  - exponential backoff retry
  - structured ToolResult on failure
  - duration measurement
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
import random
from typing import Callable, Any

from models import ToolResult
from config import config

logger = logging.getLogger(__name__)


def tool_call(
    tool_name: str,
    max_retries: int = None,
    base_delay: float = None,
    timeout: float = None,
):
    """
    Decorator that wraps an async tool function with:
    - Configurable timeout (asyncio.wait_for)
    - Exponential backoff retry (1s → 2s → 4s)
    - Structured ToolResult even on failure
    - Duration measurement in milliseconds
    """
    _max_retries = max_retries or config.MAX_TOOL_RETRIES
    _base_delay = base_delay or config.RETRY_BASE_DELAY
    _timeout = timeout or config.TOOL_TIMEOUT_SECONDS

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> ToolResult:
            attempt = 0
            last_error = None
            last_error_type = None
            start_time = time.monotonic()

            while attempt < _max_retries:
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=_timeout,
                    )
                    duration_ms = (time.monotonic() - start_time) * 1000
                    logger.debug(
                        f"[{tool_name}] success on attempt {attempt + 1} "
                        f"in {duration_ms:.1f}ms"
                    )
                    if isinstance(result, ToolResult):
                        result.duration_ms = duration_ms
                        result.retries_used = attempt
                        return result
                    return ToolResult(
                        tool_name=tool_name,
                        success=True,
                        data=result,
                        retries_used=attempt,
                        duration_ms=duration_ms,
                    )

                except asyncio.TimeoutError:
                    attempt += 1
                    last_error = f"Tool '{tool_name}' timed out after {_timeout}s"
                    last_error_type = "timeout"
                    logger.warning(f"[{tool_name}] timeout on attempt {attempt}")

                except ValueError as e:
                    # Malformed / validation errors — no retry, fail fast
                    duration_ms = (time.monotonic() - start_time) * 1000
                    logger.error(f"[{tool_name}] validation error: {e}")
                    return ToolResult(
                        tool_name=tool_name,
                        success=False,
                        error=str(e),
                        error_type="validation_error",
                        retries_used=attempt,
                        duration_ms=duration_ms,
                    )

                except KeyError as e:
                    duration_ms = (time.monotonic() - start_time) * 1000
                    logger.error(f"[{tool_name}] not found: {e}")
                    return ToolResult(
                        tool_name=tool_name,
                        success=False,
                        error=f"Record not found: {e}",
                        error_type="not_found",
                        retries_used=attempt,
                        duration_ms=duration_ms,
                    )

                except Exception as e:
                    attempt += 1
                    last_error = str(e)
                    last_error_type = "unexpected"
                    logger.warning(
                        f"[{tool_name}] error on attempt {attempt}: {e}"
                    )

                if attempt < _max_retries:
                    delay = _base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
                    logger.info(f"[{tool_name}] retrying in {delay:.2f}s...")
                    await asyncio.sleep(delay)

            duration_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                f"[{tool_name}] permanently failed after {_max_retries} attempts. "
                f"Last error: {last_error}"
            )
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=last_error,
                error_type=last_error_type,
                retries_used=attempt,
                duration_ms=duration_ms,
            )

        return wrapper
    return decorator


def inject_realistic_failure(failure_rate: float = 0.12, malformed_rate: float = 0.08):
    """
    Decorator that randomly injects realistic failures into a tool.
    failure_rate  → asyncio.TimeoutError (simulated hang)
    malformed_rate → returns malformed/partial data (ValueError raised)
    
    This satisfies the hackathon constraint: at least one tool will
    timeout or return malformed data.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            roll = random.random()
            if roll < failure_rate:
                logger.warning(
                    f"[CHAOS] Injecting timeout into {func.__name__}"
                )
                await asyncio.sleep(10)  # will be caught by timeout wrapper
            elif roll < failure_rate + malformed_rate:
                logger.warning(
                    f"[CHAOS] Injecting malformed data into {func.__name__}"
                )
                raise ValueError(
                    f"Malformed response from {func.__name__}: "
                    "unexpected null in required field 'status'"
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator
