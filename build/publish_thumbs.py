#!/usr/bin/env python3
"""Copy rendered thumbnails into the repository so Pages serves them.

Why not the archive.org item, which is where they belong
--------------------------------------------------------
Because it will not take them. Every file uploaded to an item queues an
``archive.php`` task, and the item rations how many may be queued at once::

    Please reduce your request rate. - bucket_tasks_queued exceeds rationed
    amount

Measured on this item: 34 tasks queued and draining at roughly five a minute.
That is about half a day for the four thousand thumbnails already rendered,
and months for all 48,102 — the upload, not the rendering, becomes the thing
that cannot finish. Bundling them into a zip is the documented way out and is
how the North Korea explorer serves its screenshots, but a zip uploaded here
returns 200 from S3 and then never appears on the item, twice, with the queue
empty. Until that is understood, the item cannot hold the pictures.

GitHub Pages can, up to a point, and the point is measured rather than
guessed. Published sites "may be no larger than 1 GB", and these average 26 KB,
so the cap arrives at roughly 40,000 of the 48,102 — about 83% coverage. The
budget below stops well short of that, because a Pages site that breaches the
limit is not throttled but may stop being served.

So this is a staging post and not a home. ``thumb_base`` is one field in
index.json: when the item will take a bundle, point it back and delete the
folder. Everything else about the ETD arrangement is unchanged — the per-member
data is still streamed from the item, which is the part that actually needed
the CORS header.

What to do when the budget runs out
-----------------------------------
Re-compress rather than re-render. The PDFs are not kept, so rendering again
means downloading several thousand multi-megabyte files from Wayback a second
time; the JPEGs are right here. Dropping quality takes the average under 20 KB,
which fits the whole corpus inside the cap.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from thumbs import SUFFIX  # noqa: E402

# Measured, not guessed. Everything this site publishes that is not a
# thumbnail — the page, index.json, the per-member data, the search index, the
# build scripts and the docs — comes to 20.4 MB, which leaves 1,004 MB of the
# 1 GB Pages ceiling. 940 keeps a 64 MB margin.
#
# The first version of this guessed 820 and then 760 on an assumed 26 KB
# average. The real average is 23.6 KB, so the earlier budget was giving away
# about 12,000 thumbnails for nothing: 940 MB holds roughly 41,000 of the
# 48,113 distinct documents, which is most of the corpus rather than two
# thirds of it.
#
# Not a tuning knob beyond that. The ceiling is a hard limit — published sites
# "may be no larger than 1 GB" — and a site that breaches it may simply stop
# being served, so the margin is there to absorb the data growing, not to be
# spent on more pictures.
DEFAULT_BUDGET_MB = 940


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=root / "payload" / "thumbs",
                        help="where thumbs.py renders")
    parser.add_argument("--out", type=Path, default=root / "thumbs",
                        help="served by Pages from the repository root")
    parser.add_argument("--budget-mb", type=float, default=DEFAULT_BUDGET_MB)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"nothing rendered yet at {args.source}")
    args.out.mkdir(parents=True, exist_ok=True)

    have = {path.name for path in args.out.glob(f"*{SUFFIX}")}
    carried = sum(path.stat().st_size for path in args.out.glob(f"*{SUFFIX}"))
    source = sorted(args.source.glob(f"*{SUFFIX}"))

    budget = args.budget_mb * 1024 * 1024
    copied = skipped = 0
    added = 0
    stopped = False
    for path in source:
        if path.name in have:
            skipped += 1
            continue
        size = path.stat().st_size
        if carried + added + size > budget:
            stopped = True
            break
        if not args.dry_run:
            shutil.copy2(path, args.out / path.name)
        added += size
        copied += 1

    total = carried + added
    print(f"{len(source):,} rendered, {skipped:,} already published, "
          f"{copied:,} copied", file=sys.stderr)
    print(f"  {total / 1024 / 1024:,.0f} MB of a {args.budget_mb:,.0f} MB "
          f"budget ({total / budget * 100:.0f}%)", file=sys.stderr)
    if stopped:
        print("  STOPPED AT THE BUDGET. The rest need the item to take a "
              "bundle, or a smaller thumbnail — see the module docstring.",
              file=sys.stderr)
    if args.dry_run:
        print("  (dry run, nothing written)", file=sys.stderr)


if __name__ == "__main__":
    main()
