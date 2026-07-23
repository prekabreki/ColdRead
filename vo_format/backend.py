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
from typing import TYPE_CHECKING, Literal

from . import claude_code_backend as _cli
from . import preflight as _api

if TYPE_CHECKING:
    from .models import DiagnosticReport, FormattedBlock, PreflightResult

Backend = Literal["api", "claude-code"]

VALID_BACKENDS: tuple[Backend, ...] = ("api", "claude-code")


def resolve_backend(requested: str | None) -> Backend:
    """Resolve the backend to use.

    Explicit request wins. Otherwise: env var VO_FORMAT_BACKEND, otherwise
    auto-detect (api if ANTHROPIC_API_KEY is set, else claude-code if the CLI
    is installed, else api so the caller gets the familiar 'no API key' error).
    """
    if requested:
        if requested not in VALID_BACKENDS:
            raise ValueError(f"Unknown backend '{requested}'. Choices: {', '.join(VALID_BACKENDS)}")
        return requested  # type: ignore[return-value]

    env = os.environ.get("VO_FORMAT_BACKEND")
    if env:
        if env not in VALID_BACKENDS:
            raise ValueError(f"VO_FORMAT_BACKEND='{env}' is invalid. Choices: {', '.join(VALID_BACKENDS)}")
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
) -> PreflightResult:
    chosen = resolve_backend(backend)
    if chosen == "claude-code":
        return _cli.run_preflight(script_text, filename, api_key=api_key, model=model)
    return _api.run_preflight(
        script_text,
        filename,
        api_key=api_key,
        model=model or "claude-sonnet-4-5-20250929",
    )


def run_pronunciation(
    backend: str | None,
    words: list[str],
    script_context: str,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    chosen = resolve_backend(backend)
    if chosen == "claude-code":
        return _cli.run_pronunciation(words, script_context, api_key=api_key, model=model)
    return _api.run_pronunciation(
        words,
        script_context,
        api_key=api_key,
        model=model or "claude-sonnet-4-5-20250929",
    )


def run_diagnostic(
    backend: str | None,
    script_text: str,
    preflight_result: PreflightResult,
    formatted_blocks: list[FormattedBlock],
    api_key: str | None = None,
    model: str | None = None,
) -> DiagnosticReport:
    chosen = resolve_backend(backend)
    if chosen == "claude-code":
        return _cli.run_diagnostic(script_text, preflight_result, formatted_blocks, api_key=api_key, model=model)
    return _api.run_diagnostic(
        script_text,
        preflight_result,
        formatted_blocks,
        api_key=api_key,
        model=model or "claude-sonnet-4-5-20250929",
    )
