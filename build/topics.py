#!/usr/bin/env python3
"""A controlled vocabulary of subjects and agencies, and a tagger for it.

Why the raw description is not enough
-------------------------------------
The description beside each letter is cut from its file name, and file names
are written by staffers in a hurry. Two real examples::

    Ne support wildfires
    Wildfire disasters fmags hmgp

The first is one topic wrapped in a state abbreviation and a verb; the second
is two topics followed by the acronyms for FEMA's Fire Management Assistance
Grants and Hazard Mitigation Grant Program. A reader wants "Wildfires" and
"Disasters", not either of those strings.

So each letter is also tagged against a vocabulary. The tags are what search
matches and what the interface can show; the raw description stays as a
fallback for anything the vocabulary does not cover.

Why a vocabulary rather than the frequent words
-----------------------------------------------
Ranking the words that actually appear puts "media", "document", "evo" and
"cache" at the top — path furniture — alongside generic verbs like
"requesting" and the surnames of prolific senders. Frequency finds candidates;
it cannot tell a subject from a directory name. Every term here was chosen,
and every one is checked against the corpus by ``verify``.

Singular and plural are one term. So are an agency's abbreviation and its
name: a letter about FEMA is found by searching either, which is the point of
cross-indexing them.
"""

from __future__ import annotations

import re
import sys

# Suffix rules that do not mangle the words this corpus actually contains.
# A naive "drop a trailing s" turns nexus into nexu and coronavirus into
# coronaviru, both of which appeared in the first pass over real subjects.
_KEEP_WHOLE = ("us", "is", "ss", "as", "ous")


def singular(word: str) -> str:
    """Fold a plural onto its singular, conservatively."""
    w = word.lower()
    if any(w.endswith(suffix) for suffix in _KEEP_WHOLE):
        return w
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ches") or w.endswith("shes") or w.endswith("xes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


