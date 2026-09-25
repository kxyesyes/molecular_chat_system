from __future__ import annotations

import asyncio
import inspect
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import BoundedSemaphore
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolResult
from src.agent.persistence import redact_sensitive
from src.agent.runtime.worker_ownership import reserve_worker

from .spec import ToolSpec


class ToolAdapter(ABC):
    adapter_version = "1"

    def __init__(self, spec: ToolSpec):
        self.spec = spec
        self._available = True
        self._health_message = "available"
        self._invocation_slots = BoundedSemaphore(spec.max_concurrency)

    def set_available(self, available: bool, message: str = "") -> None:
        self._available = available
        self._health_message = message or ("available" if available else "unavailable")

    def health(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "version": self.spec.version,
            "adapter_version": self.adapter_version,
            "available": self._available,
            "message": self._health_message,
            "capabilities": sorted(self.spec.capabilities),
        }

    def execute(self, input_data: Any, *, allow_retry: bool = True,
                raw_validator: Callable[[Any], None] | None = None,
                dispatch_guard: Callable[[], None] | None = None) -> ToolResult:
        if type(allow_retry) is not bool:
            raise TypeError("allow_retry must be a bool")
        if raw_validator is not None and not callable(raw_validator):
            raise TypeError("raw_validator must be callable")
        if dispatch_guard is not None and not callable(dispatch_guard):
            raise TypeError("dispatch_guard must be callable")
        if not self._available:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.TOOL_UNAVAILABLE,
                self._health_message,
            )
        try:
            payload = self._validate_input(input_data)
        except ValidationError as exc:
            return self._input_validation_error(exc)

        policy = self.spec.retry_policy
        max_attempts = policy.max_attempts if allow_retry else 1
        last_result: ToolResult | None = None
        for attempt in range(1, max_attempts + 1):
            if dispatch_guard is not None:
                # Input validation and any retry backoff consume active credit.
                dispatch_guard()
                last_result = self._execute_once(payload, raw_validator=raw_validator,
                                                  dispatch_guard=dispatch_guard)
            else:
                # No new kwargs for existing _execute_once overrides.
                last_result = (self._execute_once(payload) if raw_validator is None else
                               self._execute_once(payload, raw_validator=raw_validator))
            if last_result.success:
                return self._validate_output(last_result)
            last_result = self._validate_output(last_result)
            error_code = last_result.error.code.value if last_result.error else ""
            explicitly_retryable = last_result.quality.get("retryable")
            if (
                attempt >= max_attempts
                or error_code not in policy.retryable_error_codes
                or explicitly_retryable is False
            ):
                return last_result
            if policy.backoff_seconds:
                time.sleep(policy.backoff_seconds * (2 ** (attempt - 1)))
        return last_result or ToolResult.error_result(
            self.spec.name,
            AgentErrorCode.INTERNAL_ERROR,
            "Tool execution produced no result",
        )

    def _input_validation_error(self, exc: ValidationError) -> ToolResult:
        return ToolResult.error_result(
            self.spec.name,
            AgentErrorCode.INVALID_INPUT,
            "Tool input validation failed",
            {"errors": exc.errors(include_url=False)},
        )

    def _validate_input(self, input_data: Any) -> Any:
        if self.spec.input_schema is None:
            return input_data
        if isinstance(input_data, self.spec.input_schema):
            model = input_data
        else:
            model = self.spec.input_schema.model_validate(input_data)
        data = model.model_dump()
        if set(data) == {"query"}:
            return data["query"]
        return data

    def _execute_once(self, payload: Any, *, raw_validator=None, dispatch_guard=None) -> ToolResult:
        if not self._invocation_slots.acquire(blocking=False):
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.TOOL_UNAVAILABLE,
                "Tool concurrency limit reached",
                quality={"retryable": False, "capacity_exhausted": True},
            )
        start = time.perf_counter()
        reservation = None
        try:
            reservation = reserve_worker()
            executor = ThreadPoolExecutor(max_workers=1)
        except BaseException as exc:
            if reservation is not None:
                reservation.rollback()
            self._invocation_slots.release()
            if not isinstance(exc, Exception):
                raise
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.INTERNAL_ERROR,
                "Tool worker unavailable",
                quality={"retryable": False},
            )
        try:
            call = self.invoke if raw_validator is None else self._invoke_guarded
            args = (payload,) if raw_validator is None else (payload, raw_validator)
            if dispatch_guard is not None:
                # Reservation/executor construction may consume the remaining
                # credit. A failure here uses the same rollback/join journal as
                # a failed submit, with no physical tool invocation.
                dispatch_guard()

                def guarded_call():
                    # A submitted worker can wait in a queue past its cap.
                    # Recheck inside the reservation scope, before invoke.
                    dispatch_guard()
                    return call(*args)

                submitted_call, submitted_args = guarded_call, ()
            else:
                submitted_call, submitted_args = call, args
            future = (executor.submit(submitted_call, *submitted_args) if reservation is None else
                      executor.submit(reservation.run, submitted_call, *submitted_args))
        except BaseException:
            if reservation is not None:
                reservation.rollback(executor)
            self._invocation_slots.release()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        if reservation is not None:
            reservation.attach(executor, future)
        future.add_done_callback(lambda _: self._invocation_slots.release())
        try:
            raw = future.result(timeout=self.spec.timeout_seconds)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return self._normalize(raw, elapsed_ms)
        except FutureTimeoutError:
            future.cancel()
            return self._timeout_result(
                f"Tool timed out after {self.spec.timeout_seconds} seconds"
            )
        except (ConnectionError, httpx.HTTPError) as exc:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.PROVIDER_ERROR,
                str(exc) or "Provider request failed",
            )
        except Exception as exc:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.INTERNAL_ERROR,
                str(exc),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _timeout_result(self, message: str) -> ToolResult:
        return ToolResult.error_result(
            self.spec.name,
            AgentErrorCode.TOOL_TIMEOUT,
            message,
            quality={
                "retryable": False,
                "deadline_exceeded": True,
                "invocation_may_still_be_running": True,
            },
        )

    def _invoke_guarded(self, payload: Any, raw_validator) -> Any:
        """Per-invocation opt-in boundary, inside the same slot and deadline."""
        raw = self.invoke(payload)
        raw_validator(raw)
        return raw

    def _normalize(self, raw: Any, elapsed_ms: int) -> ToolResult:
        if isinstance(raw, ToolResult):
            if raw.elapsed_ms is None:
                raw.elapsed_ms = elapsed_ms
            raw.data = redact_sensitive(raw.data, self.spec.sensitive_fields)
            return raw
        if isinstance(raw, dict):
            if raw.get("success", False):
                return ToolResult.success_result(
                    tool_name=self.spec.name,
                    data=redact_sensitive(
                        raw.get("data"), self.spec.sensitive_fields
                    ),
                    message=raw.get("message", ""),
                    formatted=raw.get("formatted", ""),
                    elapsed_ms=elapsed_ms,
                    warnings=raw.get("warnings", []),
                    evidence=raw.get("evidence", []),
                    quality=raw.get("quality", {}),
                )
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.INTERNAL_ERROR,
                raw.get("message", "Tool execution failed"),
                {"raw_result": redact_sensitive(raw, self.spec.sensitive_fields)},
                elapsed_ms,
            )
        return ToolResult.success_result(
            self.spec.name,
            data=redact_sensitive(raw, self.spec.sensitive_fields),
            message="Tool execution completed",
            elapsed_ms=elapsed_ms,
        )

    def _validate_output(self, result: ToolResult) -> ToolResult:
        if not result.success or self.spec.output_schema is None:
            return result
        try:
            validated = self.spec.output_schema.model_validate(result.data)
        except ValidationError as exc:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.INVALID_OUTPUT,
                "Tool output validation failed",
                {"errors": exc.errors(include_url=False)},
                result.elapsed_ms,
            )
        result.data = redact_sensitive(
            validated.model_dump(), self.spec.sensitive_fields
        )
        return result

    @abstractmethod
    def invoke(self, payload: Any) -> Any:
        raise NotImplementedError


