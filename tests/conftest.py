# -*- coding: utf-8 -*-
"""Put the repository root on sys.path so tests can import snake_env / model / tools."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
