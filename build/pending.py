#!/usr/bin/env python3
"""How many documents still have no thumbnail. Counts; renders nothing.

Exists because the obvious way to ask was `thumbs.py --max 0`, and --max 0
means NO limit rather than none at all, so asking the question started a full
render. One number on stdout, nothing else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from thumbs import GIVE_UP_AFTER, SUFFIX, read_failures  # noqa: E402


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    thumbs = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "thumbs"
    failures = read_failures(root / "data" / "thumb-failures.jsonl")
    hopeless = {d for d, n in failures.items() if n >= GIVE_UP_AFTER}

    seen: set[str] = set()
    pending = 0
    with (root / "data" / "letters-parsed.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if not record["mimetype"].startswith("application/pdf"):
                continue
            digest = record.get("digest") or ""
            if not digest or digest in seen:
                continue
            seen.add(digest)
            if digest in hopeless:
                continue
            if not (thumbs / f"{digest}{SUFFIX}").exists():
                pending += 1
    print(pending)


if __name__ == "__main__":
    main()
