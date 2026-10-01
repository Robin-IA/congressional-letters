#!/usr/bin/env python3
"""Turn a letter's URL into what the letter is: date, recipient, subject, role.

Why the URL carries this at all
-------------------------------
Only 24% of these PDFs have a text layer — measured on a sample of 21, five of
which yielded text. The rest are scans of the signed original, which is what a
letter is. So the document cannot be read without OCR, and the URL has to do
the work instead.

It turns out to be good at it, because the people publishing these letters
named the files for humans::

    012622_-wh-hhs-fda---covid-therapeutics-letter
    4-5-20---minnesota-delegation-letter-to-president-trump---disaster-declaration
    barrasso-arrington-lead-bicameral-letter-to-biden-admin-on-mexico-undermining-usmca
    pressley-warren-markey-letter-to-hhs-re-racial-disparities-in-vaccine-distribution
    raskin-joins-letter-urging-trump-raise-jamal-khashoggis-disappearance-saudi

Date, co-signers, the member's role, the recipient and the subject — all of it
in the slug. This is the same shape of luck as CREST's "LETTER TO x FROM y"
titles, in a different field.

What it cannot do
-----------------
Some sites name files by topic with no recipient ("2022 crab disaster letter",
"dot_letter.pdf"), and some by nothing at all. Those keep whatever the slug
gives and leave the rest empty, rather than guessing. An empty recipient is a
fact about the catalogue, not a parse failure to paper over.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date as date_type
from pathlib import Path

# Path furniture that carries no meaning about the letter.
PATH_NOISE = re.compile(
    r"^(news|newsroom|press|press-releases?|releases?|media|download|downloads"
    r"|public|sites|default|files|imo|doc|docs|documents|wp-content|uploads"
    r"|_cache|cache|index\.cfm|content|assets|about|issues|upload|audio"
    r"|newsroom-news-releases|news-releases)$", re.IGNORECASE)

EXTENSION = re.compile(r"\.(pdf|html?|aspx|cfm|docx?|txt)$", re.IGNORECASE)

# A date the slug states outright. Ordered longest-first so that a four-digit
# year is not read as a two-digit one.
#
# WHITESPACE IS A SEPARATOR HERE. readable() has already turned hyphens,
# underscores and dots into spaces, so by the time a slug reaches this it says
# "2021 02 23", not "2021-02-23". The first version matched only [-_.] and so
# found nothing but the run-together forms — which is why date coverage
# measured 18.6% when most of these slugs do state a date.
SEP = r"[-_.\s]"
DATE_PATTERNS = [
    (re.compile(rf"\b(20[0-2]\d){SEP}([01]?\d){SEP}([0-3]?\d)\b"), "ymd"),
    (re.compile(rf"\b([01]?\d){SEP}([0-3]?\d){SEP}(20[0-2]\d)\b"), "mdy"),
    (re.compile(r"\b([01]\d)([0-3]\d)(20[0-2]\d)\b"), "mdy"),
    (re.compile(rf"\b([01]?\d){SEP}([0-3]?\d){SEP}([0-2]\d)\b"), "mdy2"),
    (re.compile(r"\b([01]\d)([0-3]\d)([0-2]\d)\b"), "mdy2"),
]

# /2016/1/slug and /2024/3/slug — a date in the path rather than the name.
PATH_DATE = re.compile(r"/(20[0-2]\d)/([01]?\d)(?:/([0-3]?\d))?/")

# How the member relates to the letter. Sites say this explicitly and it is
# worth keeping: "joins" a letter of 171 signatories is not "leads" one.
ROLE_VERBS = {
    "lead": "leads", "leads": "leads", "led": "leads", "leading": "leads",
    "join": "joins", "joins": "joins", "joined": "joins", "joining": "joins",
    "send": "sends", "sends": "sends", "sent": "sends", "sending": "sends",
    "write": "writes", "writes": "writes", "wrote": "writes",
    "urge": "urges", "urges": "urges", "urging": "urges",
    "press": "presses", "presses": "presses", "pressing": "presses",
    "demand": "demands", "demands": "demands", "demanding": "demands",
    "call": "calls", "calls": "calls", "calling": "calls",
    "ask": "asks", "asks": "asks", "asking": "asks",
    "question": "questions", "questions": "questions",
    "blast": "blasts", "blasts": "blasts", "blasting": "blasts",
    "slam": "slams", "slams": "slams", "slamming": "slams",
}

QUALIFIERS = {"bipartisan", "bicameral", "delegation", "colleagues",
              "democrats", "republicans", "members"}

# The recipient, however the slug phrases it.
RECIPIENT_PATTERNS = [
    re.compile(r"\bletters?\s+to\s+(?P<who>.+?)"
               r"(?:\s+(?:on|re|regarding|about|concerning|over)\b(?P<subj>.*))?$",
               re.IGNORECASE),
    re.compile(r"\b(?:calling|call|urging|urge|asking|ask|pressing|press|"
               r"demanding|demand)\s+(?:on\s+)?(?P<who>.+?)"
               r"(?:\s+to\b(?P<subj>.*))?$", re.IGNORECASE),
    re.compile(r"\b(?:blasting|blast|slamming|slam|questioning|question)\s+"
               r"(?P<who>.+?)(?:\s+(?:on|over|about)\b(?P<subj>.*))?$",
               re.IGNORECASE),
]

# Agencies and offices written several ways. Without this the recipient facet
# splits one agency across a dozen spellings, which is most of what the facet
# is for. Keys are matched against the whole recipient string, lower-cased.
RECIPIENTS: dict[str, tuple[str, ...]] = {
    "Department of Justice": ("doj", "justice dept", "justice department",
                              "department of justice", "us doj", "attorney general",
                              "ag", "the attorney general"),
    "Environmental Protection Agency": ("epa", "the epa", "us epa"),
    "Department of Defense": ("dod", "defense dept", "defense department",
                              "department of defense", "pentagon"),
    "Department of Health and Human Services": (
        "hhs", "health and human services", "department of health and human services"),
    "Securities and Exchange Commission": ("sec", "the sec"),
    "Internal Revenue Service": ("irs", "the irs"),
    "Office of Management and Budget": ("omb", "the omb"),
    "Department of Homeland Security": ("dhs", "homeland security"),
    "Department of Agriculture": ("usda", "agriculture dept",
                                  "department of agriculture"),
    "Department of Veterans Affairs": ("va", "the va", "veterans affairs"),
    "Department of State": ("state dept", "department of state", "secretary of state"),
    "Department of Education": ("ed", "education dept", "department of education"),
    "Department of Energy": ("doe", "energy dept", "department of energy"),
    "Department of the Treasury": ("treasury", "treasury dept",
                                   "department of the treasury"),
    "Department of Transportation": ("dot", "transportation dept",
                                     "department of transportation"),
    "Department of the Interior": ("doi", "interior dept", "interior"),
    "Department of Labor": ("dol", "labor dept", "department of labor"),
    "Department of Commerce": ("commerce dept", "department of commerce"),
    "Food and Drug Administration": ("fda", "the fda"),
    "Centers for Disease Control": ("cdc", "the cdc"),
    "Federal Communications Commission": ("fcc", "the fcc"),
    "Federal Trade Commission": ("ftc", "the ftc"),
    "Federal Emergency Management Agency": ("fema", "the fema"),
    "Government Accountability Office": ("gao", "the gao"),
    "Central Intelligence Agency": ("cia", "the cia"),
    "Federal Bureau of Investigation": ("fbi", "the fbi"),
    "Centers for Medicare and Medicaid Services": ("cms", "the cms"),
    "Consumer Financial Protection Bureau": ("cfpb", "the cfpb"),
    "Election Assistance Commission": ("eac", "the eac"),
    "Federal Energy Regulatory Commission": ("ferc", "the ferc"),
    "Office of Personnel Management": ("opm", "the opm"),
    "Small Business Administration": ("sba", "the sba"),
    "Social Security Administration": ("ssa", "the ssa"),
    "National Institutes of Health": ("nih", "the nih"),
    "Bureau of Land Management": ("blm", "the blm"),
    "Nuclear Regulatory Commission": ("nrc", "the nrc"),
    "The President": ("president", "the president", "potus",
                      "president trump", "president biden", "president obama",
                      "white house", "wh", "the white house"),
}

# Named officials, mapped to the office they held. Without this the recipient
# facet splits the same agency in two: measured on a 2,300-letter sample,
# "Pruitt" drew 18 letters and "Environmental Protection Agency" 17, and they
# are the same correspondent. Likewise "Trump" (13) and "Biden" (12) against
# "The President" (72).
#
# Deliberately limited to agency heads and cabinet officers frequently written
# to, because each entry is an assertion about who held an office and that has
# to be worth checking. Anyone not listed keeps their own name.
OFFICIALS: dict[str, tuple[str, ...]] = {
    "The President": ("trump", "president trump", "biden", "president biden",
                      "obama", "president obama", "trump white house"),
    "Department of Justice": ("holder", "lynch", "sessions", "barr", "garland",
                              "bondi", "mukasey", "gonzales", "whitaker",
                              "rosenstein"),
    "Environmental Protection Agency": ("pruitt", "wheeler", "regan",
                                        "mccarthy", "zeldin", "jackson"),
    "Department of the Treasury": ("yellen", "mnuchin", "bessent", "geithner",
                                   "lew", "paulson"),
    "Department of Health and Human Services": (
        "azar", "becerra", "price", "burwell", "sebelius", "kennedy"),
    "Department of Defense": ("hegseth", "austin", "esper", "mattis",
                             "carter", "panetta", "rumsfeld"),
    "Department of State": ("rubio", "blinken", "pompeo", "tillerson",
                            "kerry", "clinton", "rice"),
    "Department of Agriculture": ("perdue", "vilsack", "rollins"),
    "Department of Homeland Security": ("mayorkas", "noem", "nielsen",
                                        "johnson", "napolitano", "chertoff"),
    "Department of Education": ("devos", "cardona", "duncan", "mcmahon"),
    "Department of Veterans Affairs": ("shulkin", "wilkie", "mcdonough",
                                       "collins", "shinseki"),
    "Department of Transportation": ("buttigieg", "chao", "duffy", "foxx"),
    "Department of Energy": ("granholm", "perry", "wright", "moniz"),
    "Department of the Interior": ("zinke", "bernhardt", "haaland", "burgum"),
    "Department of Labor": ("acosta", "scalia", "walsh", "chavez-deremer"),
    "Securities and Exchange Commission": ("schapiro", "clayton", "gensler",
                                           "atkins", "white"),
    "Federal Communications Commission": ("pai", "rosenworcel", "wheeler",
                                          "carr", "genachowski"),
    "Federal Trade Commission": ("khan", "simons", "ferguson", "ramirez"),
    "Internal Revenue Service": ("koskinen", "rettig", "werfel"),
    "Office of Management and Budget": ("vought", "mulvaney", "young",
                                        "stockman"),
    "Centers for Disease Control": ("redfield", "walensky", "cohen",
                                    "monarez"),
    "Food and Drug Administration": ("califf", "hahn", "gottlieb", "makary"),
}

RECIPIENT_LOOKUP = {
    spelling: canonical
    for mapping in (RECIPIENTS, OFFICIALS)
    for canonical, spellings in mapping.items()
    for spelling in spellings
}

# A surname that maps to two different offices in OFFICIALS — Wheeler was both
# an EPA and an FCC administrator, Collins both VA and a senator, McCarthy both
# EPA and a Speaker. A bare surname cannot be resolved, so it is left alone.
_seen: Counter = Counter()
for _canon, _spellings in OFFICIALS.items():
    _seen.update(_spellings)
AMBIGUOUS_OFFICIALS = {name for name, n in _seen.items() if n > 1}
for _name in AMBIGUOUS_OFFICIALS:
    RECIPIENT_LOOKUP.pop(_name, None)

# Recipients that are not names: initials, stray tokens, file-naming debris.
# "sw hj letter to jc" is how one office names its files, and "jc" drew 12
# letters in the sample before this.
NOT_A_RECIPIENT = re.compile(
    r"^(?:[a-z]{1,2}|\d+|[a-z]\d+|re|cc|fw|final|draft|signed|copy|letter"
    r"|attachment|attach|encl|pdf|doc|v\d+)$", re.IGNORECASE)


def readable(key: str) -> str:
    """The part of a URL a person would read, as words."""
    path = key.split("?", 1)[0]
    parts = [p for p in path.split("/")[1:] if p]
    parts = [p for p in parts
             if not PATH_NOISE.match(p)
             and not re.fullmatch(r"[0-9a-f]{8,}", p, re.IGNORECASE)
             and not re.fullmatch(r"[0-9a-f-]{30,}", p, re.IGNORECASE)
             and not re.fullmatch(r"\d{1,4}", p)
             and not re.fullmatch(r"[0-9a-f]", p, re.IGNORECASE)]
    text = " ".join(parts) if parts else " ".join(path.split("/")[1:])
    text = EXTENSION.sub("", text)
    # A content hash that shares a path segment with the real filename, so
    # dropping whole segments above does not reach it. Left in, it surfaces as
    # a subject line reading "b06e97ccbba6b1d022652a8c250cf31f".
    text = re.sub(r"\b[0-9a-f]{16,}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[-_.+]+", " ", text)
    text = re.sub(r"%\w\w", " ", text)
    # Percent-escapes that were not UTF-8 survive unquoting as surrogates, and
    # a name like Luján arrives as a lone broken byte.
    text = "".join(c for c in text if c.isprintable() and not 0xDC00 <= ord(c) <= 0xDFFF)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def find_date(key: str, slug: str) -> tuple[str | None, str | None]:
    """An ISO date the URL states, and where it came from."""
    for pattern, order in DATE_PATTERNS:
        match = pattern.search(slug)
        if not match:
            continue
        a, b, c = match.groups()
        try:
            if order == "ymd":
                year, month, day = int(a), int(b), int(c)
            elif order == "mdy":
                month, day, year = int(a), int(b), int(c)
            else:
                month, day = int(a), int(b)
                year = 2000 + int(c)
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            return date_type(year, month, day).isoformat(), "slug"
        except ValueError:
            continue

    match = PATH_DATE.search(key)
    if match:
        year, month, day = match.group(1), match.group(2), match.group(3)
        try:
            if 1 <= int(month) <= 12:
                has_day = bool(day) and 1 <= int(day) <= 31
                iso = date_type(int(year), int(month),
                                int(day) if has_day else 1).isoformat()
                # A path of /2016/1/ dates the letter to a month, not a day.
                # Returning it as 2016-01-01 and calling that a date would put
                # every January letter on New Year's Day.
                return iso, "path" if has_day else "path-month"
        except ValueError:
            pass
    return None, None


def strip_date(slug: str) -> str:
    out = slug
    for pattern, _ in DATE_PATTERNS:
        out = pattern.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


def split_role(slug: str, last_name: str,
               surnames: frozenset[str] = frozenset()
               ) -> tuple[str | None, list[str], list[str], str, str]:
    """Pull the member's role, co-signers and qualifiers off the front.

    Returns ``(role, cosigners, qualifiers, from_letter, topic)``.

    A CO-SIGNER HAS TO BE A MEMBER OF CONGRESS. Taking every unrecognised word
    before "letter" turned "2022 crab disaster letter" into three co-signers
    named 2022, crab and disaster. Checking against the roster of sitting
    members instead means the leftovers fall through to ``topic``, which is
    where "crab disaster" belongs.
    """
    words = slug.split()
    plain_words = [w.rstrip("s") for w in words]
    if "letter" not in plain_words:
        return None, [], [], "", slug

    index = plain_words.index("letter")
    head, tail = words[:index], words[index + 1:]

    role = None
    cosigners: list[str] = []
    qualifiers: list[str] = []
    leftover: list[str] = []
    for word in head:
        token = word.lower()
        if token in ROLE_VERBS:
            role = ROLE_VERBS[token]
        elif token in QUALIFIERS:
            qualifiers.append(token)
        elif token in surnames and token != last_name.lower():
            cosigners.append(token)
        elif token != last_name.lower():
            leftover.append(word)

    # A verb can also follow the word: "letter urging trump to ...".
    if role is None and tail and tail[0].lower() in ROLE_VERBS:
        role = ROLE_VERBS[tail[0].lower()]
    return role, cosigners, qualifiers, " ".join(words[index:]), " ".join(leftover)


def canonical_recipient(raw: str) -> tuple[str | None, str | None, str]:
    """(display name, canonical name, overflow) for a recipient string.

    ``overflow`` is the tail that was not part of the name. Slugs without an
    "on" or "re" to mark the subject run the two together — "letter to eac
    election worker recruitment guidance" names the EAC and then describes the
    letter — so a recipient longer than a name is split rather than kept whole.
    """
    cleaned = re.sub(r"\s+", " ", raw or "").strip(" -–—,:;")
    if not cleaned:
        return None, None, ""
    cleaned = re.sub(r"^(the|hon|honorable|secretary|sec|administrator|"
                     r"director|chairman|chair|acting)\s+", "", cleaned,
                     flags=re.IGNORECASE).strip()
    if not cleaned:
        return None, None, ""

    def resolve(text: str) -> str | None:
        return RECIPIENT_LOOKUP.get(text.lower().rstrip("."))

    # Longest known name first, so "department of justice" wins over "justice".
    words = cleaned.split()
    for size in range(min(6, len(words)), 0, -1):
        probe = " ".join(words[:size])
        canon = resolve(probe)
        if canon:
            return probe, canon, " ".join(words[size:])

    # Not a name we know. Keep it only if it reads like one.
    if NOT_A_RECIPIENT.match(cleaned) or len(cleaned) < 3:
        return None, None, cleaned
    if all(len(w) <= 2 for w in words):
        return None, None, cleaned
    # Four words is already a long way for a name; beyond that it is prose.
    if len(words) > 4:
        return None, None, cleaned
    return cleaned, cleaned.title(), ""


def parse(record: dict, last_name: str,
          surnames: frozenset[str] = frozenset()) -> dict:
    key = record["key"]
    slug = readable(key)
    iso, date_source = find_date(key, slug)
    body = strip_date(slug)
    role, cosigners, qualifiers, from_letter, topic = split_role(
        body, last_name, surnames)

    recipient = recipient_canonical = subject = None
    for pattern in RECIPIENT_PATTERNS:
        match = pattern.search(from_letter or body)
        if not match:
            continue
        recipient, recipient_canonical, overflow = canonical_recipient(
            match.group("who"))
        if recipient:
            tail = (match.groupdict().get("subj") or "").strip(" -–—,:;")
            subject = " ".join(p for p in (overflow, tail) if p).strip()
            break

    if not subject:
        # Whatever is left once the word "letter" and its framing are gone,
        # plus the topic words that sat in front of it: "2022 crab disaster
        # letter" is about a crab disaster, and that is stated before the noun
        # rather than after it.
        tail = re.sub(r"\b(letters?|to|of|on|re|regarding|from)\b", " ",
                      from_letter or body, flags=re.IGNORECASE)
        if recipient:
            tail = tail.replace(recipient.lower(), " ")
        subject = re.sub(r"\s+", " ", f"{topic} {tail}").strip()

    if subject:
        # The topic and the tail can name the same thing twice — "send send",
        # "final final" — because one came from each side of the word "letter".
        words = subject.split()
        subject = " ".join(
            w for i, w in enumerate(words) if i == 0 or w != words[i - 1])
        subject = subject.strip(" -–—,:;") or None

    # Only 19% of slugs state a date. For the rest, the capture is a real upper
    # bound — the letter existed by the day Wayback saw it — so the timeline
    # can place every letter as long as it says which kind of date it is
    # showing. "In the archive by March 2016" is information; a blank is not.
    captured = record.get("timestamp", "")
    first_seen = (f"{captured[:4]}-{captured[4:6]}-{captured[6:8]}"
                  if len(captured) >= 8 else None)
    if iso:
        best, precision = iso, ("month" if date_source == "path-month" else "day")
    elif first_seen:
        best, precision = first_seen, "captured-by"
    else:
        best, precision = None, None

    return {
        **record,
        "slug": slug,
        "date": iso,
        "date_source": date_source,
        "first_seen": first_seen,
        "date_best": best,
        "date_precision": precision,
        "recipient": recipient,
        "recipient_canonical": recipient_canonical,
        "subject": subject or None,
        "role": role,
        "cosigners": cosigners,
        "qualifiers": qualifiers,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path,
                        default=root / "data" / "letters-raw.jsonl")
    parser.add_argument("--members", type=Path,
                        default=root / "data" / "members.json")
    parser.add_argument("--out", type=Path,
                        default=root / "data" / "letters-parsed.jsonl")
    args = parser.parse_args()

    member_list = json.loads(args.members.read_text(encoding="utf-8"))
    members = {m["bioguide"]: m for m in member_list}
    surnames = frozenset(
        (m.get("last") or "").lower() for m in member_list if m.get("last"))

    stats = Counter()
    with args.raw.open(encoding="utf-8") as source, \
            args.out.open("w", encoding="utf-8") as sink:
        for line in source:
            record = json.loads(line)
            member = members.get(record.get("bioguide"), {})
            parsed = parse(record, member.get("last", ""), surnames)
            stats["letters"] += 1
            stats["dated"] += bool(parsed["date"])
            stats["with_recipient"] += bool(parsed["recipient_canonical"])
            stats["with_subject"] += bool(parsed["subject"])
            stats["with_role"] += bool(parsed["role"])
            sink.write(json.dumps(parsed, ensure_ascii=False) + "\n")

    total = max(1, stats["letters"])
    for field in ("letters", "dated", "with_recipient", "with_subject",
                  "with_role"):
        print(f"  {field:>16}: {stats[field]:>7,} "
              f"({stats[field] / total * 100:5.1f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()
