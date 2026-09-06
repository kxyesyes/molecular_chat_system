from __future__ import annotations

import asyncio
import inspect
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.persistence import redact_sensitive

from .spec import ToolSpec


class ToolAdapter(ABC):
    adapter_version = "1"

    def __init__(self, spec: ToolSpec):
        self.spec = spec
        self._available = True
        self._health_message = "available"

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

    def execute(self, input_data: Any) -> ToolResult:
        if not self._available:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.TOOL_UNAVAILABLE,
                self._health_message,
            )
        try:
            payload = self._validate_input(input_data)
        except ValidationError as exc:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.INVALID_INPUT,
                "Tool input validation failed",
                {"errors": exc.errors(include_url=False)},
            )

        policy = self.spec.retry_policy
        last_result: ToolResult | None = None
        for attempt in range(1, policy.max_attempts + 1):
            last_result = self._execute_once(payload)
            if last_result.success:
                return self._validate_output(last_result)
            error_code = last_result.error.code.value if last_result.error else ""
            explicitly_retryable = last_result.quality.get("retryable")
            if (
                attempt >= policy.max_attempts
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

    def _execute_once(self, payload: Any) -> ToolResult:
        start = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.invoke, payload)
                raw = future.result(timeout=self.spec.timeout_seconds)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return self._normalize(raw, elapsed_ms)
        except FutureTimeoutError:
            return ToolResult.error_result(
                self.spec.name,
                AgentErrorCode.TOOL_TIMEOUT,
                f"Tool timed out after {self.spec.timeout_seconds} seconds",
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
    def __init__(self, spec: ToolSpec, tool: Any):
        super().__init__(spec)
        self.tool = tool

    def invoke(self, payload: Any) -> Any:
        from src.agent.tools.base_tool import execute_tool_compat

        raw = self.tool.execute(payload)
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
