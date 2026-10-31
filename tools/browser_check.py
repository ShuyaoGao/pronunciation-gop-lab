# -*- coding: utf-8 -*-
"""Desktop acceptance of play/index.html in the locally installed Edge (headless) at 1920x1080 and
1366x768: the page's built-in self-test, the real-time recording path (fake microphone), the exit button,
and optionally screenshots.

    python -m tools.browser_check                   # self-test (both sizes) + recording path + exit
    python -m tools.browser_check --shots docs/img  # also write the desktop screenshots

Driver: Playwright with the installed Edge (channel "msedge"; nothing is downloaded), under the
machine-wide Edge lock and free-memory guard of tools/mobile_check.py, so only one Edge runs on this
machine at a time.  Chromium's fake microphone (--use-fake-device-for-media-stream) is used, so no real
audio is ever recorded.  Phone sizes are checked separately by tools/mobile_check_gop.py.

The self-test (play/js/app.js `selftest()`) drives every mode and asserts no JS errors and no horizontal
overflow; its JSON result is read back from <pre id="selftest">.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "play" / "index.html"
SIZES = [(1920, 1080), (1366, 768)]
FAKE_MIC_ARGS = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
SHOTS = [
    ("demo_sub_1920x1080.png", (1920, 1080), "shot=sub"),
    ("mic_1920x1080.png", (1920, 1080), "shot=mic"),
    ("demo_del_1366x768.png", (1366, 768), "shot=del"),
]


def playwright_available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


class Edge:
    """One headless Edge (through Playwright) under the machine-wide lock; use as a context manager."""

    def __init__(self):
        self._pwcm = self._pw = self._browser = self._lock = None

    def __enter__(self):
        sys.path.insert(0, str(ROOT))
        from tools.mobile_check import EdgeLock
        from playwright.sync_api import sync_playwright
        self._lock = EdgeLock().__enter__()
        self._pwcm = sync_playwright()
        self._pw = self._pwcm.__enter__()
        self._browser = self._pw.chromium.launch(channel="msedge", headless=True, args=FAKE_MIC_ARGS)
        return self

    def __exit__(self, *a):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pwcm:
                self._pwcm.__exit__(None, None, None)
            if self._lock:
                self._lock.__exit__(None, None, None)

    def page(self, size, query):
        ctx = self._browser.new_context(viewport={"width": size[0], "height": size[1]}, locale="zh-CN")
        page = ctx.new_page()
        page.goto(INDEX.as_uri() + "?" + query)
        return ctx, page

    def read_result(self, size, query, timeout_ms):
        ctx, page = self.page(size, query)
        try:
            page.wait_for_function("document.getElementById('selftest').textContent.length > 0", timeout=timeout_ms)
            return json.loads(page.evaluate("document.getElementById('selftest').textContent"))
        except Exception as e:  # did not finish
            return {"ok": False, "errors": ["result not produced: %r" % (e,)][:1]}
        finally:
            ctx.close()

    def selftest(self, size):
        return self.read_result(size, "selftest=1", 180000)

    def mic_check(self, kind):
        """kind='stream': synthetic tone through a MediaStream; kind='device': the browser's fake microphone."""
        return self.read_result((1366, 768), "mictest=" + kind, 60000)

    def exit_check(self):
        ctx, page = self.page((1366, 768), "exittest=1")
        try:
            page.wait_for_function("/已停止运行/.test(document.body.textContent)", timeout=15000)
            return "已停止运行，所有计算都已结束，可以直接关闭这个标签页" in page.evaluate("document.body.textContent")
        except Exception:
            return False
        finally:
            ctx.close()

    def screenshot(self, size, query, path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        ctx, page = self.page(size, query + "&nohelp=1")
        try:
            page.wait_for_function("window.__gop && window.__gop.ready", timeout=60000)
            page.wait_for_timeout(900)
            page.screenshot(path=str(path), full_page=False)
        finally:
            ctx.close()
        return path.exists() and path.stat().st_size > 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="directory for screenshots")
    args = ap.parse_args(argv)
    if not playwright_available():
        print("playwright not installed (pip install playwright): browser check NOT run")
        return 2
    ok = True
    with Edge() as edge:
        print("browser:", edge._browser.version, "(Edge through Playwright)")
        for size in SIZES:
            r = edge.selftest(size)
            good = bool(r.get("ok")) and not r.get("errors")
            ok = ok and good
            print(f"[{size[0]}x{size[1]}] {'PASS' if good else 'FAIL'}: {json.dumps(r, ensure_ascii=False)[:600]}", flush=True)
        for kind in ("stream", "device"):
            m = edge.mic_check(kind)
            good = m.get("ok") is True or (kind == "device" and m.get("gotMic") is False)
            print(f"  recording path ({kind}): {'PASS' if m.get('ok') else ('NOT AVAILABLE' if good else 'FAIL')}: "
                  f"{json.dumps(m, ensure_ascii=False)[:300]}", flush=True)
            ok = ok and good
        ex = edge.exit_check()
        print(f"  exit button: {'PASS' if ex else 'FAIL'}", flush=True)
        ok = ok and ex
        if args.shots:
            d = Path(args.shots)
            if not d.is_absolute():
                d = ROOT / d
            for name, size, q in SHOTS:
                done = edge.screenshot(size, q, d / name)
                print(f"  screenshot {name}: {'ok' if done else 'FAILED'}", flush=True)
                ok = ok and done
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
