"""Parse marking schemes from JSON or simple text lines."""

from __future__ import annotations

import json
import re
from typing import Mapping

from .geometry import LETTERS, NUM_QUESTIONS

DEFAULT_KEY: dict[int, str] = {
    1: "A", 2: "C", 3: "C", 4: "C", 5: "C",
    6: "A", 7: "C", 8: "C", 9: "B", 10: "A",
    11: "B", 12: "A", 13: "B", 14: "B", 15: "D",
    16: "B", 17: "D", 18: "A", 19: "D", 20: "B",
    21: "A", 22: "C", 23: "D", 24: "D", 25: "B",
    26: "B", 27: "B", 28: "B", 29: "C", 30: "C",
    31: "D", 32: "C", 33: "C", 34: "B", 35: "D",
    36: "A", 37: "A", 38: "D", 39: "B", 40: "A",
}

_LINE_RE = re.compile(
    r"^\s*(?:Q\s*)?(\d{1,3})\s*[:\=\.\-\s]\s*([A-Da-d])\s*$"
)
_PAIR_RE = re.compile(r"(\d{1,3})\s*([A-Da-d])")


def parse_marking_scheme(text: str, expect_n: int = NUM_QUESTIONS) -> dict[int, str]:
    """
    Accept:
      - JSON object {"1":"A","2":"C",...} or {"1":"A",...} with int-like keys
      - lines like `1:A` / `1 = A` / `Q1 A`
      - compact tokens `1A 2C 3C ...`
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("marking scheme is empty")

    key: dict[int, str] = {}

    if raw.startswith("{") or raw.startswith("["):
        data = json.loads(raw)
        if isinstance(data, list):
            # [{"q":1,"answer":"A"}] or ["A","C",...]
            if data and isinstance(data[0], dict):
                for item in data:
                    q = int(item.get("q") or item.get("question"))
                    a = str(item.get("answer") or item.get("key") or item.get("a")).upper()
                    key[q] = a
            else:
                for i, a in enumerate(data, start=1):
                    key[i] = str(a).upper()
        elif isinstance(data, Mapping):
            for k, v in data.items():
                key[int(k)] = str(v).strip().upper()
        else:
            raise ValueError("unsupported JSON marking scheme shape")
    else:
        lines = [ln for ln in raw.replace(",", "\n").splitlines() if ln.strip()]
        parsed_any = False
        for ln in lines:
            m = _LINE_RE.match(ln)
            if m:
                key[int(m.group(1))] = m.group(2).upper()
                parsed_any = True
        if not parsed_any:
            for m in _PAIR_RE.finditer(raw):
                key[int(m.group(1))] = m.group(2).upper()

    if not key:
        raise ValueError("could not parse any Q→answer pairs from marking scheme")

    cleaned: dict[int, str] = {}
    for q, a in sorted(key.items()):
        if a not in LETTERS:
            raise ValueError(f"invalid answer for Q{q}: {a!r} (want A–D)")
        if not 1 <= q <= expect_n:
            # allow extra keys but ignore beyond expect_n for v1
            if q > expect_n:
                continue
        cleaned[q] = a

    missing = [q for q in range(1, expect_n + 1) if q not in cleaned]
    if missing:
        raise ValueError(
            f"marking scheme missing {len(missing)} question(s): "
            + ", ".join(f"Q{q}" for q in missing[:12])
            + ("…" if len(missing) > 12 else "")
        )
    return cleaned
