#!/usr/bin/env python3
"""Bake the parsed letters into the JSON the explorer reads.

Shape, and where each part lives
--------------------------------
Laid out the way internetarchivecanada/etd-viewer is: a small static page in
the repository, and the bulk of the data in an archive.org item that the page
streams client-side. Items serve ``Access-Control-Allow-Origin: *``, so the
browser can read them directly — that one header is what makes the whole
arrangement work, and it is why the ETD viewer can say "no server, no build".

    index.json                   IN THE REPO. Every member, their counts and
                                 spans, the facets, the year histogram. ~150 KB,
                                 so the page paints immediately.
    payload/member/<id>.json     IN AN ARCHIVE.ORG ITEM. One member's letters,
                                 fetched when that member is opened. ~21 MB.
    payload/thumbs/<digest>.jpg  IN AN ARCHIVE.ORG ITEM. Rendered first pages,
                                 ~1.3 GB when complete.

``payload/`` is gitignored and uploaded by ``build/upload_item.py``. Keeping
it out of the repository is not tidiness: 1.3 GB of thumbnails does not belong
in a Pages repository, which is exactly why the North Korea explorer keeps its
screenshots on an item too.

A visitor loads the small index and then one member at a time.

Letters are arrays rather than objects, with the column order carried in
index.json so the page and the build cannot disagree about it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date as date_type
from pathlib import Path

COLUMNS = ["key", "timestamp", "kind", "date", "precision", "recipient",
           "subject", "role", "cosigners", "digest"]

# Words that say nothing about a letter and make a subject read like debris.
STOPWORDS = {
    "letter", "letters", "final", "signed", "copy", "pdf", "doc", "docx",
    "the", "a", "an", "and", "or", "for", "with", "from", "to", "of", "on",
    "in", "at", "by", "re", "fw", "cc", "v1", "v2", "draft", "attachment",
}


def tidy_subject(subject: str | None) -> str | None:
    if not subject:
        return None
    words = [w for w in re.split(r"\s+", subject) if w]
    kept = [w for w in words if w.lower() not in STOPWORDS and len(w) > 1]
    if not kept:
        return None
    text = " ".join(kept)
    # Slugs are lower case; sentence case reads as a description rather than
    # as a filename, which is what this is being presented as.
    text = text[0].upper() + text[1:]
    return text[:140]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed", type=Path,
                        default=root / "data" / "letters-parsed.jsonl")
    parser.add_argument("--members", type=Path,
                        default=root / "data" / "members.json")
    # thumbs live under --payload; kept as a flag for odd layouts
    parser.add_argument("--thumbs", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=root,
                        help="where index.json is written (the repo root)")
    parser.add_argument("--payload", type=Path, default=root / "payload",
                        help="per-member files and thumbnails, uploaded separately")
    parser.add_argument("--pdf-dates", type=Path,
                        default=root / "data" / "pdf-dates.jsonl")
    parser.add_argument("--item", default="",
                        help="archive.org item holding the payload; sets the "
                             "base URLs the published page fetches from")
    args = parser.parse_args()

    thumbs_dir = args.thumbs or (args.payload / "thumbs")
    if args.item:
        base = f"https://archive.org/download/{args.item}/"
        data_base, thumb_base = base + "member/", base + "thumbs/"
    else:
        data_base, thumb_base = "payload/member/", "payload/thumbs/"

    # Dates the PDFs state about themselves, collected by the thumbnail pass.
    # Preferred over the Wayback capture and beaten only by a date the
    # filename states outright. Measured on 14 letters whose filename gave a
    # date: all 14 PDFs carried one, 13 agreed within three days, median
    # difference zero.
    pdf_dates: dict[str, str] = {}
    if args.pdf_dates.exists():
        with args.pdf_dates.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if row.get("digest") and row.get("date"):
                    pdf_dates[row["digest"]] = row["date"]
    if pdf_dates:
        print(f"  {len(pdf_dates):,} dates from PDF metadata", file=sys.stderr)

    member_rows = json.loads(args.members.read_text(encoding="utf-8"))
    members = {m["bioguide"]: m for m in member_rows}

    by_member: dict[str, list] = defaultdict(list)
    recipients = Counter()
    years = Counter()
    kinds = Counter()
    precisions = Counter()
    roles = Counter()
    per_member_recipients: dict[str, Counter] = defaultdict(Counter)
    per_member_years: dict[str, Counter] = defaultdict(Counter)
    total = 0

    with args.parsed.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            bioguide = record.get("bioguide")
            if bioguide not in members:
                continue
            total += 1

            digest = record.get("digest") or ""
            kind = ("pdf" if record["mimetype"].startswith("application/pdf")
                    else "page")
            recipient = record.get("recipient_canonical")
            subject = tidy_subject(record.get("subject"))
            # Filename first, then what the PDF says about itself, then the
            # capture. Only the first is the letter's own stated date; the
            # second is when the file was made, which for a scan is when it
            # was scanned.
            best = record.get("date")
            precision = record.get("date_precision")
            if not best:
                from_pdf = pdf_dates.get(digest or "")
                if from_pdf:
                    best, precision = from_pdf, "scanned"
                else:
                    best = record.get("date_best")
            year = int(best[:4]) if best else None

            # The capture digest is the content address of the letter, and
            # also the thumbnail's filename. Identical letters captured many
            # times share one digest, so they share one rendering.
            has_thumb = bool(digest) and (thumbs_dir / f"{digest}.jpg").exists()

            by_member[bioguide].append([
                record["key"], record["timestamp"], kind, best,
                precision, recipient, subject,
                record.get("role"),
                ",".join(record.get("cosigners") or []),
                digest if has_thumb else "",
            ])

            if recipient:
                recipients[recipient] += 1
                per_member_recipients[bioguide][recipient] += 1
            if year:
                years[year] += 1
                per_member_years[bioguide][year] += 1
            kinds[kind] += 1
            precisions[precision or "none"] += 1
            if record.get("role"):
                roles[record["role"]] += 1

    # A searchable index of every letter's description, so search works from
    # a cold page load. Without it search could only see members the visitor
    # had already opened, which is to say it did not work.
    #
    # Compact by construction: member ids and recipients are interned, and a
    # result points at (member, row) rather than carrying the letter's URL,
    # which is the longest field. 93,000 letters come to about 4 MB, fetched
    # once on the first search rather than on page load.
    search_rows: list = []
    search_members: list[str] = []
    search_recipients: list[str] = []
    member_slot: dict[str, int] = {}
    recipient_slot: dict[str, int] = {}

    out_member = args.payload / "member"
    if out_member.exists():
        shutil.rmtree(out_member)
    out_member.mkdir(parents=True, exist_ok=True)

    index_members = []
    for bioguide, member in members.items():
        letters = by_member.get(bioguide, [])
        # Chronological, so a member page and the timeline agree without the
        # browser sorting thousands of rows.
        letters.sort(key=lambda row: (row[3] or "9999", row[0]))
        if letters:
            m_idx = member_slot.setdefault(bioguide, len(search_members))
            if m_idx == len(search_members):
                search_members.append(bioguide)
            for row_idx, row in enumerate(letters):
                subject = row[COLUMNS.index("subject")]
                recipient = row[COLUMNS.index("recipient")]
                if not subject and not recipient:
                    continue
                r_idx = -1
                if recipient:
                    r_idx = recipient_slot.setdefault(recipient,
                                                      len(search_recipients))
                    if r_idx == len(search_recipients):
                        search_recipients.append(recipient)
                year = int(row[COLUMNS.index("date")][:4])                     if row[COLUMNS.index("date")] else 0
                # 1 when the year is the letter's own, 0 when it is only the
                # date the Wayback Machine first saw the file. Sorting mixes
                # the two, and a capture year looks exact unless it is marked.
                exact = 0 if row[COLUMNS.index("precision")] == "captured-by" else 1
                search_rows.append([m_idx, r_idx, year, row_idx, subject or "",
                                    exact])
            (out_member / f"{bioguide}.json").write_text(
                json.dumps(letters, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")

        member_years = per_member_years.get(bioguide, Counter())
        index_members.append({
            "id": bioguide,
            "name": member["name"],
            "last": member["last"],
            "chamber": member["chamber"],
            "state": member["state"],
            "district": member.get("district"),
            "party": member["party"],
            "host": member["host"],
            "site": member["url"],
            "letters": len(letters),
            "span": ([min(member_years), max(member_years)]
                     if member_years else None),
            "top": [name for name, _ in
                    per_member_recipients.get(bioguide, Counter()).most_common(3)],
        })
    index_members.sort(key=lambda m: -m["letters"])

    index = {
        "built": date_type.today().isoformat(),
        "columns": COLUMNS,
        "letters": total,
        "members_with_letters": sum(1 for m in index_members if m["letters"]),
        "members_total": len(index_members),
        "years": {str(y): n for y, n in sorted(years.items())},
        "span": [min(years), max(years)] if years else None,
        "kinds": dict(kinds),
        "precisions": dict(precisions),
        "roles": dict(roles),
        # The page shows the top 200 but must not report that as the total:
        # there are thousands, most of them named once.
        "recipients": recipients.most_common(200),
        "recipients_total": len(recipients),
        "thumbs": sum(1 for rows in by_member.values()
                      for row in rows if row[9]),
        # Where the browser fetches the bulk data from. Relative paths serve
        # a local checkout; --item rewrites both to an archive.org download
        # URL for the published page.
        "thumb_base": thumb_base,
        "data_base": data_base,
        "search_url": (base + "search.json") if args.item else "payload/search.json",
        # Offered on the empty search page. Each one was checked against the
        # index and returns real results; a suggestion that finds nothing is
        # worse than none.
        "suggestions": ["covid", "opioid", "wildfire", "student loans",
                        "immigration", "veterans", "medicare", "climate",
                        "broadband", "tariff", "census", "social security"],
        "members": index_members,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    search_path = args.payload / "search.json"
    search_path.write_text(json.dumps(
        {"members": search_members, "recipients": search_recipients,
         "rows": search_rows}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"  search.json   {search_path.stat().st_size / 1024 / 1024:>8,.1f} MB "
          f"across {len(search_rows):,} letters   (payload)", file=sys.stderr)

    size = (args.out / "index.json").stat().st_size
    per_member_bytes = sum(p.stat().st_size for p in out_member.glob("*.json"))
    print(f"  index.json      {size / 1024:>8,.0f} KB   (in the repo)",
          file=sys.stderr)
    thumb_bytes = sum(p.stat().st_size for p in thumbs_dir.glob("*.jpg"))         if thumbs_dir.exists() else 0
    print(f"  member/*.json   {per_member_bytes / 1024:>8,.0f} KB "
          f"across {len(list(out_member.glob('*.json')))} files   (payload)",
          file=sys.stderr)
    print(f"  thumbs/*.jpg    {thumb_bytes / 1024:>8,.0f} KB   (payload)",
          file=sys.stderr)
    print(f"  page fetches payload from: {data_base}", file=sys.stderr)
    print(f"\n{total:,} letters, "
          f"{index['members_with_letters']:,} of {index['members_total']:,} "
          f"members, {index['span'][0] if index['span'] else '?'}"
          f"-{index['span'][1] if index['span'] else '?'}", file=sys.stderr)
    print(f"  recipients: {len(recipients):,}   "
          f"thumbnails: {index['thumbs']:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
