#!/usr/bin/env python3
"""Upload the payload to an archive.org item, and point the page at it.

The arrangement, copied from internetarchivecanada/etd-viewer
-------------------------------------------------------------
The repository holds the page and a small index; everything bulky lives in an
archive.org item that the browser reads directly. That works because item
downloads carry ``Access-Control-Allow-Origin: *`` — verified against
``archive.org/download/etd-catalog-cardinalscholar-bsu-edu/``. Without that one
header the page would need a server, and the ETD viewer could not say "no
server, no build".

What goes where::

    index.json                  in the repository, ~160 KB, so the page paints
    payload/member/<id>.json    here, ~20 MB across 511 files
    payload/thumbs/<digest>.jpg here, ~1.3 GB when the renderer has finished

Re-running is cheap: files already on the item with the same size are skipped,
so a run after another thousand thumbnails uploads only those thousand.

Uploaded in batches, and the order matters
------------------------------------------
One call with 4,236 files in it gets 56 member files in and then::

    Please reduce your request rate. - bucket_tasks_queued exceeds rationed
    amount

The ration is on tasks QUEUED, not on bytes, so a few thousand small files
trips it where one large file would not. Hence batches with a pause, and a
long back-off when the item says so.

They are also ordered: ``member/*.json`` and ``search.json`` before any
thumbnail. Those are what the page reads to be correct — a rebuild changes
every one of them, and until they land the live index and the data it
describes disagree. A thumbnail that has not arrived is a gap in a wall of
documents; a member file that has not arrived is the wrong letters. So the
correctness-critical few megabytes go first and the gigabyte of pictures
follows, which also means an interrupted run is interrupted in the right
place.

Afterwards, rebuild with ``--item`` so ``index.json`` records the item's
download URLs rather than the local ``payload/`` paths::

    python3 build/upload_item.py --item letters-from-congress-data
    python3 build/build_site.py  --item letters-from-congress-data
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

METADATA = {
    "title": "Letters from Congress — explorer data",
    "mediatype": "data",
    "collection": "opensource",
    "subject": ["congress", "oversight", "letters", "wayback machine",
                "government documents", "united states"],
    "description": (
        "Data for the Letters from Congress explorer: letters published on "
        "sitting members of Congress's own websites and preserved by the "
        "Wayback Machine. <br><br>"
        "<b>member/&lt;bioguide&gt;.json</b> — one member's letters, as arrays "
        "whose column order is given by <tt>index.json</tt> in the viewer "
        "repository. <b>thumbs/&lt;digest&gt;.jpg</b> — the rendered first "
        "page of a letter, named by its Wayback capture digest, so a letter "
        "captured many times is stored once.<br><br>"
        "Explorer: https://github.com/internetarchivecanada/letters-from-congress"
    ),
}


def plan(payload: Path) -> list[tuple[str, Path]]:
    """(remote name, local path) for everything that should be on the item.

    Walks the whole payload rather than a list of known subdirectories. The
    first version named "member" and "thumbs" explicitly, so when search.json
    was added beside them the uploader reported "0 to upload" and the live
    site searched against a file that was never there.
    """
    files: list[tuple[str, Path]] = []
    for path in sorted(payload.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix in (".part", ".tmp"):
            continue
        files.append((path.relative_to(payload).as_posix(), path))
    return files


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", required=True, help="archive.org identifier")
    parser.add_argument("--payload", type=Path, default=root / "payload")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0,
                        help="upload at most this many files this run")
    parser.add_argument("--batch", type=int, default=150,
                        help="files per upload call; the item rations queued "
                             "tasks, so this is not about bytes")
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between batches")
    parser.add_argument("--skip", action="append", default=[],
                        help="skip payload paths starting with this; "
                             "`--skip thumbs/` while Pages serves the pictures "
                             "and the item rations queued tasks")
    args = parser.parse_args()

    import internetarchive as ia

    wanted = plan(args.payload)
    if args.skip:
        before = len(wanted)
        wanted = [(name, path) for name, path in wanted
                  if not any(name.startswith(prefix) for prefix in args.skip)]
        print(f"  skipping {before - len(wanted):,} files under "
              f"{', '.join(args.skip)}", file=sys.stderr)
    if not wanted:
        raise SystemExit(f"nothing to upload under {args.payload}")

    item = ia.get_item(args.item)
    # Skip what is already there at the same size. The thumbnails arrive over
    # days, so almost every run after the first is mostly a no-op.
    present = {f["name"]: int(f.get("size") or 0) for f in item.files}
    todo = [(name, path) for name, path in wanted
            if present.get(name) != path.stat().st_size]
    # Correctness before pictures: see the module docstring.
    todo.sort(key=lambda pair: (pair[0].startswith("thumbs/"), pair[0]))

    total = sum(p.stat().st_size for _, p in todo)
    print(f"{len(wanted):,} files in the payload, {len(todo):,} to upload "
          f"({total / 1024 / 1024:.0f} MB)", file=sys.stderr)
    if args.limit:
        todo = todo[: args.limit]
        print(f"  limited to {len(todo):,} this run", file=sys.stderr)
    if args.dry_run or not todo:
        for name, _ in todo[:10]:
            print(f"    {name}", file=sys.stderr)
        return

    exists = item.exists
    sent = failed = 0
    batches = [todo[i: i + args.batch]
               for i in range(0, len(todo), args.batch)]
    for index, batch in enumerate(batches, 1):
        for attempt in range(6):
            try:
                result = item.upload(
                    {name: str(path) for name, path in batch},
                    metadata=None if exists else METADATA,
                    retries=5,
                    retries_sleep=10,
                    verbose=False,
                    queue_derive=False,   # nothing here needs a derive
                )
                exists = True             # metadata is set once
                bad = [r for r in result
                       if getattr(r, "status_code", 200) != 200]
                sent += len(batch) - len(bad)
                failed += len(bad)
                break
            except Exception as exc:  # noqa: BLE001
                # "bucket_tasks_queued exceeds rationed amount" clears with
                # time and nothing else, so wait properly rather than retrying
                # into the same wall.
                wait = min(300, 20 * (2 ** attempt))
                print(f"  batch {index}/{len(batches)} backing off {wait}s: "
                      f"{str(exc)[:90]}", file=sys.stderr, flush=True)
                time.sleep(wait)
        else:
            failed += len(batch)
            print(f"  batch {index}/{len(batches)} GAVE UP", file=sys.stderr)
        print(f"  {sent:,}/{len(todo):,} uploaded", file=sys.stderr, flush=True)
        if index < len(batches):
            time.sleep(args.pause)

    print(f"\nuploaded {sent:,}; {failed:,} failed", file=sys.stderr)
    print(f"https://archive.org/details/{args.item}", file=sys.stderr)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
