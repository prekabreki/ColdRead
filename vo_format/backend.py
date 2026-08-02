"""Backend selector for script analysis.

Two backends:
- "api"          Direct Anthropic API calls (preflight.py). Needs ANTHROPIC_API_KEY.
- "claude-code"  Local Claude Code CLI in --print mode. Uses the user's
                 Claude.ai subscription (OAuth) instead of API credit.

Public entrypoints (`run_preflight`, `run_pronunciation`, `run_diagnostic`)
share the same signature as their `preflight` counterparts with an extra
leading `backend` argument so the CLI/GUI can route at call time without
having to import two modules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Literal, Protocol

from ._backend_shared import DEFAULT_API_MODEL
from .models import DiagnosticReport, FormattedBlock, PreflightResult
from . import preflight as _api
from . import claude_code_backend as _cli

BackendName = Literal["api", "claude-code"]

VALID_BACKENDS: tuple[BackendName, ...] = ("api", "claude-code")


class Backend(Protocol):
    """Protocol satisfied by both analysis backends.

    Each method mirrors the inner signature of the corresponding function
    in `preflight.py` / `claude_code_backend.py` (no leading `backend`
    argument — that is handled by the dispatcher in this module).
    """

    def run_preflight(
        self,
        script_text: str,
        filename: str,
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> PreflightResult: ...

    def run_pronunciation(
        self,
        words: list[str],
        script_context: str,
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> dict[str, str]: ...

    def run_diagnostic(
        self,
        script_text: str,
        preflight_result: PreflightResult,
        formatted_blocks: list[FormattedBlock],
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> DiagnosticReport: ...


class _APIBackendImpl:
    """Adapter that applies the API model default before delegating."""

    def run_preflight(
        self,
        script_text: str,
        filename: str,
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> PreflightResult:
        return _api.run_preflight(
            script_text, filename, api_key=api_key,
            model=model or DEFAULT_API_MODEL,
        )

    def run_pronunciation(
        self,
        words: list[str],
        script_context: str,
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> dict[str, str]:
        return _api.run_pronunciation(
            words, script_context, api_key=api_key,
            model=model or DEFAULT_API_MODEL,
        )

    def run_diagnostic(
        self,
        script_text: str,
        preflight_result: PreflightResult,
        formatted_blocks: list[FormattedBlock],
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> DiagnosticReport:
        return _api.run_diagnostic(
            script_text, preflight_result, formatted_blocks,
            api_key=api_key, model=model or DEFAULT_API_MODEL,
        )


class _ClaudeCodeBackendImpl:
    """Adapter that passes `model` through — the subprocess backend handles
    its own default (`claude_code_backend.DEFAULT_MODEL`)."""

    def run_preflight(
        self,
        script_text: str,
        filename: str,
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> PreflightResult:
        return _cli.run_preflight(
            script_text, filename, api_key=api_key, model=model,
            process_registry=process_registry,
        )

    def run_pronunciation(
        self,
        words: list[str],
        script_context: str,
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> dict[str, str]:
        return _cli.run_pronunciation(
            words, script_context, api_key=api_key, model=model,
            process_registry=process_registry,
        )

    def run_diagnostic(
        self,
        script_text: str,
        preflight_result: PreflightResult,
        formatted_blocks: list[FormattedBlock],
        api_key: str | None = None,
        model: str | None = None,
        *,
        process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
    ) -> DiagnosticReport:
        return _cli.run_diagnostic(
            script_text, preflight_result, formatted_blocks,
            api_key=api_key, model=model,
            process_registry=process_registry,
        )


def get_backend(name: BackendName) -> Backend:
    """Return a Protocol-conforming backend for the given name."""
    if name == "claude-code":
        return _ClaudeCodeBackendImpl()
    return _APIBackendImpl()


def resolve_backend(requested: str | None) -> BackendName:
    """Resolve the backend to use.

    Explicit request wins. Otherwise: env var VO_FORMAT_BACKEND, otherwise
    auto-detect (api if ANTHROPIC_API_KEY is set, else claude-code if the CLI
    is installed, else api so the caller gets the familiar 'no API key' error).
    """
    if requested:
        if requested not in VALID_BACKENDS:
            raise ValueError(
                f"Unknown backend '{requested}'. Choices: {', '.join(VALID_BACKENDS)}"
            )
        return requested  # type: ignore[return-value]

    env = os.environ.get("VO_FORMAT_BACKEND")
    if env:
        if env not in VALID_BACKENDS:
            raise ValueError(
                f"VO_FORMAT_BACKEND='{env}' is invalid. Choices: {', '.join(VALID_BACKENDS)}"
            )
        return env  # type: ignore[return-value]

    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which(os.environ.get("VO_FORMAT_CLAUDE_CMD") or "claude"):
        return "claude-code"
    return "api"


def run_preflight(
    backend: str | None,
    script_text: str,
    filename: str,
    api_key: str | None = None,
    model: str | None = None,
    *,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> PreflightResult:
    chosen = resolve_backend(backend)
    impl = get_backend(chosen)
    return impl.run_preflight(
        script_text, filename, api_key=api_key, model=model,
        process_registry=process_registry,
    )


def run_pronunciation(
    backend: str | None,
    words: list[str],
    script_context: str,
    api_key: str | None = None,
    model: str | None = None,
    *,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> dict[str, str]:
    chosen = resolve_backend(backend)
    impl = get_backend(chosen)
    return impl.run_pronunciation(
        words, script_context, api_key=api_key, model=model,
        process_registry=process_registry,
    )


def run_diagnostic(
    backend: str | None,
    script_text: str,
    preflight_result: PreflightResult,
    formatted_blocks: list[FormattedBlock],
    api_key: str | None = None,
    model: str | None = None,
    *,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> DiagnosticReport:
    chosen = resolve_backend(backend)
    impl = get_backend(chosen)
    return impl.run_diagnostic(
        script_text, preflight_result, formatted_blocks,
        api_key=api_key, model=model,
        process_registry=process_registry,
    )
