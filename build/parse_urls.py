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
    r"|cache|index\.cfm|content|assets|about|issues|upload|audio"
    r"|newsroom-news-releases|news-releases|reference|reference_item"
    r"|bookjackets|common|image|images|item|stories|pdf|attachments"
    r"|sites-default-files|general|shared|resources|static"
    r"|uploadedfiles|media-center|mediacenter|posts|press-release"
    r"|evo|subsites|evo-subsites|sites-default|inline-files|node|view|print"
    r"|wysiwyg|wysiwyg-uploaded|uploaded|vendor|themes|modules)$",
    re.IGNORECASE)

# Drupal stores a site's uploads under its own hostname:
# buchanan.house.gov/sites/buchanan.house.gov/files/letter to ssa.pdf
# Left in, the host becomes the first words of the description, which is why
# 194 letters were described as "house gov".
HOSTNAME_SEGMENT = re.compile(
    r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.(gov|com|org|net|us)$", re.IGNORECASE)

# Site builders prefix internal folders with an underscore — /public/_files/,
# /_cache/ — and without stripping it the folder name survives into the
# description, so every Barrasso letter began with the word "Files".
LEADING_UNDERSCORE = re.compile(r"^_+")

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
    # "20130624fhfaletter" — year first, no separators.
    (re.compile(r"\b(20[0-2]\d)([01]\d)([0-3]\d)\b"), "ymd"),
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

# Titles that belong to a correspondent rather than to the letter. Stripped
# from a recipient, and from a subject, where they otherwise stand in as the
# whole description: 149 letters were described as "secretary", because
# "letter to secretary cardona" resolved Cardona and left his title behind.
HONORIFICS = {
    "THE", "HON", "HONORABLE", "MR", "MRS", "MS", "MISS", "DR", "PROF",
    "SECRETARY", "SEC", "ADMINISTRATOR", "DIRECTOR", "CHAIRMAN", "CHAIRWOMAN",
    "CHAIR", "ACTING", "DEPUTY", "ASSISTANT", "COMMISSIONER", "GOVERNOR",
    "GOV", "AMBASSADOR", "SENATOR", "SEN", "CONGRESSMAN", "CONGRESSWOMAN",
    "REPRESENTATIVE", "REP", "PRESIDENT", "JUDGE", "GENERAL", "ADMIRAL",
}

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
                              "department of defense", "pentagon",
                              "secdef", "secretary of defense"),
    "Department of Health and Human Services": (
        "hhs", "health and human services", "department of health and human services"),
    "Securities and Exchange Commission": ("sec", "the sec"),
    "Internal Revenue Service": ("irs", "the irs"),
    "Office of Management and Budget": ("omb", "the omb"),
    "Department of Homeland Security": ("dhs", "homeland security"),
    "Department of Agriculture": ("usda", "agriculture dept",
                                  "department of agriculture"),
    "Department of Veterans Affairs": ("va", "the va", "veterans affairs",
                                      "secva", "secretary of veterans affairs"),
    "Department of State": ("state dept", "department of state",
                            "secretary of state", "state", "state department",
                            "the state department"),
    "Department of Housing and Urban Development": (
        "hud", "the hud", "housing and urban development"),
    "Office of the US Trade Representative": ("ustr", "the ustr",
                                              "trade representative"),
    "Department of Education": ("ed", "education dept", "department of education"),
    "Department of Energy": ("doe", "energy dept", "department of energy"),
    "Department of the Treasury": ("treasury", "treasury dept",
                                   "department of the treasury"),
    "Department of Transportation": ("dot", "transportation dept",
                                     "department of transportation"),
    "Department of the Interior": ("doi", "interior dept", "interior"),
    "Department of Labor": ("dol", "labor dept", "department of labor"),
    "Department of Commerce": ("commerce dept", "department of commerce",
                              "commerce", "the commerce department"),
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
    "United States Postal Service": ("usps", "the usps", "postal service",
                                     "postmaster general", "us postal service"),
    "Immigration and Customs Enforcement": ("ice", "the ice"),
    "Citizenship and Immigration Services": ("uscis", "the uscis"),
    "Customs and Border Protection": ("cbp", "the cbp"),
    "Bureau of Alcohol, Tobacco, Firearms and Explosives": ("atf", "the atf"),
    "Bureau of Prisons": ("bop", "the bop"),
    "Drug Enforcement Administration": ("dea", "the dea"),
    "National Oceanic and Atmospheric Administration": ("noaa", "the noaa"),
    "General Services Administration": ("gsa", "the gsa"),
    "Office of the Comptroller of the Currency": ("occ", "the occ"),
    "Commodity Futures Trading Commission": ("cftc", "the cftc"),
    "National Highway Traffic Safety Administration": ("nhtsa", "the nhtsa"),
    "Federal Reserve": ("fed", "the fed", "federal reserve",
                        "the federal reserve", "frb", "powell",
                        "fed chair", "federal reserve chair"),
    "Army Corps of Engineers": ("usace", "army corps", "the army corps",
                                "corps of engineers",
                                "army corps of engineers"),
    "Federal Aviation Administration": ("faa", "the faa"),
    "Federal Housing Finance Agency": ("fhfa", "the fhfa"),
    "Federal Deposit Insurance Corporation": ("fdic", "the fdic"),
    "National Labor Relations Board": ("nlrb", "the nlrb"),
    "Occupational Safety and Health Administration": ("osha", "the osha"),
    "Federal Election Commission": ("fec", "the fec"),
    "Transportation Security Administration": ("tsa", "the tsa"),
    "National Aeronautics and Space Administration": ("nasa", "the nasa"),
    "Office of the Inspector General": ("ig", "oig", "inspector general",
                                        "the inspector general"),
    "Government Publishing Office": ("gpo", "the gpo"),
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
                      "white house", "wh", "the white house",
                      "pres biden", "pres trump", "pres obama"),
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
    "Department of Commerce": ("ross", "raimondo", "lutnick", "pritzker"),
    "Federal Bureau of Investigation": ("wray", "comey", "patel", "mueller"),
    "United States Postal Service": ("dejoy", "brennan", "donahoe"),
    "The Vice President": ("pence", "vice president", "the vice president",
                           "vp", "vance"),
}

