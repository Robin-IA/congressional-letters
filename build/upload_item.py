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

Afterwards, rebuild with ``--item`` so ``index.json`` records the item's
download URLs rather than the local ``payload/`` paths::

    python3 build/upload_item.py --item letters-from-congress-data
    python3 build/build_site.py  --item letters-from-congress-data
"""

from __future__ import annotations

import argparse
import sys
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
    args = parser.parse_args()

    import internetarchive as ia

    wanted = plan(args.payload)
    if not wanted:
        raise SystemExit(f"nothing to upload under {args.payload}")

    item = ia.get_item(args.item)
    # Skip what is already there at the same size. The thumbnails arrive over
    # days, so almost every run after the first is mostly a no-op.
    present = {f["name"]: int(f.get("size") or 0) for f in item.files}
    todo = [(name, path) for name, path in wanted
            if present.get(name) != path.stat().st_size]

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
    result = item.upload(
        {name: str(path) for name, path in todo},
        metadata=None if exists else METADATA,
        retries=5,
        retries_sleep=10,
        verbose=True,
        queue_derive=False,     # nothing here needs a derive
    )
    failed = [r for r in result if getattr(r, "status_code", 200) != 200]
    print(f"\nuploaded {len(todo) - len(failed):,}; {len(failed)} failed",
          file=sys.stderr)
    print(f"https://archive.org/details/{args.item}", file=sys.stderr)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