class LegacyPythonToolAdapter(ToolAdapter):
    def health(self) -> dict[str, Any]:
        health = super().health()
        health["readiness"] = "not_probed" if self.readiness_unknown else "adapter_ready"
        if self.readiness_unknown and health["available"]:
            health.update(available=None, message="Runtime dependencies not probed; lazy execution permitted")
        probe = getattr(self.tool, "registration_health", None)
        if health["available"] is not False and callable(probe):
            # Opt-in contract: inspect existing local state only, no loading/I/O.
            state = probe()
            health.update(available=state["available"], message=state["message"])
            health["readiness"] = "local_state_ready" if state["available"] else "unavailable"
        if health["available"] is False:
            health["readiness"] = "unavailable"
        if self._last_execution:
            health.update(self._last_execution)
        return health

    def __init__(self, spec: ToolSpec, tool: Any, *, readiness_unknown: bool = False):
        super().__init__(spec)
        self.tool = tool
        self.readiness_unknown = readiness_unknown
        self._last_execution = {}

    def execute(self, input_data: Any, **kwargs) -> ToolResult:
        result = super().execute(input_data, **kwargs)
        # A request-specific failure/success cannot establish tool-wide readiness.
        # Telemetry must not throw before the caller validates a raw observation,
        # nor retain arbitrary provider text in these enum-only health fields.
        valid_status = isinstance(result.status, ObservationStatus)
        error_code = getattr(result.error, "code", None)
        self._last_execution = {
            "last_execution_status": result.status.value if valid_status else "failed",
            "last_error_code": (
                error_code.value if isinstance(error_code, AgentErrorCode)
                else AgentErrorCode.INVALID_OUTPUT.value
                if not valid_status or result.error is not None else None
            ),
        }
        return result

    def invoke(self, payload: Any) -> Any:
        return self._invoke_guarded(payload, None)

    def _invoke_guarded(self, payload: Any, raw_validator) -> Any:
        from src.agent.tools.base_tool import execute_tool_compat

        raw = self.tool.execute(payload)
        if raw_validator is not None:
            raw_validator(raw)
        if isinstance(raw, dict) and (
            ("success" in raw and type(raw["success"]) is not bool)
            or (raw.get("success") is True and (
                raw.get("error") is not None
                or raw.get("status") in {
                    "failed", "rejected", "cancelled", "unavailable", "invalid_input"
                }
            ))
            or (raw.get("success") is False and raw.get("status") == "succeeded")
        ):
            return ToolResult.error_result(
                self.spec.name, AgentErrorCode.INVALID_OUTPUT,
                "Conflicting raw tool result status",
            )
        if isinstance(raw, ToolResult):
            return raw

        class CompletedInvocation:
            name = self.spec.name

            def execute(self, _payload: Any) -> Any:
                return raw

        result = execute_tool_compat(CompletedInvocation(), payload)
        result.elapsed_ms = None
        return result

    def close(self) -> None:
        close = getattr(self.tool, "close", None)
        if callable(close):
            close()