# Congressional leadership, written to as often as any agency. Grouping the
# titles keeps "Speaker Johnson", "Speaker Pelosi" and a bare "Speaker" from
# being three correspondents, while leaving the party leaders as themselves.
LEADERSHIP: dict[str, tuple[str, ...]] = {
    "The Speaker of the House": ("speaker", "the speaker", "speaker pelosi",
                                 "speaker ryan", "speaker johnson",
                                 "speaker boehner", "speaker mccarthy"),
    # Bare surnames of party leaders. A letter addressed to McConnell or
    # Schumer is addressed to them as leader; split out individually they
    # fragment a facet that exists to show who Congress writes to. Where the
    # slug names the office instead - "speaker pelosi" - that wins, because it
    # is more specific.
    "Congressional leadership": ("leadership", "congressional leadership",
                                 "colleagues", "dear colleague",
                                 "house leadership", "senate leadership",
                                 "appropriators", "approps",
                                 "appropriations leaders",
                                 "mcconnell", "schumer", "pelosi", "jeffries",
                                 "thune", "majority leader", "minority leader"),
}

RECIPIENT_LOOKUP = {
    spelling: canonical
    for mapping in (RECIPIENTS, OFFICIALS, LEADERSHIP)
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

# A slug written with no separators at all, where the description has fused
# into the name: "Ahold Delhaize Repricegouginginstopshopsinmassachusetts"
# counted as a correspondent 59 times. No real name or agency has a
# seventeen-letter single word in it — "Representatives" is fifteen,
# "Administration" fourteen.
RUN_TOGETHER = re.compile(r"[A-Za-z]{17,}")


def readable(key: str) -> str:
    """The part of a URL a person would read, as words."""
    path = key.split("?", 1)[0]
    parts = [LEADING_UNDERSCORE.sub("", p) for p in path.split("/")[1:] if p]
    # Sites spell the same folder both ways — wysiwyg_uploaded and
    # wysiwyg-uploaded — so the furniture list is matched against one spelling
    # rather than needing an entry for each.
    parts = [p for p in parts
             if p and not PATH_NOISE.match(p.replace("_", "-"))
             and not HOSTNAME_SEGMENT.match(p)
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
    # Filenames written without separators: "fdaletter", "usdaletter03212013",
    # "20130624fhfaletter". The word has to be prised off its neighbours, or
    # the recipient stays glued to it and the date never meets a word boundary.
    #
    # Digits first, so that "usdaletter03212013" has become "usdaletter
    # 03212013" before the word split runs — otherwise neither side of
    # "letter" has the boundary a \b would need.
    # Six digits is the threshold because it frees "04252013" while leaving
    # "fy11" and "h1n1" alone.
    text = re.sub(r"(?<=[a-z])(\d{6,})", r" \1", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d{6,})(?=[a-z])", r"\1 ", text, flags=re.IGNORECASE)
    # One pass, both sides at once. Two separate substitutions split the same
    # word twice — "olivialetters" became "olivia letter s", because the
    # second rule fired on the "s" the first had just exposed.
    text = re.sub(r"([a-z]*)(letters?)([a-z0-9]*)",
                  lambda m: " ".join(p for p in m.groups() if p),
                  text, flags=re.IGNORECASE)
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
            parsed = date_type(year, month, day)
            # A letter cannot be dated after today. Eleven slugs in 29,316
            # parsed to 2027-2029 — "...letter to hhs 5 29 29" reads as a
            # two-digit year that is not one — and those few were enough to
            # report the whole corpus as running to 2029.
            if parsed > date_type.today():
                continue
            return parsed.isoformat(), "slug"
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
    if RUN_TOGETHER.search(cleaned):
        return None, None, cleaned
    if all(len(w) <= 2 for w in words):
        return None, None, cleaned
    # Four words is already a long way for a name; beyond that it is prose.
    if len(words) > 4:
        return None, None, cleaned
    return cleaned, cleaned.title(), ""


def known_recipient(text: str) -> tuple[str, str] | None:
    """Resolve a phrase ONLY against the known vocabulary, never by guesswork.

    Used to rescue a recipient that is sitting in the subject because the slug
    never said "letter to": a file called ``hhs-letter_041525.pdf`` names its
    recipient and no pattern was going to find it there. 211 letters were
    described as "hhs", "fda", "usps", "doj" or "cms" — each of them an
    agency this module can already name.

    Guessing is what makes this safe to do at all. Only a phrase already in
    the lookup is promoted, so a subject that merely reads like a name stays
    where it is.
    """
    words = re.sub(r"\s+", " ", (text or "")).strip().lower().split()
    for size in range(min(5, len(words)), 0, -1):
        probe = " ".join(words[:size])
        canon = RECIPIENT_LOOKUP.get(probe.rstrip("."))
        if canon:
            return probe, canon
    return None


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
        # When the slug has no "letter" token at all, split_role hands the
        # whole thing back as the topic and nothing as the remainder. Deriving
        # a tail from the same slug printed every such subject twice:
        # "files barrasso va letter1 files barrasso va letter1".
        tail = ""
        if from_letter:
            tail = re.sub(r"\b(letters?|to|of|on|re|regarding|from)\b", " ",
                          from_letter, flags=re.IGNORECASE)
            if recipient:
                tail = tail.replace(recipient.lower(), " ")
        subject = re.sub(r"\s+", " ", f"{topic} {tail}").strip()

    if subject and recipient_canonical:
        # Overflow that names the same correspondent is not a subject.
        # "letter to ag garland" resolves on "ag" and leaves "garland" behind,
        # which then describes the letter as "garland".
        same = known_recipient(subject)
        if same and same[1] == recipient_canonical:
            subject = " ".join(subject.split()[len(same[0].split()):])

    if subject:
        # The honorific belongs to the recipient, not to the subject. Without
        # this, "letter to secretary cardona" resolves Cardona correctly and
        # then describes the letter as "secretary" — 149 of them did.
        # Role verbs go the same way: "urging", "calling" describe the act of
        # writing, not what the letter is about.
        subject = " ".join(
            w for w in subject.split()
            if w.upper().strip(".") not in HONORIFICS
            and w.lower() not in ROLE_VERBS)
        # A subject made only of digits is a fragment of a filename.
        if not re.search(r"[a-z]{2}", subject, re.IGNORECASE):
            subject = ""
        # "dhs_ig" and "hud-ig-letter" name a department's Inspector General.
        # The department is the recipient and "ig" qualifies it; on its own it
        # describes nothing.
        if recipient_canonical and subject.lower() in {
                "ig", "oig", "inspector general", "sec", "secretary",
                "admin", "office", "dept", "department"}:
            subject = ""

    # A recipient stranded in the subject, because the slug never said
    # "letter to". Only promoted when the vocabulary already knows the name.
    if not recipient_canonical and subject:
        hit = known_recipient(subject)
        if hit:
            phrase, recipient_canonical = hit
            recipient = phrase
            remainder = subject.split()[len(phrase.split()):]
            subject = " ".join(remainder)

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
