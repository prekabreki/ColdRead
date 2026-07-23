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
from typing import Any

log = logging.getLogger(__name__)


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

from .models import (
    DiagnosticEntry,
    DiagnosticReport,
    FormattedBlock,
    PreflightResult,
)
from .preflight import (
    APIConnectionError,
    APIResponseError,
    DIAGNOSTIC_SYSTEM_PROMPT,
    JSONParseError,
    PREFLIGHT_SYSTEM_PROMPT,
    PRONUNCIATION_SYSTEM_PROMPT,
    PreflightError,
    _extract_json,
    _validate_and_build,
)


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
    """Windows-only flag that prevents a console window for the subprocess.

    DETACHED_PROCESS can additionally stop Node.js grandchildren from
    inheriting the parent's console handle when the CLI is run with
    active tool calls (--permission-mode acceptEdits produces real
    child I/O). For our use
    case we pass `--tools ""` — no tool calls, no chatty grandchildren
    — and DETACHED_PROCESS turned out to break the stdin pipe on
    Windows (the subprocess hangs waiting for input that never lands).
    CREATE_NO_WINDOW alone hides the popup terminal without disturbing
    Python's stdin PIPE.

    Returns 0 on non-Windows platforms; the flag constants are Windows-only.
    """
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW


def _invoke_claude_cli(
    system_prompt: str,
    user_message: str,
    *,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Run the Claude CLI in --print mode and return the assistant's text.

    Raises APIConnectionError / APIResponseError / JSONParseError to match the
    shape of the API backend.
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
        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                cwd=scratch,
                env=env,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as e:
            _dbg(f"  !! TimeoutExpired after {time.monotonic() - t0:.1f}s")
            raise APIConnectionError(
                f"Claude CLI timed out after {timeout}s"
            ) from e
        except OSError as e:
            _dbg(f"  !! OSError: {e!r}")
            raise APIConnectionError(f"Failed to launch Claude CLI: {e}") from e
        elapsed = time.monotonic() - t0
        _dbg(f"  <- subprocess.run returned in {elapsed:.1f}s  rc={proc.returncode}")
        _dbg(f"     stdout_len={len(proc.stdout or '')}  stderr_len={len(proc.stderr or '')}")
        if proc.stderr:
            _dbg(f"     stderr_tail={(proc.stderr or '').strip()[-400:]!r}")

    stdout = (proc.stdout or "").strip()
    stderr_tail = (proc.stderr or "").strip()[-500:]

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

    if proc.returncode != 0:
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
            f"Claude CLI exited {proc.returncode}: {stderr_tail or '<no stderr>'}"
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
) -> PreflightResult:
    """Run preflight analysis via the Claude Code CLI.

    Signature parity with `preflight.run_preflight` so the dispatcher can swap
    backends without rewriting call sites. `api_key` is accepted but ignored:
    auth comes from the CLI's OAuth tokens.
    """
    line_count = script_text.count("\n") + 1

    # Same sampling strategy as the API path so behavior matches.
    BUDGET_CHARS = 200_000
    analysis_text = script_text
    truncated = False
    if len(script_text) > BUDGET_CHARS:
        truncated = True
        head = int(BUDGET_CHARS * 0.50)
        mid = int(BUDGET_CHARS * 0.25)
        tail = int(BUDGET_CHARS * 0.25)
        middle_start = (len(script_text) - mid) // 2
        analysis_text = (
            f"{script_text[:head]}\n\n"
            f"[... {len(script_text) - head - mid - tail:,} characters omitted ...]\n\n"
            f"{script_text[middle_start:middle_start + mid]}\n\n"
            f"[... resuming near end of script ...]\n\n"
            f"{script_text[-tail:]}"
        )

    truncation_note = ""
    if truncated:
        truncation_note = (
            f" The script is very long ({len(script_text):,} characters) and has been "
            f"sampled (beginning, middle, end) for analysis. Line numbers are approximate."
        )

    user_message = (
        f'Analyze the following voice-over script. The script is from a file '
        f'named "{filename}" and is {line_count} lines long.{truncation_note}\n\n'
        f"<script>\n{analysis_text}\n</script>"
    )

    response_text = _invoke_claude_cli(
        PREFLIGHT_SYSTEM_PROMPT,
        user_message,
        model=model,
        timeout=timeout,
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
) -> dict[str, str]:
    """Generate phonetic spellings via the Claude Code CLI.

    Non-fatal: returns {} on any failure, matching the API path's behavior.
    """
    if not words:
        return {}

    seen: set[str] = set()
    unique_words: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            unique_words.append(w)

    word_list = ", ".join(unique_words)
    user_message = (
        f"Generate phonetic pronunciations for these words from a {script_context}:\n\n"
        f"{word_list}"
    )

    try:
        response_text = _invoke_claude_cli(
            PRONUNCIATION_SYSTEM_PROMPT,
            user_message,
            model=model,
            timeout=timeout,
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
) -> DiagnosticReport:
    """Run a diagnostic review via the Claude Code CLI.

    Returns a stub report on failure (matching the API path).
    """
    classifications: list[dict[str, Any]] = []
    for block in formatted_blocks:
        if block.source_line is not None:
            classifications.append(
                {
                    "line": block.source_line,
                    "type": block.block_type.value,
                    "text_preview": block.text[:80] if block.text else "",
                }
            )

    preflight_dict = {
        "archetype": preflight_result.archetype.value,
        "characters": [
            {"name": c.name, "line_count": c.line_count}
            for c in preflight_result.characters
        ],
        "has_narrator": preflight_result.has_narrator,
        "sections": [
            {"title": s.title, "start_line": s.start_line, "end_line": s.end_line}
            for s in preflight_result.sections
        ],
        "metadata_blocks": [
            {"type": m.type, "start_line": m.start_line, "end_line": m.end_line}
            for m in preflight_result.metadata_blocks
        ],
        "warnings": preflight_result.warnings,
    }

    user_message = (
        "Review the following formatter output for quality issues.\n\n"
        f"PREFLIGHT ANALYSIS:\n{json.dumps(preflight_dict, indent=2)}\n\n"
        f"FORMATTER CLASSIFICATIONS:\n{json.dumps(classifications, indent=2)}\n\n"
        f"ORIGINAL SCRIPT:\n<script>\n{script_text}\n</script>"
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
