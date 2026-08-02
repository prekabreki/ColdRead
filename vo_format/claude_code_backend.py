"""Claude Code subprocess backend for script analysis.

Mirrors the public API of `preflight.py` (`run_preflight`, `run_pronunciation`,
`run_diagnostic`) but invokes the local `claude` CLI in `--print` mode instead
of calling the Anthropic API directly. This lets users without API credit run
the analysis pipeline via their Claude Code subscription.

Auth: the CLI prefers `ANTHROPIC_API_KEY` over OAuth credentials, so this
module strips that env var before spawning so the subscription tokens are used.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

from ._backend_shared import (
    APIConnectionError,
    APIResponseError,
    DIAGNOSTIC_SYSTEM_PROMPT,
    JSONParseError,
    PREFLIGHT_SYSTEM_PROMPT,
    PRONUNCIATION_SYSTEM_PROMPT,
    PreflightError,
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
# Diagnostic file log — gated behind VO_FORMAT_DEBUG.
# ---------------------------------------------------------------------------
# The GUI's debug textbox sits idle while the subprocess runs (the worker
# thread blocks on subprocess.run). When something goes wrong in the
# windowed exe — no console parent, hung child, weird .cmd shim — we have
# no in-GUI breadcrumbs to read. This file log captures every step so the
# user can paste it back when reporting issues.
# Enable by setting the VO_FORMAT_DEBUG environment variable to any value.
# The log is automatically rotated (truncated) at 1 MiB to prevent unbounded
# growth. No script-derived content is ever written to the log.
DEBUG_LOG_PATH = pathlib.Path.home() / "vo-format-claude-debug.log"
_DEBUG_LOG_MAX_BYTES = 1_048_576  # 1 MiB


def _dbg(msg: str) -> None:
    """Append a timestamped line to the diagnostic log.

    Only writes when VO_FORMAT_DEBUG is set in the environment.  Truncates
    the log file when it exceeds _DEBUG_LOG_MAX_BYTES.  Best-effort — all
    failures are silently swallowed.
    """
    if not os.environ.get("VO_FORMAT_DEBUG"):
        return
    try:
        # Rotate (truncate) if the file exceeds the size cap.
        if DEBUG_LOG_PATH.exists() and DEBUG_LOG_PATH.stat().st_size > _DEBUG_LOG_MAX_BYTES:
            with DEBUG_LOG_PATH.open("w", encoding="utf-8") as f:
                f.write(f"[log rotated at {datetime.datetime.now().isoformat(timespec='milliseconds')}]\n")
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='milliseconds')}  {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------

# Sonnet is the default: haiku gets the structural shape right (archetype,
# section count, presence flags) but materially under-detects pronunciation
# flags and is inconsistent on character extraction — both directly user-
# visible for VO work. Users who want the speed (~3x faster) can opt in via
# VO_FORMAT_CLAUDE_CODE_MODEL=haiku or the `model` parameter.
DEFAULT_MODEL = "sonnet"

# Hard cap on per-call wall time. Document-heavy archetypes occasionally take
# 7-8 minutes for preflight (sonnet spends a lot of time on the first token,
# and the Claude Code wrapper buffers the whole response — no streaming
# progress to distinguish "thinking" from "stuck"). 20 minutes absorbs the
# slow tail while still surfacing genuinely hung processes.
DEFAULT_TIMEOUT_SEC = 1200


def _resolve_claude_cli() -> str:
    """Locate the `claude` executable on PATH."""
    cmd = os.environ.get("VO_FORMAT_CLAUDE_CMD") or "claude"
    resolved = shutil.which(cmd)
    if not resolved:
        raise APIConnectionError(
            f"Could not find '{cmd}' on PATH. Install Claude Code from "
            "https://claude.com/claude-code and sign in with `claude /login`, "
            "or set VO_FORMAT_CLAUDE_CMD to the full path."
        )
    return resolved


# Env vars that flip the CLI into pay-per-use API mode (or onto a
# third-party provider). We strip all of these so the subscription
# OAuth tokens are used instead. Learned the hard way: a stray
# ANTHROPIC_API_KEY in a dev shell quietly burned API credits even
# though the user had selected the "Claude Code" backend.
_API_MODE_ENV_VARS_TO_STRIP: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)


def _build_subprocess_env() -> dict[str, str]:
    """Build the environment for the subprocess.

    Strips ANTHROPIC_API_KEY (and friends) so the CLI falls back to the
    Claude.ai OAuth tokens stored in the keychain — that's the whole point
    of this backend (the user is out of API credit).

    Also forces UTF-8 stdio so high-Unicode characters in scripts (em-dashes,
    accented glyphs, fantasy names) survive on Windows. Without this the CLI
    inherits cp1252 and the JSON it emits gets mojibake'd before we parse it.
    """
    env = {
        k: v for k, v in os.environ.items() if k not in _API_MODE_ENV_VARS_TO_STRIP
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _subprocess_creationflags() -> int:
    """Windows-only flags: no console popup + isolated process group.

    CREATE_NEW_PROCESS_GROUP lets the GUI's `_reap_claude` target the
    child's process group via `Popen.kill()` so `claude`'s own children
    get reaped too, without resorting to name-based killing.
    """
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def _invoke_claude_cli(
    system_prompt: str,
    user_message: str,
    *,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> str:
    """Run the Claude CLI in --print mode and return the assistant's text.

    Raises APIConnectionError / APIResponseError / JSONParseError to match the
    shape of the API backend.

    If *process_registry* is provided, it is called with the live
    ``Popen`` object just after spawn (so the caller can terminate it)
    and again with ``None`` when the process exits.
    """
    claude_bin = _resolve_claude_cli()
    chosen_model = model or os.environ.get("VO_FORMAT_CLAUDE_CODE_MODEL") or DEFAULT_MODEL

    _dbg("=" * 60)
    _dbg(f"_invoke_claude_cli: claude_bin={claude_bin!r}")
    _dbg(f"  model={chosen_model!r}  timeout={timeout}s")
    _dbg(f"  system_prompt_len={len(system_prompt)} chars  user_msg_len={len(user_message)} chars")
    _dbg(f"  sys.platform={sys.platform!r}  python={sys.executable!r}")

    # Run from a neutral temp dir so the CLI doesn't auto-discover a
    # CLAUDE.md, .claude/ settings, or project hooks belonging to whatever
    # cwd happened to call us. This keeps the analysis context clean and
    # avoids burning quota on project bootstrap hooks.
    env = _build_subprocess_env()
    creation_flags = _subprocess_creationflags()
    _dbg(f"  creation_flags=0x{creation_flags:08x}  env_size={len(env)}")
    _dbg(f"  env has ANTHROPIC_API_KEY={'ANTHROPIC_API_KEY' in env}  ANTHROPIC_AUTH_TOKEN={'ANTHROPIC_AUTH_TOKEN' in env}")

    # Hybrid input path:
    #   short messages → pass directly as the -p argument
    #   long messages  → write to context.md, pass via --append-system-prompt-file
    #     (no tool round-trip; the script content becomes part of the system
    #     context and -p becomes a short trigger). One model turn either way.
    # Windows' CreateProcess command-line limit is 32,767 chars. Fixed overhead
    # per call (binary path + flags + system_prompt + wrapper) is ~5KB; leave
    # headroom and cap inline messages at 24KB. stdin is always DEVNULL — the
    # pipe handshake hangs forever when the parent is a windowed PyInstaller
    # exe (no console), which is the bug this whole module exists to dodge.
    INLINE_BUDGET_CHARS = 24_000
    inline_path = len(user_message) <= INLINE_BUDGET_CHARS

    with tempfile.TemporaryDirectory(prefix="vo-format-claude-") as scratch:
        scratch_path = pathlib.Path(scratch)
        _dbg(f"  scratch_cwd={scratch!r}")

        if inline_path:
            _dbg(f"  path=INLINE  (user_msg fits in {INLINE_BUDGET_CHARS} char budget)")
            mode_specific = [
                "-p", user_message,
                "--tools", "",
            ]
        else:
            context_file = scratch_path / "context.md"
            context_file.write_text(user_message, encoding="utf-8")
            _dbg(
                f"  path=FILE  wrote context.md ({context_file.stat().st_size} bytes; "
                f"user_msg over {INLINE_BUDGET_CHARS}-char inline budget)"
            )
            # --append-system-prompt-file is a real CLI flag (hidden from main
            # --help but documented in the --bare description). It loads the
            # file as additional system context, dodging both the command-line
            # length limit and the Read-tool round-trip the prior file path
            # used to need. Single turn, no tool calls.
            trigger_prompt = (
                "Analyze the voice-over script provided in the system context "
                "above and return the complete JSON analysis object described "
                "in your system instructions. The JSON must include every "
                "field listed there (archetype, characters, has_narrator, "
                "source_types, sections, detected_stage_directions, "
                "detected_sound_cues, metadata_blocks, pronunciation_flags, "
                "suggested_toggles, warnings) — populated, not omitted. "
                "Output ONLY the JSON object: no preamble, no markdown "
                "fences, no narration."
            )
            mode_specific = [
                "-p", trigger_prompt,
                "--append-system-prompt-file", str(context_file),
                "--tools", "",
            ]

        # --strict-mcp-config + empty --mcp-config: block all MCP server
        # discovery. Without this, --setting-sources user causes the CLI to
        # boot every MCP server in the user's config (context7, playwright,
        # etc.) on every preflight — ~30-60s of cold-start overhead per call
        # plus extra hang surface, even though our --tools list never
        # references MCP tools. Issue bop-scripty-w6h.
        cmd = [
            claude_bin,
            "--print",
            "--output-format", "json",
            "--system-prompt", system_prompt,
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources", "user",
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
            "--model", chosen_model,
            *mode_specific,
        ]
        _dbg(f"  cmd[0..2]={cmd[:3]!r}  argv_len={len(cmd)}")

        _dbg(f"  -> subprocess.run starting at {datetime.datetime.now().isoformat(timespec='milliseconds')}")
        t0 = time.monotonic()

        # Start the process so the caller can terminate it mid-flight.
        popen_kwargs: dict[str, Any] = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=scratch,
            env=env,
            creationflags=creation_flags,
        )
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        popen = subprocess.Popen(cmd, **popen_kwargs)
        if process_registry:
            try:
                process_registry(popen)
            except Exception:
                pass

        try:
            try:
                stdout, stderr_text = popen.communicate(timeout=timeout)
                elapsed = time.monotonic() - t0
                _dbg(f"  <- subprocess communicated in {elapsed:.1f}s  rc={popen.returncode}")
                _dbg(f"     stdout_len={len(stdout or '')}  stderr_len={len(stderr_text or '')}")
                if stderr_text:
                    _dbg(f"     stderr_tail={stderr_text.strip()[-400:]!r}")
            except subprocess.TimeoutExpired as e:
                _dbg(f"  !! TimeoutExpired after {time.monotonic() - t0:.1f}s")
                popen.kill()
                stdout, stderr_text = popen.communicate()
                raise APIConnectionError(
                    f"Claude CLI timed out after {timeout}s"
                ) from e
            except OSError as e:
                _dbg(f"  !! OSError: {e!r}")
                raise APIConnectionError(f"Failed to launch Claude CLI: {e}") from e
        finally:
            if process_registry:
                try:
                    process_registry(None)
                except Exception:
                    pass

    stdout = (stdout or "").strip()
    stderr_tail = (stderr_text or "").strip()[-500:]

    # Parse the wrapper first so we can surface structured errors (the CLI
    # emits the envelope on stdout even when API calls fail; stderr is
    # usually empty in that case).
    wrapper: dict[str, Any] | None = None
    if stdout:
        try:
            loaded = json.loads(stdout)
            if isinstance(loaded, dict):
                wrapper = loaded
        except json.JSONDecodeError:
            wrapper = None

    if popen.returncode != 0:
        # API errors (credit exhaustion, rate
        # limits, validation failures) come back as the JSON envelope with
        # is_error=true. The useful message lives in envelope["result"]
        # and the status in envelope["api_error_status"]. Surface those
        # so the user sees "Credit balance is too low" instead of "exited 1".
        if wrapper is not None and wrapper.get("is_error"):
            msg = wrapper.get("result") or wrapper.get("subtype") or "unknown error"
            api_status = wrapper.get("api_error_status")
            status_part = f" (api_error_status={api_status})" if api_status else ""
            raise APIResponseError(f"Claude CLI error: {msg}{status_part}")
        raise APIResponseError(
            f"Claude CLI exited {popen.returncode}: {stderr_tail or '<no stderr>'}"
        )

    if not stdout:
        raise JSONParseError("Claude CLI returned empty output")

    if wrapper is None:
        # Wrapper wasn't parseable JSON but the CLI exited cleanly — pass
        # the raw stdout downstream. The prompts demand JSON, so the body
        # alone may still be valid for _extract_json's lenient parser.
        log.warning("Claude CLI returned non-JSON wrapper: %s", stdout[:300])
        return stdout

    if wrapper.get("is_error"):
        msg = wrapper.get("result") or wrapper.get("subtype") or "unknown error"
        api_status = wrapper.get("api_error_status")
        status_part = f" (api_error_status={api_status})" if api_status else ""
        raise APIResponseError(f"Claude CLI error: {msg}{status_part}")

    result = wrapper.get("result")
    if not isinstance(result, str) or not result.strip():
        raise JSONParseError(
            f"Claude CLI wrapper had no 'result' string: {stdout[:300]}"
        )

    return result


# ---------------------------------------------------------------------------
# Public API (matches preflight.py)
# ---------------------------------------------------------------------------


def run_preflight(
    script_text: str,
    filename: str,
    api_key: str | None = None,  # accepted for signature parity; ignored
    model: str | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> PreflightResult:
    """Run preflight analysis via the Claude Code CLI.

    Signature parity with `preflight.run_preflight` so the dispatcher can swap
    backends without rewriting call sites. `api_key` is accepted but ignored:
    auth comes from the CLI's OAuth tokens.
    """
    user_message = _build_preflight_message(script_text, filename)
    response_text = _invoke_claude_cli(
        PREFLIGHT_SYSTEM_PROMPT,
        user_message,
        model=model,
        timeout=timeout,
        process_registry=process_registry,
    )
    data = _extract_json(response_text)
    return _validate_and_build(data)


def run_pronunciation(
    words: list[str],
    script_context: str,
    api_key: str | None = None,
    model: str | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> dict[str, str]:
    """Generate phonetic spellings via the Claude Code CLI.

    Non-fatal: returns {} on any failure, matching the API path's behavior.
    """
    if not words:
        return {}

    user_message = _build_pronunciation_message(words, script_context)

    try:
        response_text = _invoke_claude_cli(
            PRONUNCIATION_SYSTEM_PROMPT,
            user_message,
            model=model,
            timeout=timeout,
            process_registry=process_registry,
        )
    except PreflightError as e:
        log.warning("Pronunciation guide via Claude Code CLI failed: %s", e)
        return {}

    try:
        data = _extract_json(response_text)
    except JSONParseError as e:
        log.warning("Could not parse pronunciation guide response: %s", e)
        return {}

    result: dict[str, str] = {}
    for word, phonetic in data.items():
        if isinstance(phonetic, str):
            result[str(word)] = phonetic
    return result


def run_diagnostic(
    script_text: str,
    preflight_result: PreflightResult,
    formatted_blocks: list[FormattedBlock],
    api_key: str | None = None,
    model: str | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    process_registry: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> DiagnosticReport:
    """Run a diagnostic review via the Claude Code CLI.

    Returns a stub report on failure (matching the API path).
    """
    user_message = _build_diagnostic_message(
        script_text, preflight_result, formatted_blocks
    )

    def _empty_report(summary: str) -> DiagnosticReport:
        return DiagnosticReport(
            misclassified_lines=[],
            missed_characters=[],
            missed_stage_directions=[],
            missed_sound_cues=[],
            unstripped_metadata=[],
            unhandled_patterns=[],
            summary=summary,
        )

    try:
        response_text = _invoke_claude_cli(
            DIAGNOSTIC_SYSTEM_PROMPT,
            user_message,
            model=model,
            timeout=timeout,
            process_registry=process_registry,
        )
    except PreflightError as e:
        return _empty_report(f"Diagnostic CLI call failed: {e}")

    try:
        data = _extract_json(response_text)
    except JSONParseError:
        return _empty_report(f"Could not parse diagnostic response: {response_text[:300]}")

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
