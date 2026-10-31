# -*- coding: utf-8 -*-
"""Phone-portrait and desktop acceptance for play/index.html (series rule "section 8", v2).

    python -m tools.mobile_check_gop                   # all 6 sizes, screenshots to a temp dir
    python -m tools.mobile_check_gop --shots docs/img  # also copy the 390x844 shot to docs/img/mobile_390x844.png

Drives the locally installed Edge through Playwright (channel "msedge") and emulates the devices through the
DevTools protocol (viewport, DPR, mobile, touch), never through --window-size.  Sizes: 390x844, 414x896,
360x780 (DPR 3), 768x1024 (DPR 2) with touch, plus desktop 1920x1080 and 1366x768.  The driver
(tools/mobile_check.py: machine-wide Edge lock, free-memory guard, overflow + JS-error checks, screenshots)
is part of this repository.  Edge is started with Chromium's fake microphone, so the recording scenario never
opens a real microphone.

Phone scenario: body gets class touchp; first-visit help shows the touch illustration (no keyboard list) with
the start button inside the viewport; the header is one row (title, tabs, menu); tabs / buttons / sliders /
phone chips have >= 44 px touch targets and labels >= 14 px; tap "换一种读法" -> the heat map and the scores
change; tap the noise slider at its right end -> the number of phones judged non-standard changes; toggle the
threshold checkbox -> scores change; the process panel sits inline under the stage in demo mode and opens as a
full-screen sheet (and closes with "返回") in mic / chat mode; the menu opens and closes and reaches the help;
recording with the fake microphone through the big button works, and a denied microphone shows an explicit
message while the other modes keep working; no horizontal overflow; no JS errors.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "play" / "index.html"
JS_READY = "window.__gop && window.__gop.ready"
FAKE_MIC_ARGS = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"]


def _targets(page, sel, min_h):
    """Visible elements matching sel whose height is below min_h (as 'sel#i h=..')."""
    return page.evaluate("""([sel, minH]) => {
      const out = [];
      document.querySelectorAll(sel).forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        if (r.height < minH - 0.5) out.push(sel + '#' + i + ' h=' + r.height.toFixed(1));
      });
      return out; }""", [sel, min_h])


def _font_min(page, sel):
    return page.evaluate("""(sel) => {
      let m = 1e9;
      document.querySelectorAll(sel).forEach(el => {
        const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) return;
        m = Math.min(m, parseFloat(getComputedStyle(el).fontSize)); });
      return m; }""", sel)


def _overflow(page, w):
    return page.evaluate("""(w) => {
      const d = document.documentElement, out = [];
      if (d.scrollWidth > w + 1) out.push('document scrollWidth ' + d.scrollWidth + ' > ' + w);
      ['#top', '#stage', '#opts', '#controls', '#explain', '#panel'].forEach(s => { const el = document.querySelector(s);
        if (el && el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'auto') out.push(s + ' scrollWidth ' + el.scrollWidth + ' > ' + el.clientWidth); });
      return out; }""", w)


def _canvas_sig(page, cid):
    """Cheap signature of what is drawn on a canvas (sum over a pixel sub-sample)."""
    return page.evaluate("""(cid) => { const c = document.getElementById(cid); if (!c) return null;
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let s = 0;
      for (let i = 0; i < d.length; i += 97) s = (s + d[i] * (i % 251)) % 1000000007; return s + ':' + c.width + 'x' + c.height; }""", cid)


def _scores(page):
    return page.evaluate("window.__gop.S.res.phones.map(p => Math.round(p.score)).join(',')")


def _gops(page):
    return page.evaluate("window.__gop.S.res.phones.map(p => p.gop.toFixed(3)).join(',')")


def _level_counts(page):
    return page.evaluate("""() => { const c = {good: 0, warn: 0, bad: 0};
      window.__gop.S.res.phones.forEach(p => { c[p.level] = (c[p.level] || 0) + 1; }); return c; }""")


def _pause_demo(page, tap):
    if page.evaluate("window.__gop.S.playing"):
        tap("#b-play")
        page.wait_for_timeout(150)


def scenario(page, size):
    fails = []
    mobile = size["kind"] == "mobile"
    w, h = size["w"], size["h"]
    tap = page.tap if mobile else page.click
    page.wait_for_function(JS_READY, timeout=120000)
    page.wait_for_timeout(200)

    if not mobile:
        if not page.evaluate("document.getElementById('help').hidden"):
            page.click("#help-close")
            page.wait_for_timeout(150)
        if page.evaluate("document.body.classList.contains('touchp') || document.body.classList.contains('touchl')"):
            fails.append("desktop size got body.touchp / touchl (touch misdetected)")
        if page.evaluate("getComputedStyle(document.getElementById('rotate-hint')).display") != "none":
            fails.append("rotate hint shown on desktop")
        if page.evaluate("getComputedStyle(document.getElementById('menu-btn')).display") != "none":
            fails.append("menu button visible on desktop")
        if page.evaluate("getComputedStyle(document.getElementById('mob-bar')).display") != "none":
            fails.append("bottom panel button visible on desktop")
        if page.evaluate("document.getElementById('right').getBoundingClientRect().width") < 300:
            fails.append("right panel not visible inline on desktop")
        _pause_demo(page, tap)
        page.click("#opts .preset[data-key=mumble]")
        page.wait_for_timeout(200)
        before = _gops(page)
        page.click("#b-reroll")
        page.wait_for_timeout(300)
        if _gops(page) == before:
            fails.append("desktop: 换一种读法 did not change the GOP values")
        page.click(".tab[data-mode=mic]")
        page.wait_for_timeout(200)
        if page.evaluate("window.__gop.app.mode") != "mic":
            fails.append("click on tab did not switch mode")
        page.click(".tab[data-mode=demo]")
        page.wait_for_timeout(200)
        if page.evaluate("document.documentElement.scrollHeight > innerHeight + 1 || document.body.scrollHeight > innerHeight + 1"):
            fails.append("desktop page scrolls vertically (layout should fit the window)")
        fails += _overflow(page, w)
        return fails

    # ---------------------------------------------------------------- phone / tablet portrait
    if not page.evaluate("document.body.classList.contains('touchp')"):
        fails.append("body has no class touchp")
        return fails
    # first-visit help: touch illustration, no keyboard list, scrollable body, start button fixed inside the viewport
    if page.evaluate("document.getElementById('help').hidden"):
        fails.append("help overlay not shown on first visit")
    else:
        st = page.evaluate("""() => ({
          touch: getComputedStyle(document.querySelector('.help-touch')).display !== 'none',
          kbd: getComputedStyle(document.querySelector('.help-kbd')).display === 'none',
          scroll: getComputedStyle(document.querySelector('.help-body')).overflowY === 'auto' })""")
        if not st["touch"]:
            fails.append("help: touch illustration hidden")
        if not st["kbd"]:
            fails.append("help: keyboard list visible on touch device")
        if not st["scroll"]:
            fails.append("help: body not scrollable")
        bb = page.locator("#help-close").bounding_box()
        if not bb or bb["y"] + bb["height"] > h + 0.5 or bb["height"] < 44:
            fails.append("help: start button not fixed inside viewport / too small: %r" % (bb,))
        tap("#help-close")
        page.wait_for_timeout(150)
        if not page.evaluate("document.getElementById('help').hidden"):
            fails.append("help: tap on start did not close it")
    # header: one row (brand, tabs, menu button share one band), nothing wider than the screen
    hd = page.evaluate("""() => { const q = s => document.querySelector(s).getBoundingClientRect();
      const b = q('.brand'), t = q('#tabs'), m = q('#menu-btn'), top = q('#top');
      return {b, t, m, top: {h: top.height, bottom: top.bottom, right: top.right}}; }""")
    for k in ("b", "t", "m"):
        r = hd[k]
        if r["height"] == 0 or r["bottom"] > hd["top"]["bottom"] + 0.5 or r["right"] > w + 0.5:
            fails.append("header element %s not in the single header row: %r" % (k, r))
    if hd["top"]["h"] > 64:
        fails.append("header taller than one row: %.0f px" % hd["top"]["h"])
    # demo mode: pause, go to step 5 so that every chart is drawn
    _pause_demo(page, tap)
    tap(".step[data-step='5']")
    page.wait_for_timeout(300)
    if page.evaluate("window.__gop.S.step") != 5:
        fails.append("tap on step ⑤ did not set the step")
    # touch targets and label sizes
    for sel, mh in ((".tab", 44), ("#menu-btn", 44), ("#opts .preset", 44), ("#controls .btn", 44), ("#opts select", 44),
                    ("#rng-noise", 44), ("#stage .chip[data-ph]", 44), ("#steps .step", 40)):
        bad = _targets(page, sel, mh)
        if bad:
            fails.append("touch target < %d px: %s" % (mh, ", ".join(bad[:4])))
    fm = _font_min(page, "#opts .lab, #opts .preset, #controls .btn, #explain, .sim-badge, .transcript, .metric .lb, .wd .wl, .lt, .subcard, #steps .step")
    if fm < 14:
        fails.append("label font-size %.1f px < 14 px" % fm)
    # panel inline under the stage in demo mode (watch mode: compact drawing, page scrolls)
    pr = page.evaluate("""() => { const r = document.getElementById('right').getBoundingClientRect(), s = document.getElementById('stage').getBoundingClientRect();
      return {w: r.width, top: r.top, stageBottom: s.bottom, pos: getComputedStyle(document.getElementById('right')).position,
              bar: getComputedStyle(document.getElementById('mob-bar')).display}; }""")
    if pr["w"] < w - 40 or pr["pos"] != "static" or pr["top"] < pr["stageBottom"] - 1:
        fails.append("demo: process panel not inline under the stage: %r" % (pr,))
    if pr["bar"] != "none":
        fails.append("demo: bottom panel button should be hidden in watch mode")
    if not page.evaluate("document.documentElement.scrollHeight > innerHeight"):
        fails.append("demo: page is expected to scroll vertically (watch mode)")
    gw = page.evaluate("""() => { const wrap = document.getElementById('gop-wrap'), c = document.getElementById('cv-gop');
      return {ox: getComputedStyle(wrap).overflowX, wrapW: wrap.clientWidth, cw: c.getBoundingClientRect().width, right: wrap.getBoundingClientRect().right}; }""")
    if gw["ox"] != "auto" or gw["right"] > w + 0.5 or gw["cw"] < gw["wrapW"] - 4:
        fails.append("GOP bars not in a horizontally scrolling container: %r" % (gw,))
    # core operation 1: 换一种读法 (含糊不清 scenario) -> main chart (heat map) and GOP values change
    tap("#opts .preset[data-key=mumble]")
    page.wait_for_timeout(250)
    sig0, g0 = _canvas_sig(page, "cv-heat"), _gops(page)
    tap("#b-reroll")
    page.wait_for_timeout(350)
    sig1, g1 = _canvas_sig(page, "cv-heat"), _gops(page)
    if sig0 == sig1 or g0 == g1:
        fails.append("换一种读法: heat map / GOP values did not change (%s -> %s)" % (g0[:30], g1[:30]))
    # core operation 2: the noise slider through a touch at its right end -> judged counts change
    sl = page.locator("#rng-noise").bounding_box()
    if not sl or sl["height"] < 44:
        fails.append("noise slider touch area < 44 px: %r" % (sl,))
    else:
        c0, v0 = _level_counts(page), page.evaluate("window.__gop.S.noise")
        page.touchscreen.tap(sl["x"] + sl["width"] - 3, sl["y"] + sl["height"] / 2)
        page.wait_for_timeout(350)
        c1, v1 = _level_counts(page), page.evaluate("window.__gop.S.noise")
        if v1 <= v0 or c0 == c1:
            fails.append("noise slider: tap did not change the judged counts (noise %s -> %s, %r -> %r)" % (v0, v1, c0, c1))
    # core operation 3: threshold checkbox -> scores change
    s0 = _scores(page)
    tap("#ck-legacy")
    page.wait_for_timeout(300)
    if not page.evaluate("window.__gop.S.legacy") or _scores(page) == s0:
        fails.append("threshold toggle did not change the scores")
    tap("#ck-legacy")
    page.wait_for_timeout(200)
    fails += _overflow(page, w)
    # menu: open, help, close; open and close again with the button
    tap("#menu-btn")
    page.wait_for_timeout(150)
    if page.evaluate("document.getElementById('menu').hidden"):
        fails.append("menu did not open")
    tap("#menu-help")
    page.wait_for_timeout(150)
    if page.evaluate("document.getElementById('help').hidden") or not page.evaluate("document.getElementById('menu').hidden"):
        fails.append("menu -> help did not open help / close menu")
    tap("#help-close")
    page.wait_for_timeout(150)
    tap("#menu-btn")
    page.wait_for_timeout(120)
    opened = not page.evaluate("document.getElementById('menu').hidden")
    tap("#menu-btn")
    page.wait_for_timeout(120)
    if not opened or not page.evaluate("document.getElementById('menu').hidden"):
        fails.append("menu button does not toggle")
    # mic mode (operate mode): panel collapsed, bottom button opens the sheet full screen, 返回 closes it
    tap(".tab[data-mode=mic]")
    page.wait_for_timeout(300)
    if page.evaluate("window.__gop.app.mode") != "mic":
        fails.append("tap on tab did not switch to mic")
    if page.evaluate("document.getElementById('right').getBoundingClientRect().width") != 0:
        fails.append("mic: panel should be collapsed by default")
    pb = page.locator("#panel-btn").bounding_box()
    if not pb or pb["height"] < 44 or pb["y"] + pb["height"] > h + 0.5:
        fails.append("mic: bottom panel button missing / too small / outside viewport: %r" % (pb,))
    tap("#panel-btn")
    page.wait_for_timeout(300)
    rb = page.evaluate("""() => { const r = document.getElementById('right').getBoundingClientRect();
      const c = document.getElementById('panel-close').getBoundingClientRect(); const pos = getComputedStyle(document.getElementById('right')).position;
      return {sheet: document.body.classList.contains('sheet-panel'), pos, w: r.width, h: r.height, cy: c.bottom, ch: c.height,
              txt: document.getElementById('panel').textContent.length, cvw: document.getElementById('cv-db').getBoundingClientRect().width}; }""")
    if not rb["sheet"] or rb["pos"] != "fixed" or rb["w"] < w - 1 or rb["h"] < h - 1 or rb["txt"] < 50 or rb["cy"] > h + 0.5 or rb["ch"] < 44 or rb["cvw"] < w * 0.6:
        fails.append("panel sheet did not open full screen: %r" % (rb,))
    tap("#panel-close")
    page.wait_for_timeout(200)
    if page.evaluate("document.body.classList.contains('sheet-panel') || document.getElementById('right').getBoundingClientRect().width !== 0"):
        fails.append("panel sheet did not close")
    # recording through the big button (fake microphone; no real microphone is opened)
    rec = page.locator("#b-rec").bounding_box()
    if not rec or rec["height"] < 48:
        fails.append("record button < 48 px: %r" % (rec,))
    tap("#b-rec")
    try:
        page.wait_for_function("window.__gop.M.recording === true", timeout=8000)
        page.wait_for_timeout(1500)
        tap("#b-rec")
        page.wait_for_function("window.__gop.M.result !== null", timeout=15000)
        if not page.evaluate("window.__gop.M.result.ok"):
            fails.append("recording: analysis of the fake microphone signal reported not ok: %s" % page.evaluate("window.__gop.M.status"))
        elif not page.evaluate("/流利度/.test(document.getElementById('stage').textContent)"):
            fails.append("recording: fluency result not shown on the stage")
    except Exception as e:  # recording never started (no fake device?)
        fails.append("recording through the big button did not start: %r / %s" % (e, page.evaluate("window.__gop.M.status")))
    # denied microphone: explicit message, other modes keep working
    page.evaluate("""() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('denied'), {name: 'NotAllowedError'})); }""")
    tap("#b-rec")
    page.wait_for_timeout(500)
    msg = page.evaluate("document.getElementById('mic-status').textContent")
    if "麦克风权限" not in msg or "选择录音文件" not in msg:
        fails.append("denied microphone: no explicit message: %r" % (msg,))
    # chat mode: send through the big button, the difficulty table fits the sheet
    tap(".tab[data-mode=chat]")
    page.wait_for_timeout(250)
    page.fill("#chat-in", "I is a student and yesterday I go to the park")
    tap("#b-send")
    page.wait_for_timeout(300)
    if page.evaluate("window.__gop.C.log.length") != 2:
        fails.append("chat: tap on send did not add a message pair")
    tap("#panel-btn")
    page.wait_for_timeout(300)
    tb = page.evaluate("""() => { const t = document.querySelector('#panel .ftable'); if (!t) return null; const box = t.parentElement;
      return {ox: getComputedStyle(box).overflowX, right: box.getBoundingClientRect().right, w: t.getBoundingClientRect().width}; }""")
    if not tb or tb["ox"] != "auto" or tb["right"] > w + 0.5:
        fails.append("chat: difficulty table overflows the sheet: %r" % (tb,))
    tap("#panel-close")
    page.wait_for_timeout(200)
    # practice: checklist readable
    tap(".tab[data-mode=practice]")
    page.wait_for_timeout(250)
    if _font_min(page, "#explain .tasks li") < 14:
        fails.append("practice: task text < 14 px")
    # back to the default view for the screenshot (demo, 读错一个音, step ⑤, top of the page)
    tap(".tab[data-mode=demo]")
    page.wait_for_timeout(150)
    _pause_demo(page, tap)
    tap("#opts .preset[data-key=sub]")
    page.wait_for_timeout(150)
    sl = page.locator("#rng-noise").bounding_box()
    if sl:   # noise back to the default 0.3 for the screenshot
        page.touchscreen.tap(sl["x"] + sl["width"] * 0.3, sl["y"] + sl["height"] / 2)
        page.wait_for_timeout(200)
    tap(".step[data-step='5']")
    page.wait_for_timeout(300)
    page.evaluate("window.scrollTo(0, 0); document.getElementById('toast').classList.remove('show')")
    page.wait_for_timeout(400)
    fails += _overflow(page, w)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="copy the phone screenshot (390x844) into this directory as mobile_390x844.png")
    ap.add_argument("--out", help="directory for all screenshots + mobile_check.json (default: a temp dir)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from tools.mobile_check import run  # noqa: E402

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="gop_mobile_"))
    rep = run(INDEX.as_uri(), out, scenario=scenario, settle_ms=800, launch_args=FAKE_MIC_ARGS)
    for s in rep["sizes"]:
        print("%-11s %4dx%-4d dpr%d %s" % (s["name"], s["w"], s["h"], s["dpr"], "PASS" if not s["fails"] else "FAIL: " + " | ".join(s["fails"])))
    print("screenshots:", out)
    if args.shots:
        d = Path(args.shots)
        if not d.is_absolute():
            d = ROOT / d
        d.mkdir(parents=True, exist_ok=True)
        src = out / "m390x844.png"
        if src.exists():
            shutil.copy2(src, d / "mobile_390x844.png")
            print("wrote", (d / "mobile_390x844.png").as_posix())
    print(json.dumps({"ok": rep["ok"], "sizes": [(s["name"], not s["fails"]) for s in rep["sizes"]]}))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
