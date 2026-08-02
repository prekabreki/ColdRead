"""The sync client asset. The queue logic is JS, so this guards its shape."""

from __future__ import annotations


class TestSyncAsset:
    def test_the_asset_exists_and_is_the_expected_shape(self) -> None:
        from importlib.resources import files

        source = (files("vo_format.readview") / "sync.js").read_text(
            encoding="utf-8"
        )
        assert "function coldreadSync(" in source
        # Durations, never wall-clock times: the merge must not depend on any
        # device's idea of what time it is.
        assert "age_ms" in source
        # The queue outlives the page, or a booth session's marks die with it.
        assert "pending" in source
        # keepalive, or the last flush of a session is cancelled by navigation.
        assert "keepalive" in source
