# -*- coding: utf-8 -*-
"""Fill every {{PLACEHOLDER}} from credits.json (author, series, GitHub user, repo name ...)
and the numbers from results/result.json, then rebuild play/js/credits.js.

    python -m tools.fill_credits            # render tools/templates/*.tmpl -> repo root
    python -m tools.fill_credits --release  # ... and rebuild release/*.zip

Templates live in tools/templates/ (README.md.tmpl -> README.md, ...). Re-running is safe:
outputs are always regenerated from the templates. Finally it lists any placeholder that is
still unfilled anywhere in the repository.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "tools" / "templates"
PH = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
TEXT_EXT = {".md", ".txt", ".cff", ".json", ".js", ".html", ".css", ".py", ".ini", ".tmpl", ""}


def numbers_from_results():
    """Numbers for README / CITATION, read from the data files that the page also shows."""
    import re
    d = ROOT / "data"
    if not (d / "thresholds.json").exists():
        return {}
    th = json.loads((d / "thresholds.json").read_text(encoding="utf-8"))
    rep = (d / "calibration_report.md").read_text(encoding="utf-8")
    voc = json.loads((d / "model_vocab.json").read_text(encoding="utf-8"))
    g2p = json.loads((d / "g2p_sentences.json").read_text(encoding="utf-8"))
    from tools.build import parse_calibration_report
    cal = parse_calibration_report(rep)
    real = [t for t, _ in voc["vocab_order"] if t not in ("|", " ") and t[0] not in "[<{"]
    n_sent = sum(len(v) for v in g2p["levels"].values())
    phones = [len(s["phones"]) for v in g2p["levels"].values() for s in v]
    anchors = ", ".join(f"{g:g}→{s:g}" for g, s in th["gop_to_score_anchors"])
    return {
        "N_UTTS": f"{th['n_utts']:,}", "N_PHONES": f"{th['n_phones']:,}", "N_GOOD": f"{th['n_good']:,}",
        "N_BAD": f"{th['n_bad']:,}", "AUC": f"{th['auc_good_vs_bad']}", "PEARSON": f"{th['pearson_gop_vs_expert']}",
        "SPEARMAN": f"{cal['spearman']}", "CAL_MINUTES": f"{cal['minutes']:g}", "CAL_DATE": th["calibrated_on"],
        "CAL_DATASET": th["dataset"], "ANCHORS": anchors, "GREEN": f"{th['level_cutoffs']['green']}",
        "YELLOW": f"{th['level_cutoffs']['yellow']}", "VOCAB_SIZE": str(voc["size"]), "N_PHONE_TOKENS": str(len(real)),
        "N_SENTENCES": str(n_sent), "MIN_PHONES": str(min(phones)), "MAX_PHONES": str(max(phones)),
        "G2P_VERSION": g2p.get("g2p_en_version", "?"),
    }


def render(values):
    written = []
    for tpl in sorted(TEMPLATES.glob("*.tmpl")):
        text = tpl.read_text(encoding="utf-8")
        text = PH.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)
        out = ROOT / tpl.name[:-len(".tmpl")]
        out.write_text(text, encoding="utf-8", newline="\n")
        written.append(out)
    return written


def leftover_placeholders():
    left = []
    for p in ROOT.rglob("*"):
        if (not p.is_file() or ".git" in p.parts or "templates" in p.parts or p.suffix not in TEXT_EXT
                or p.resolve() == Path(__file__).resolve()):
            continue
        if p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in PH.finditer(text):
            left.append((p.relative_to(ROOT).as_posix(), m.group(0)))
    return left


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", action="store_true")
    args = ap.parse_args(argv)
    if not TEMPLATES.is_dir():
        raise SystemExit("tools/templates/ not found (run this inside the published repository)")
    credits = json.loads((ROOT / "credits.json").read_text(encoding="utf-8"))
    values = {k: v for k, v in credits.items() if not k.startswith("_")}
    values.update(numbers_from_results())
    for p in render(values):
        print("wrote", p.relative_to(ROOT).as_posix())
    from tools import build
    build.main(["--release"] if args.release else [])
    left = leftover_placeholders()
    if left:
        print("UNFILLED placeholders:")
        for f, ph in left:
            print("  ", f, ph)
    else:
        print("no unfilled placeholders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
