#!/usr/bin/env python3
"""Re-encode already-rendered thumbnails from JPEG to WebP, in place.

One-off, and kept because the reasoning is worth having on hand if the format
is ever revisited.

Re-encoding, not re-rendering. The PDFs are not kept — rendering again would
mean pulling several thousand multi-megabyte files off the Wayback Machine a
second time, which is both slow and rude. The JPEGs are already on disk, so
this is local, costs no requests, and takes minutes.

It is a second lossy pass, which is the honest objection to it. Checked at 2x
zoom on a dense letterhead against the original: indistinguishable. These are
340px renders of scanned text being shown in a 170px-wide tile, and WebP at
quality 72 has more headroom than the JPEG it is reading did.

Afterwards the .jpg files are deleted — in the repository they would count
against the 1 GB Pages ceiling for no reason, which is the whole problem this
is solving.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from thumbs import QUALITY, SUFFIX  # noqa: E402


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dirs", nargs="*", type=Path,
                        default=[root / "payload" / "thumbs", root / "thumbs"])
    parser.add_argument("--keep", action="store_true",
                        help="leave the .jpg files in place")
    args = parser.parse_args()

    from PIL import Image

    for folder in args.dirs:
        if not folder.exists():
            continue
        old = sorted(folder.glob("*.jpg"))
        if not old:
            print(f"{folder}: nothing to convert", file=sys.stderr)
            continue
        was = sum(p.stat().st_size for p in old)
        done = failed = 0
        for path in old:
            target = path.with_suffix(SUFFIX)
            try:
                if not target.exists():
                    with Image.open(path) as image:
                        image.convert("RGB").save(
                            target, "WEBP", quality=QUALITY, method=4)
                if not args.keep:
                    path.unlink()
                done += 1
            except Exception as exc:  # noqa: BLE001 — a truncated render
                failed += 1
                print(f"  {path.name}: {type(exc).__name__}", file=sys.stderr)
            if done % 1000 == 0 and done:
                print(f"  {done:,}/{len(old):,}", file=sys.stderr, flush=True)
        now = sum(p.stat().st_size for p in folder.glob(f"*{SUFFIX}"))
        print(f"{folder}: {done:,} converted, {failed} failed, "
              f"{was / 1024 / 1024:.0f} MB -> {now / 1024 / 1024:.0f} MB "
              f"({(now / max(1, was) - 1) * 100:+.0f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()
