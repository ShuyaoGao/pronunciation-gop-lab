# -*- coding: utf-8 -*-
"""play/ must work from file:// with no install and no network, stay small, ship no audio,
label the simulated data, and carry the phone-portrait UI parts (series rule, section 8)."""
import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAY = ROOT / "play"
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".webm", ".opus"}


def _texts():
    for p in PLAY.rglob("*"):
        if p.is_file() and p.suffix in (".html", ".js", ".css"):
            yield p, p.read_text(encoding="utf-8")


def test_ascii_paths():
    for p in PLAY.rglob("*"):
        assert p.relative_to(PLAY).as_posix().isascii(), p


def test_scripts_and_styles_are_local_and_exist():
    html = (PLAY / "index.html").read_text(encoding="utf-8")
    srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    hrefs = re.findall(r'<link[^>]*\shref="([^"]+)"', html)
    assert srcs and hrefs and 'type="module"' not in html
    for s in srcs + hrefs:
        assert not re.match(r"^[a-z]+:", s), s
        assert (PLAY / s).is_file(), s
    assert srcs.index("data/app_data.js") < srcs.index("js/app.js")


def test_no_network_no_module_no_service_worker():
    for p, t in _texts():
        assert not re.search(r"https?://", t), f"URL in {p}"
        for bad in ("fetch(", "XMLHttpRequest", "serviceWorker", "import(", "@import", "@font-face", 'type="module"'):
            assert bad not in t, f"{bad} in {p}"


def test_no_audio_anywhere():
    """No recordings are published."""
    for d in ("play", "data", "docs"):   # (publish/ is also checked by tools/make_publish.py)
        base = ROOT / d
        if base.exists():
            assert not [p for p in base.rglob("*") if p.suffix.lower() in AUDIO_EXT], d


def test_simulated_data_is_labelled():
    cfg = (PLAY / "js" / "config.js").read_text(encoding="utf-8")
    app = (PLAY / "js" / "app.js").read_text(encoding="utf-8")
    sim = (PLAY / "js" / "simulate.js").read_text(encoding="utf-8")
    assert "模拟数据：后验矩阵由程序生成，不是真实录音，也不是真实模型的输出" in cfg
    assert "CFG.simBadge" in app and app.count("模拟数据") >= 3
    assert "NOT a real recording and NOT real model output" in sim
    assert "不是 AI 模型" in app


def test_required_ui_parts():
    html = (PLAY / "index.html").read_text(encoding="utf-8")
    for needle in ('data-mode="demo"', 'data-mode="mic"', 'data-mode="chat"', 'data-mode="practice"',
                   'id="btn-exit"', 'id="btn-help"', 'id="ck-big"', 'id="foot"', 'id="panel"'):
        assert needle in html, needle
    app = (PLAY / "js" / "app.js").read_text(encoding="utf-8")
    assert "已停止运行，所有计算都已结束，可以直接关闭这个标签页" in app and "window.close()" in app
    assert "getTracks().forEach(function (t) { t.stop(); })" in app      # exit releases the microphone


def test_phone_portrait_ui_parts():
    """Series rule section 8: one-row header with a menu, collapsed panel with a full-screen sheet and a
    return button, rotate hint, touch illustration in the help, >= 44 px targets, no double-tap zoom."""
    html = (PLAY / "index.html").read_text(encoding="utf-8")
    css = (PLAY / "css" / "app.css").read_text(encoding="utf-8")
    app = (PLAY / "js" / "app.js").read_text(encoding="utf-8")
    for needle in ('id="menu-btn"', 'id="menu"', 'id="menu-exit"', 'id="menu-help"', 'id="panel-btn"', 'id="panel-close"',
                   'id="rotate-hint"', 'id="rot-ok"', 'class="help-touch"', 'class="keys-list help-kbd"', 'class="help-body"',
                   'class="help-actions"', 'id="app-title-short"', 'viewport-fit=cover'):
        assert needle in html, needle
    assert html.count('class="short"') == 4 and html.count('class="full"') == 4
    for rule in ("body.touchp", "body.touchl", "sheet-panel", "env(safe-area-inset-bottom)", "100dvh", "touch-action: none",
                 "min-height: 44px", "user-select: none", ".help-kbd { display: none; }", ".hscroll"):
        assert rule in css, rule
    assert "classifyTouch" in app and "w <= 980" in app and "openSheet" in app and "rot-ok" in app
    assert "visibilitychange" in app and "contextmenu" in app and "navigator.vibrate" in app
    assert "麦克风权限" in app and "选择录音文件" in app     # explicit message when the microphone is denied


def test_credits_filled():
    t = (PLAY / "js" / "credits.js").read_text(encoding="utf-8")
    data = json.loads(t[t.index("{"): t.rindex("}") + 1])
    assert data["AUTHOR"] == "Shuyao Gao" and "aSSIST University" in data["UNIVERSITY_EN"] and data["UNIVERSITY_KO"]
    assert data["AUTHOR_EMAIL"] == "gaoshuyao.mk@gmail.com"
    assert "{{" not in t
    assert "AUTHOR_EMAIL" in (PLAY / "js" / "app.js").read_text(encoding="utf-8")   # footer shows the contact e-mail


def test_root_entry_and_nojekyll():
    """Release zip / repository root: index.html jumps to play/index.html; .nojekyll in the published repository."""
    tmpl = ROOT / "tools" / "publish_templates" / "index.html.tmpl"
    idx = ROOT / "index.html"
    src = idx if idx.exists() else tmpl
    assert src.exists()
    t = src.read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in t and "url=play/index.html" in t and 'href="play/index.html"' in t
    if not (ROOT / "tools" / "publish_templates").exists():     # the published repository
        assert (ROOT / ".nojekyll").exists() and idx.exists() and "{{" not in t


def test_size_budget():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in PLAY.rglob("*"):
            if p.is_file():
                z.write(p, "play/" + p.relative_to(PLAY).as_posix())
    assert len(buf.getvalue()) <= 15 * 1024 * 1024


def test_calibration_numbers_on_page_come_from_data():
    t = (PLAY / "data" / "app_data.js").read_text(encoding="utf-8")
    d = json.loads(t[t.index("{"): t.rindex("}") + 1])
    th = json.loads((ROOT / "data" / "thresholds.json").read_text(encoding="utf-8"))
    for k in ("n_utts", "n_phones", "n_good", "n_bad", "auc_good_vs_bad", "pearson_gop_vs_expert", "gop_to_score_anchors"):
        assert d["thresholds"][k] == th[k]
    assert (th["n_utts"], th["n_phones"], th["auc_good_vs_bad"], th["pearson_gop_vs_expert"]) == (1200, 21194, 0.878, 0.4892)
    assert d["calibration"]["spearman"] == 0.3881 and len(d["calibration"]["per_phone"]) == 36
