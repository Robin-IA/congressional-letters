#!/usr/bin/env python3
"""Bake the parsed letters into the JSON the explorer reads.

Shape, and why it is split
--------------------------
The corpus is large enough that one file would be a slow first paint —
tens of thousands of letters at roughly 150 bytes each. It is also organised
around members, so it splits along the axis people actually browse:

    data/index.json            every member, their counts and spans, the
                               aggregate facets, the year histogram
    data/member/<bioguide>.json  one member's letters, fetched when opened

A visitor loads a small index and then one member at a time. The North Korea
explorer splits per host for the same reason.

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
    parser.add_argument("--thumbs", type=Path, default=root / "site" / "thumbs")
    parser.add_argument("--out", type=Path, default=root / "site" / "data")
    args = parser.parse_args()

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

            kind = ("pdf" if record["mimetype"].startswith("application/pdf")
                    else "page")
            recipient = record.get("recipient_canonical")
            subject = tidy_subject(record.get("subject"))
            best = record.get("date_best")
            year = int(best[:4]) if best else None

            # The capture digest is the content address of the letter, and
            # also the thumbnail's filename. Identical letters captured many
            # times share one digest, so they share one rendering.
            digest = record.get("digest") or ""
            has_thumb = bool(digest) and (args.thumbs / f"{digest}.jpg").exists()

            by_member[bioguide].append([
                record["key"], record["timestamp"], kind, best,
                record.get("date_precision"), recipient, subject,
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
            precisions[record.get("date_precision") or "none"] += 1
            if record.get("role"):
                roles[record["role"]] += 1

    out_member = args.out / "member"
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
        "thumb_base": "thumbs/",
        "members": index_members,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    size = (args.out / "index.json").stat().st_size
    per_member_bytes = sum(p.stat().st_size for p in out_member.glob("*.json"))
    print(f"  index.json      {size / 1024:>8,.0f} KB", file=sys.stderr)
    print(f"  member/*.json   {per_member_bytes / 1024:>8,.0f} KB "
          f"across {len(list(out_member.glob('*.json')))} files",
          file=sys.stderr)
    print(f"\n{total:,} letters, "
          f"{index['members_with_letters']:,} of {index['members_total']:,} "
          f"members, {index['span'][0] if index['span'] else '?'}"
          f"-{index['span'][1] if index['span'] else '?'}", file=sys.stderr)
    print(f"  recipients: {len(recipients):,}   "
          f"thumbnails: {index['thumbs']:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