# ---------------------------------------------------------------- the subjects
#
# canonical label -> the forms that should find it. Forms are matched after
# the same singular() fold, so "wildfires" finds "wildfire" without listing it.
SUBJECTS: dict[str, tuple[str, ...]] = {
    # health
    "COVID-19": ("covid", "covid-19", "covid19", "coronavirus", "pandemic",
                 "sars-cov-2"),
    "Vaccines": ("vaccine", "vaccination", "immunization", "immunisation"),
    "Opioids": ("opioid", "fentanyl", "overdose", "naloxone", "oxycontin"),
    "Medicare": ("medicare",),
    "Medicaid": ("medicaid", "chip"),
    "Mental health": ("mental health", "suicide", "behavioral health"),
    "Prescription drugs": ("prescription", "drug pricing", "insulin",
                           "pharmaceutical", "pbm"),
    "Health care": ("health care", "healthcare", "hospital", "aca",
                    "affordable care"),
    "Abortion": ("abortion", "reproductive", "mifepristone", "roe"),
    "Public health": ("public health", "disease", "outbreak"),

    # environment and energy
    "Wildfires": ("wildfire", "forest fire", "fire season"),
    "Climate": ("climate", "greenhouse gas", "emissions", "carbon",
                "global warming"),
    "Water": ("water", "drinking water", "watershed", "drought", "aquifer"),
    "PFAS": ("pfas", "forever chemical", "pfoa", "pfos"),
    "Pollution": ("pollution", "contamination", "toxic", "superfund",
                  "clean air"),
    "Energy": ("energy", "electricity", "grid", "pipeline", "oil", "gas",
               "renewable", "solar", "wind power"),
    "Nuclear": ("nuclear", "radioactive", "uranium"),
    "Public lands": ("public land", "national park", "wilderness", "blm land",
                     "monument"),
    "Wildlife": ("wildlife", "endangered species", "habitat", "fishery",
                 "salmon"),
    "Disasters": ("disaster", "hurricane", "flood", "tornado", "earthquake",
                  "fema assistance", "emergency declaration"),

    # economy
    "Taxes": ("tax", "taxation", "irs audit", "tax credit"),
    "Tariffs": ("tariff", "trade war", "section 232", "section 301"),
    "Trade": ("trade", "export", "import", "usmca", "wto"),
    "Inflation": ("inflation", "cost of living", "price gouging", "prices"),
    "Banking": ("bank", "banking", "credit union", "fdic insurance"),
    "Housing": ("housing", "mortgage", "rent", "homeless", "eviction"),
    "Student loans": ("student loan", "student debt", "tuition", "pell"),
    "Small business": ("small business", "ppp", "sba loan"),
    "Jobs": ("jobs", "unemployment", "workforce", "labor", "wage", "union"),
    "Social Security": ("social security", "ssi", "ssdi"),
    "Crypto": ("crypto", "cryptocurrency", "bitcoin", "stablecoin", "ftx"),

    # security and justice
    "Immigration": ("immigration", "immigrant", "asylum", "refugee", "daca",
                    "deportation", "visa", "border wall"),
    "Border": ("border", "border security", "migrant"),
    "Guns": ("gun", "firearm", "atf rule", "assault weapon", "gun violence"),
    "Policing": ("police", "policing", "law enforcement"),
    "Prisons": ("prison", "incarceration", "bureau of prisons", "inmate"),
    "Civil rights": ("civil right", "voting right", "discrimination",
                     "disability right"),
    "Elections": ("election", "voting", "ballot", "redistricting",
                  "election security"),
    "Privacy": ("privacy", "surveillance", "data broker", "fisa"),
    "Cybersecurity": ("cyber", "cybersecurity", "ransomware", "hacking",
                      "data breach"),
    "Oversight": ("oversight", "investigation", "subpoena", "inspector general",
                  "whistleblower", "foia"),
    "Ethics": ("ethics", "conflict of interest", "insider trading",
               "stock trading"),

    # defence and foreign
    "Defense": ("defense", "pentagon", "military", "armed force",
                "national defense"),
    "Veterans": ("veteran", "va care", "gi bill"),
    "Ukraine": ("ukraine", "ukrainian"),
    "Russia": ("russia", "russian", "putin"),
    "China": ("china", "chinese", "ccp", "taiwan"),
    "Israel and Gaza": ("israel", "gaza", "palestinian", "hamas"),
    "Iran": ("iran", "iranian"),
    "Afghanistan": ("afghanistan", "afghan"),
    "Foreign aid": ("foreign aid", "usaid", "humanitarian assistance"),

    # infrastructure and services
    "Broadband": ("broadband", "internet access", "rural broadband",
                  "digital divide"),
    "Transportation": ("transportation", "highway", "transit", "rail",
                       "amtrak", "bridge"),
    "Aviation": ("aviation", "airline", "airport", "faa certification",
                 "boeing"),
    "Postal Service": ("postal", "usps delay", "mail delivery"),
    "Agriculture": ("agriculture", "farm", "farmer", "crop", "livestock",
                    "snap benefit", "farm bill"),
    "Education": ("education", "school", "teacher", "student", "university",
                  "title ix"),
    "Childcare": ("childcare", "child care", "head start"),
    "Technology": ("artificial intelligence", "social media", "algorithm",
                   "big tech", "antitrust"),
    "Telecom": ("spectrum", "telecom", "robocall", "net neutrality"),

    # government operations
    "Appropriations": ("appropriation", "funding request", "budget request",
                       "continuing resolution", "shutdown", "earmark"),
    "Federal workforce": ("federal employee", "federal worker", "rif",
                          "hiring freeze", "telework"),
    "Nominations": ("nomination", "nominee", "confirmation"),
    "Regulation": ("rulemaking", "proposed rule", "comment period",
                   "regulatory"),
}

