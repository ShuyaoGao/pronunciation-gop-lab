# -*- coding: utf-8 -*-
"""Phone-portrait (390x844, 414x896, 360x780, 768x1024, touch) and desktop (1920x1080, 1366x768) acceptance
of play/index.html through DevTools emulation in the installed Edge (tools/mobile_check_gop.py).

Opt-in (it starts a real browser): GOP_BROWSER_TEST=1 python -m pytest tests/test_mobile.py
Same as: python -m tools.mobile_check_gop
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.skipif(os.environ.get("GOP_BROWSER_TEST") != "1",
                                reason="set GOP_BROWSER_TEST=1 to run the headless browser test")

SIZE_NAMES = ["m390x844", "m414x896", "m360x780", "t768x1024", "d1920x1080", "d1366x768"]


@pytest.fixture(scope="module")
def report():
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed")
    from tools.mobile_check import run
    from tools.mobile_check_gop import FAKE_MIC_ARGS, INDEX, scenario
    out = Path(tempfile.mkdtemp(prefix="gop_mobile_test_"))
    rep = run(INDEX.as_uri(), out, scenario=scenario, settle_ms=800, launch_args=FAKE_MIC_ARGS)
    return {s["name"]: s for s in rep["sizes"]}


@pytest.mark.parametrize("name", SIZE_NAMES)
def test_size_passes(report, name):
    s = report[name]
    assert s["fails"] == [], s["fails"]
    assert Path(s["screenshot"]).stat().st_size > 0
