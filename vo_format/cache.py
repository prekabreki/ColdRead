"""Preflight cache keyed by content hash, with optional disk persistence."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from .models import (
    Archetype,
    CharacterInfo,
    MetadataBlock,
    PreflightResult,
    PronunciationFlag,
    Section,
    SourceType,
)

log = logging.getLogger(__name__)

_DEFAULT_CACHE_PATH = Path.home() / ".vo-formatter" / "preflight_cache.json"


def _result_to_dict(result: PreflightResult) -> dict:
    return dataclasses.asdict(result)


def _dict_to_result(d: dict) -> PreflightResult:
    return PreflightResult(
        archetype=Archetype(d["archetype"]),
        characters=[CharacterInfo(**c) for c in d["characters"]],
        has_narrator=d["has_narrator"],
        source_types=[SourceType(**s) for s in d["source_types"]],
        sections=[Section(**s) for s in d["sections"]],
        detected_stage_directions=d["detected_stage_directions"],
        detected_sound_cues=d["detected_sound_cues"],
        metadata_blocks=[MetadataBlock(**m) for m in d["metadata_blocks"]],
        pronunciation_flags=[PronunciationFlag(**p) for p in d["pronunciation_flags"]],
        suggested_toggles=d["suggested_toggles"],
        warnings=d["warnings"],
    )


class PreflightCache:
    """Cache PreflightResult objects by SHA-256 hash of normalized text.

    If *path* is provided (default: ~/.vo-formatter/preflight_cache.json),
    the cache is loaded from disk on init and written through on every put().
    Pass path=None for a pure in-memory cache.
    """

    def __init__(self, path: Path | None = _DEFAULT_CACHE_PATH) -> None:
        self._store: dict[str, PreflightResult] = {}
        self._path = path
        if self._path is not None:
            self._load_from_disk()

    @staticmethod
    def hash_text(normalized_text: str) -> str:
        return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

    def get(self, text_hash: str) -> PreflightResult | None:
        return self._store.get(text_hash)

    def put(self, text_hash: str, result: PreflightResult) -> None:
        self._store[text_hash] = result
        if self._path is not None:
            self._save_to_disk()

    def has(self, text_hash: str) -> bool:
        return text_hash in self._store

    def clear(self) -> None:
        self._store.clear()
        if self._path is not None:
            self._save_to_disk()

    @property
    def size(self) -> int:
        return len(self._store)

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw: dict[str, Any] = json.load(f)
            for key, val in raw.items():
                try:
                    self._store[key] = _dict_to_result(val)
                except Exception as e:
                    log.debug("Skipping malformed cache entry %s: %s", key[:8], e)
            log.debug("Preflight cache loaded: %d entries from %s", len(self._store), self._path)
        except Exception as e:
            log.warning("Could not load preflight cache from %s: %s", self._path, e)

    def _save_to_disk(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: _result_to_dict(v) for k, v in self._store.items()}
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self._path)
        except Exception as e:
            log.warning("Could not save preflight cache to %s: %s", self._path, e)
