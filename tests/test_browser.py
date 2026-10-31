# -*- coding: utf-8 -*-
"""Headless Edge (through Playwright) acceptance of play/index.html at the desktop sizes 1920x1080 and
1366x768: built-in self-test, recording path with a fake microphone, exit button.

Opt-in (it starts a real browser): GOP_BROWSER_TEST=1 python -m pytest tests/test_browser.py
Same as: python -m tools.browser_check
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import browser_check  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("GOP_BROWSER_TEST") != "1",
                                reason="set GOP_BROWSER_TEST=1 to run the headless browser test")


@pytest.fixture(scope="module")
def edge():
    if not browser_check.playwright_available():
        pytest.skip("playwright not installed")
    with browser_check.Edge() as e:
        yield e


@pytest.mark.parametrize("size", browser_check.SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_selftest_no_js_errors(edge, size):
    r = edge.selftest(size)
    assert r.get("errors") == [], r
    assert r.get("ok") is True, r
    assert r["viewport"] == [size[0], size[1]] or r["viewport"][0] <= size[0]


def test_exit_button_stops_page(edge):
    assert edge.exit_check()


@pytest.mark.parametrize("kind", ["stream", "device"])
def test_recording_path(edge, kind):
    """Capture -> resample -> analysis in real time; 'stream' = synthetic tone, 'device' = fake microphone."""
    r = edge.mic_check(kind)
    if kind == "device" and r.get("gotMic") is False:
        pytest.skip("fake microphone not available in this browser")
    assert r.get("ok") is True, r