# ------------------------------------------------------------- the agencies
#
# Abbreviation and full name index to the same label, so either finds the
# letter. Only agencies that actually appear as SUBJECTS of letters are here;
# the recipient vocabulary in parse_urls.py is a separate, larger list.
AGENCIES: dict[str, tuple[str, ...]] = {
    "FEMA": ("fema", "federal emergency management agency"),
    "EPA": ("epa", "environmental protection agency"),
    "FDA": ("fda", "food and drug administration"),
    "CDC": ("cdc", "centers for disease control"),
    "CMS": ("cms", "centers for medicare and medicaid services"),
    "HHS": ("hhs", "health and human services"),
    "DHS": ("dhs", "homeland security"),
    "DOJ": ("doj", "justice department", "department of justice"),
    "FBI": ("fbi", "federal bureau of investigation"),
    "ICE": ("ice detention", "immigration and customs enforcement"),
    "CBP": ("cbp", "customs and border protection"),
    "USDA": ("usda", "department of agriculture"),
    "VA": ("veterans affairs", "va hospital"),
    "DOD": ("dod", "department of defense"),
    "DOE": ("doe", "department of energy"),
    "DOT": ("dot", "department of transportation"),
    "FAA": ("faa", "federal aviation administration"),
    "FCC": ("fcc", "federal communications commission"),
    "FTC": ("ftc", "federal trade commission"),
    "SEC": ("sec filing", "securities and exchange commission"),
    "IRS": ("irs", "internal revenue service"),
    "SSA": ("ssa", "social security administration"),
    "HUD": ("hud", "housing and urban development"),
    "USPS": ("usps", "postal service"),
    "NIH": ("nih", "national institutes of health"),
    "NOAA": ("noaa", "national oceanic and atmospheric"),
    "GAO": ("gao", "government accountability office"),
    "OMB": ("omb", "office of management and budget"),
    "SBA": ("sba", "small business administration"),
    "NASA": ("nasa",),
    "Army Corps": ("army corps", "usace", "corps of engineers"),
    "Federal Reserve": ("federal reserve", "the fed"),
}


def _build_index() -> dict[str, list[tuple[str, str]]]:
    """first-word -> [(normalised phrase, label)], longest phrases first."""
    index: dict[str, list[tuple[str, str]]] = {}
    for mapping in (SUBJECTS, AGENCIES):
        for label, forms in mapping.items():
            for form in forms:
                words = [singular(w) for w in form.split()]
                if not words:
                    continue
                index.setdefault(words[0], []).append((" ".join(words), label))
    for key in index:
        index[key].sort(key=lambda pair: -len(pair[0].split()))
    return index


INDEX = _build_index()
WORD = re.compile(r"[a-z][a-z0-9'-]*")


def tag(text: str, limit: int = 4) -> list[str]:
    """The vocabulary terms a description is about, most specific first.

    Multi-word forms win over single words at the same position, so
    "social security" is one tag rather than two unrelated ones.
    """
    if not text:
        return []
    words = [singular(w) for w in WORD.findall(text.lower())]
    found: list[str] = []
    i = 0
    while i < len(words):
        for phrase, label in INDEX.get(words[i], ()):
            size = len(phrase.split())
            if " ".join(words[i:i + size]) == phrase:
                if label not in found:
                    found.append(label)
                i += size - 1
                break
        i += 1
    return found[:limit]


def verify(samples: list[str]) -> dict[str, int]:
    """How often each term fires, so a term that never fires can be cut."""
    counts = {label: 0 for label in list(SUBJECTS) + list(AGENCIES)}
    for text in samples:
        for label in tag(text, limit=99):
            counts[label] += 1
    return counts


if __name__ == "__main__":
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    texts = []
    for path in (root / "payload" / "member").glob("*.json"):
        for row in json.loads(path.read_text(encoding="utf-8")):
            if row[6]:
                texts.append(row[6])
    print(f"{len(texts):,} descriptions", file=sys.stderr)
    counts = verify(texts)
    tagged = sum(1 for t in texts if tag(t))
    print(f"{tagged:,} tagged ({tagged / max(1, len(texts)) * 100:.1f}%)",
          file=sys.stderr)
    dead = [label for label, n in counts.items() if n == 0]
    print(f"terms that never fire: {len(dead)}  {dead[:12]}", file=sys.stderr)
    for label, n in sorted(counts.items(), key=lambda kv: -kv[1])[:30]:
        print(f"  {n:>6}  {label}", file=sys.stderr)
