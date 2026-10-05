from __future__ import annotations

import pytest

from wbs import config
from wbs.tools import find_tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolated workspace per test; settings cache is reset so WBS_WORKSPACE takes effect."""
    ws = tmp_path / "ws"
    monkeypatch.setenv("WBS_WORKSPACE", str(ws))
    monkeypatch.delenv("WBS_CONFIRM_PAID", raising=False)
    config.load_settings.cache_clear()
    yield ws
    config.load_settings.cache_clear()


requires_blender = pytest.mark.skipif(find_tool("blender") is None, reason="Blender not available")
requires_ffmpeg = pytest.mark.skipif(find_tool("ffmpeg") is None or find_tool("ffprobe") is None,
                                     reason="ffmpeg/ffprobe not available")