class HTTPToolAdapter(ToolAdapter):
    def __init__(
        self,
        spec: ToolSpec,
        url: str,
        method: str = "POST",
        headers_factory: Callable[[], dict[str, str]] | None = None,
    ):
        super().__init__(spec)
        self.url = url
        self.method = method.upper()
        self.headers_factory = headers_factory

    def invoke(self, payload: Any) -> Any:
        with httpx.Client(timeout=self.spec.timeout_seconds) as client:
            response = client.request(
                self.method,
                self.url,
                json=payload,
                headers=self.headers_factory() if self.headers_factory else None,
            )
            response.raise_for_status()
            return response.json()


class ModelToolAdapter(ToolAdapter):
    def __init__(self, spec: ToolSpec, model: Any):
        super().__init__(spec)
        self.model = model

    def invoke(self, payload: Any) -> Any:
        prompt = payload if isinstance(payload, str) else str(payload)
        generated = self.model.generate(prompt)
        if inspect.isawaitable(generated):
            return asyncio.run(generated)
        return generated


class CompositeToolAdapter(ToolAdapter):
    def __init__(
        self,
        spec: ToolSpec,
        adapters: list[ToolAdapter],
        combine: Callable[[list[ToolResult]], Any] | None = None,
    ):
        super().__init__(spec)
        self.adapters = adapters
        self.combine = combine

    def invoke(self, payload: Any) -> Any:
        results = [adapter.execute(payload) for adapter in self.adapters]
        if self.combine:
            return self.combine(results)
        if any(not result.success for result in results):
            failed = next(result for result in results if not result.success)
            return failed
        return {
            "success": True,
            "message": "Composite tool completed",
            "data": [result.data for result in results],
        }


class MCPToolAdapter(ToolAdapter):
    """Reserved interface for future MCP-backed tools."""

    def __init__(self, spec: ToolSpec):
        super().__init__(spec)
        self.set_available(False, "MCP tool transport is not configured")

    def invoke(self, payload: Any) -> Any:
        raise RuntimeError("MCP tool transport is not configured")
