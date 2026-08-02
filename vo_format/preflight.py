"""Claude API preflight analysis and diagnostic review."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

log = logging.getLogger(__name__)

from ._backend_shared import (  # noqa: E402, F401 — re-exported for backward compat
    APIConnectionError,
    APIResponseError,
    API_TIMEOUT,
    DIAGNOSTIC_SYSTEM_PROMPT,
    JSONParseError,
    PREFLIGHT_SYSTEM_PROMPT,
    PRONUNCIATION_SYSTEM_PROMPT,
    PreflightError,
    ValidationError,
    DEFAULT_API_MODEL,
    _build_diagnostic_message,
    _build_preflight_message,
    _build_pronunciation_message,
    _extract_json,
    _validate_and_build,
)
from .models import (
    DiagnosticEntry,
    DiagnosticReport,
    FormattedBlock,
    PreflightResult,
)


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------


def _get_api_key(cli_key: str | None = None) -> str:
    """Resolve the Anthropic API key."""
    if cli_key:
        return cli_key
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    raise PreflightError(
        "No API key found. Set ANTHROPIC_API_KEY environment variable "
        "or pass --api-key on the command line."
    )


def run_preflight(
    script_text: str,
    filename: str,
    api_key: str | None = None,
    model: str = DEFAULT_API_MODEL,
) -> PreflightResult:
    """Run Claude API preflight analysis on a script.

    Args:
        script_text: The normalized script text.
        filename: Original filename for context.
        api_key: Anthropic API key (falls back to env var).
        model: Model to use for preflight.

    Returns:
        PreflightResult with all detected structure.

    Raises:
        PreflightError: On any API or parsing failure.
    """
    key = _get_api_key(api_key)
    user_message = _build_preflight_message(script_text, filename)

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=PREFLIGHT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=API_TIMEOUT,
        )
    except anthropic.APIConnectionError as e:
        raise APIConnectionError(f"Could not connect to Claude API: {e}") from e
    except anthropic.APIStatusError as e:
        raise APIResponseError(f"Claude API error: {e.message}") from e

    # Extract text content
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    if not response_text.strip():
        raise JSONParseError("API returned empty response")

    data = _extract_json(response_text)
    return _validate_and_build(data)


# ---------------------------------------------------------------------------
# Diagnostic review
# ---------------------------------------------------------------------------


def run_diagnostic(
    script_text: str,
    preflight_result: PreflightResult,
    formatted_blocks: list[FormattedBlock],
    api_key: str | None = None,
    model: str = DEFAULT_API_MODEL,
) -> DiagnosticReport:
    """Run a diagnostic review comparing formatter output to the original script.

    This is a second API call (opt-in via --diagnose) that identifies
    misclassified lines and formatting issues.
    """
    user_message = _build_diagnostic_message(
        script_text, preflight_result, formatted_blocks
    )

    try:
        key = _get_api_key(api_key)
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=DIAGNOSTIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=API_TIMEOUT,
        )
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
        return DiagnosticReport(
            misclassified_lines=[],
            missed_characters=[],
            missed_stage_directions=[],
            missed_sound_cues=[],
            unstripped_metadata=[],
            unhandled_patterns=[],
            summary=f"Diagnostic API call failed: {e}",
        )

    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    try:
        data = _extract_json(response_text)
    except JSONParseError:
        return DiagnosticReport(
            misclassified_lines=[],
            missed_characters=[],
            missed_stage_directions=[],
            missed_sound_cues=[],
            unstripped_metadata=[],
            unhandled_patterns=[],
            summary=f"Could not parse diagnostic response: {response_text[:300]}",
        )

    # Build report
    misclassified = []
    for entry in data.get("misclassified_lines", []):
        misclassified.append(
            DiagnosticEntry(
                line_number=int(entry.get("line_number", 0)),
                original_text=str(entry.get("original_text", "")),
                assigned_type=str(entry.get("assigned_type", "")),
                issue=str(entry.get("issue", "")),
                suggestion=str(entry.get("suggestion", "")),
            )
        )

    return DiagnosticReport(
        misclassified_lines=misclassified,
        missed_characters=data.get("missed_characters", []),
        missed_stage_directions=data.get("missed_stage_directions", []),
        missed_sound_cues=data.get("missed_sound_cues", []),
        unstripped_metadata=data.get("unstripped_metadata", []),
        unhandled_patterns=data.get("unhandled_patterns", []),
        summary=data.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# Pronunciation guide generation
# ---------------------------------------------------------------------------


def run_pronunciation(
    words: list[str],
    script_context: str,
    api_key: str | None = None,
    model: str = DEFAULT_API_MODEL,
) -> dict[str, str]:
    """Generate phonetic spellings for a list of words via Claude API.

    Args:
        words: List of words to generate phonetics for.
        script_context: Brief context string (e.g. "Warcraft fantasy setting").
        api_key: Anthropic API key (falls back to env var).
        model: Model to use.

    Returns:
        Dict mapping each word to its phonetic spelling.
        Returns empty dict on failure (non-fatal).
    """
    if not words:
        return {}

    key = _get_api_key(api_key)
    user_message = _build_pronunciation_message(words, script_context)

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,
            system=PRONUNCIATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=API_TIMEOUT,
        )
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
        log.warning("Pronunciation guide API call failed: %s", e)
        return {}

    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text += block.text

    try:
        data = _extract_json(response_text)
    except JSONParseError as e:
        log.warning("Could not parse pronunciation guide response: %s", e)
        return {}

    # Validate: should be a flat dict of str -> str
    result = {}
    for word, phonetic in data.items():
        if isinstance(phonetic, str):
            result[str(word)] = phonetic

    return result
