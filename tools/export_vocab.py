# -*- coding: utf-8 -*-
"""Copy the phoneme model's output vocabulary (44 tokens, ~0.5 KB) into data/model_vocab.json.

    python -m tools.export_vocab [path/to/model/snapshot]

Reads vocab.json + added_tokens.json of vitouphy/wav2vec2-xls-r-300m-timit-phoneme from the local
HuggingFace cache (or the given folder). Only the token list is copied; the model weights are not.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "model_vocab.json"
MODEL_ID = "vitouphy/wav2vec2-xls-r-300m-timit-phoneme"


def find_snapshot():
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    snaps = hub / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots"
    for d in sorted(snaps.glob("*")):
        if (d / "vocab.json").exists():
            return d
    raise SystemExit("model snapshot not found in the HuggingFace cache; pass its folder as argument")


def main():
    snap = Path(sys.argv[1]) if len(sys.argv) > 1 else find_snapshot()
    vocab = json.loads((snap / "vocab.json").read_text(encoding="utf-8"))
    added = json.loads((snap / "added_tokens.json").read_text(encoding="utf-8")) if (snap / "added_tokens.json").exists() else {}
    tok = json.loads((snap / "tokenizer_config.json").read_text(encoding="utf-8"))
    order = list(vocab.items()) + [(k, v) for k, v in added.items() if k not in vocab]
    out = {"_note": f"Output vocabulary of {MODEL_ID} (Apache-2.0), copied by tools/export_vocab.py. "
                    "Order = tokenizer.get_vocab() order. The CTC blank is the pad token.",
           "model_id": MODEL_ID, "pad_token": tok.get("pad_token"), "blank_id": vocab[tok.get("pad_token")],
           "vocab_order": order, "size": max(v for _, v in order) + 1}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(order)} tokens, blank={out['blank_id']}")


if __name__ == "__main__":
    main()
