"""Tests for the backend dispatcher and claude_code_backend env handling."""

from __future__ import annotations

import os

import pytest

from vo_format import claude_code_backend
from vo_format.backend import VALID_BACKENDS, resolve_backend


# ---------------------------------------------------------------------------
# resolve_backend
# ---------------------------------------------------------------------------


def test_valid_backends_tuple():
    assert "api" in VALID_BACKENDS
    assert "claude-code" in VALID_BACKENDS


def test_resolve_backend_explicit_request_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xxx")
    monkeypatch.setenv("VO_FORMAT_BACKEND", "api")
    assert resolve_backend("claude-code") == "claude-code"
    assert resolve_backend("api") == "api"


def test_resolve_backend_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_backend("nope")


def test_resolve_backend_env_var(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("VO_FORMAT_BACKEND", "claude-code")
    assert resolve_backend(None) == "claude-code"


def test_resolve_backend_env_var_rejects_unknown(monkeypatch):
    monkeypatch.setenv("VO_FORMAT_BACKEND", "bogus")
    with pytest.raises(ValueError):
        resolve_backend(None)


def test_resolve_backend_auto_prefers_api_when_key_set(monkeypatch):
    monkeypatch.delenv("VO_FORMAT_BACKEND", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xxx")
    assert resolve_backend(None) == "api"


def test_resolve_backend_auto_falls_back_to_claude_code(monkeypatch, tmp_path):
    monkeypatch.delenv("VO_FORMAT_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Plant a fake `claude` executable on PATH so shutil.which finds it.
    fake = tmp_path / ("claude.bat" if os.name == "nt" else "claude")
    fake.write_text("echo fake")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.delenv("VO_FORMAT_CLAUDE_CMD", raising=False)
    assert resolve_backend(None) == "claude-code"


# ---------------------------------------------------------------------------
# claude_code_backend._build_subprocess_env
# ---------------------------------------------------------------------------


def test_build_subprocess_env_strips_api_key_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    env = claude_code_backend._build_subprocess_env()
    assert "ANTHROPIC_API_KEY" not in env


def test_build_subprocess_env_strips_third_party_routing(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    env = claude_code_backend._build_subprocess_env()
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "CLAUDE_CODE_USE_VERTEX" not in env


def test_build_subprocess_env_strips_anthropic_auth_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-xxx")
    env = claude_code_backend._build_subprocess_env()
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_build_subprocess_env_forces_utf8_stdio(monkeypatch):
    env = claude_code_backend._build_subprocess_env()
    assert env.get("PYTHONIOENCODING") == "utf-8"
    assert env.get("PYTHONUTF8") == "1"


def test_pronunciation_returns_empty_for_empty_input():
    # Pure: short-circuits before any subprocess.
    assert claude_code_backend.run_pronunciation([], "ctx") == {}
