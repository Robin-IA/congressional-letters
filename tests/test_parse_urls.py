"""Tests for the URL parser.

Every URL here was observed in the harvest. The parser is the whole product —
only 24% of these PDFs have a text layer, so what the URL yields is what a
visitor sees — and it is almost all regular expressions, which is exactly the
code that needs cases pinned down.

    python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

from harvest import canonical, keep, letter_depth  # noqa: E402
from parse_urls import (  # noqa: E402
    canonical_recipient,
    find_date,
    parse,
    readable,
    split_role,
)


def letter(key, timestamp="20200101000000", mimetype="application/pdf"):
    return {"key": key, "url": "https://" + key, "timestamp": timestamp,
            "mimetype": mimetype, "length": "1000", "digest": "X"}


class Keep(unittest.TestCase):
    def test_rejects_a_captured_404_page(self):
        # These are convincing because the real document's path is inside them.
        self.assertFalse(keep(letter(
            'cantwell.senate.gov/404error.html?request=/news/'
            'medicare_deadline_letter_to_frist.pdf', mimetype="text/html")))
        self.assertFalse(keep(letter(
            "whitehouse.senate.gov/404/?go=404/404/download/"
            "2013-letter-to-president.pdf", mimetype="text/html")))

    def test_rejects_a_waf_block_page(self):
        self.assertFalse(keep(letter(
            "grassley.senate.gov/$(SERVE_403)/upload/Letter.pdf"
            "?referenceerror=18.9cc", mimetype="text/html")))

    def test_rejects_a_legacy_template_url(self):
        self.assertFalse(keep(letter(
            "schiff.house.gov/CA29/Templates/News/press_release_template.aspx"
            "?NRMODE=Published", mimetype="text/html")))

    def test_keeps_a_real_letter(self):
        self.assertTrue(keep(letter(
            "cantwell.senate.gov/imo/media/doc/2022 crab disaster letter.pdf")))

    def test_rejects_a_crawler_trap_under_a_letters_section(self):
        # Many sites have a SECTION called letters, and Wayback has crawled an
        # endless JavaScript-library path space beneath it. Unfiltered, these
        # made John Larson the most prolific correspondent in Congress with
        # 43,357 letters against Elizabeth Warren's 6,053.
        for url in (
            "larson.house.gov/media-center/op-eds-and-letters/esri/renderers/"
            "esri/esri/symbols/simplefillsymbol?page=3",
            "morelle.house.gov/media/letters-0/esri/renderers/dijit/tooltipdialog",
            "tiffany.house.gov/media/letters/esri/profiles/dojo/dom-class",
        ):
            self.assertFalse(keep(letter(url, mimetype="text/html")), url)

    def test_keeps_a_letter_one_level_under_a_letters_folder(self):
        # Durbin files his as /appropriations/letters/FY11_DefenseApprops.pdf,
        # where the word is one segment up rather than in the filename.
        self.assertTrue(keep(letter(
            "durbin.senate.gov/appropriations/letters/FY11_DefenseApprops.pdf")))
        self.assertEqual(letter_depth(
            "durbin.senate.gov/appropriations/letters/FY11_Defense.pdf"), 1)

    def test_rejects_a_paginated_list_view(self):
        self.assertFalse(keep(letter(
            "x.house.gov/media/letters?page=4", mimetype="text/html")))


class Canonical(unittest.TestCase):
    def test_collapses_viewer_variants(self):
        # Wyden's site serves one letter at four URLs.
        base = "wyden.senate.gov/download/-letter-to-gao-on-royalty-in-kind"
        for variant in ("?download=1", "?inline=google", "?inline=scribd", ""):
            self.assertEqual(
                canonical(f"https://www.wyden.senate.gov/download/"
                          f"-letter-to-gao-on-royalty-in-kind{variant}"), base)

    def test_strips_a_digest_prefix(self):
        # Klobuchar stores uploads as <uuid>/<32 hex>.<real name>.pdf
        self.assertEqual(
            canonical("https://www.klobuchar.senate.gov/public/_cache/files/0/2/"
                      "02ee4ad5/57bed48b2d231336a8e1c2a8af04116c."
                      "minnesota-delegation-letter-to-trump.pdf"),
            "klobuchar.senate.gov/public/_cache/files/0/2/02ee4ad5/"
            "minnesota-delegation-letter-to-trump.pdf")

    def test_strips_an_embed_suffix(self):
        self.assertEqual(
            canonical("https://www.barrasso.senate.gov/x-lead-letter-on-irs/embed"),
            "barrasso.senate.gov/x-lead-letter-on-irs")


class Readable(unittest.TestCase):
    def test_drops_path_furniture(self):
        self.assertEqual(
            readable("whitehouse.senate.gov/wp-content/uploads/imo/media/doc/"
                     "letter to mcgahn on law enforcement contacts.pdf"),
            "letter to mcgahn on law enforcement contacts")

    def test_splits_a_filename_written_without_separators(self):
        # Warren's older files are "fdaletter.pdf", "usdaletter03212013.pdf",
        # "20130624fhfaletter.pdf". Left glued, the recipient stays stuck to
        # the word and the date never meets a word boundary.
        self.assertEqual(readable("x.gov/a/fdaletter.pdf"), "fda letter")
        self.assertEqual(readable("x.gov/a/usdaletter03212013.pdf"),
                         "usda letter 03212013")
        self.assertEqual(readable("x.gov/a/20130624fhfaletter.pdf"),
                         "20130624 fhfa letter")

    def test_does_not_split_short_alphanumerics(self):
        # Six digits is the threshold so that these survive intact.
        self.assertIn("fy11", readable("x.gov/a/fy11_defense_letter.pdf"))
        self.assertIn("h1n1", readable("x.gov/a/h1n1-letter-to-cdc.pdf"))

    def test_drops_hash_directories(self):
        text = readable("klobuchar.senate.gov/public/_cache/files/0/2/02ee4ad5-"
                        "cc35-4abd-9a16-0f51902e5d4a/"
                        "minnesota-delegation-letter-to-president-trump.pdf")
        self.assertEqual(text,
                         "minnesota delegation letter to president trump")


class Dates(unittest.TestCase):
    def test_reads_an_iso_date(self):
        self.assertEqual(
            find_date("x", "2021 02 23 sw hj letter to jc")[0], "2021-02-23")

    def test_reads_a_month_first_date(self):
        self.assertEqual(
            find_date("x", "letter to senate judiciary committee 9 27 2023")[0],
            "2023-09-27")

    def test_reads_a_run_together_date(self):
        self.assertEqual(
            find_date("x", "02142020 letter to epa on pfas action plan")[0],
            "2020-02-14")

    def test_reads_a_run_together_year_first_date(self):
        self.assertEqual(find_date("x", "20130624 fhfa letter")[0], "2013-06-24")

    def test_reads_a_two_digit_year(self):
        self.assertEqual(find_date("x", "042310 letter to reid")[0],
                         "2010-04-23")

    def test_falls_back_to_a_date_in_the_path(self):
        iso, source = find_date(
            "klobuchar.senate.gov/public/2016/1/klobuchar-collins-lead-letter",
            "klobuchar collins lead letter")
        self.assertEqual(iso, "2016-01-01")
        self.assertEqual(source, "path-month")

    def test_a_month_only_path_is_not_a_day(self):
        # Reporting /2016/1/ as a precise 1 January would put every January
        # letter on New Year's Day.
        _, source = find_date("x.gov/public/2016/1/some-letter", "some letter")
        self.assertEqual(source, "path-month")

    def test_rejects_an_impossible_date(self):
        self.assertIsNone(find_date("x", "letter 13 45 2020")[0])

    def test_rejects_a_future_date(self):
        # "provider relief fund letter to hhs 5 29 29" reads as 2029, and a
        # handful of these reported the whole corpus as running to 2029.
        self.assertIsNone(
            find_date("x", "provider relief fund letter to hhs 5 29 29")[0])
        self.assertIsNone(find_date("x", "letter 12 06 29")[0])


class Recipients(unittest.TestCase):
    def test_expands_an_agency_abbreviation(self):
        self.assertEqual(canonical_recipient("doj")[1], "Department of Justice")
        self.assertEqual(canonical_recipient("epa")[1],
                         "Environmental Protection Agency")

    def test_maps_an_official_to_their_office(self):
        # Pruitt drew 18 letters and "EPA" 17; they are one correspondent.
        self.assertEqual(canonical_recipient("pruitt")[1],
                         "Environmental Protection Agency")
        self.assertEqual(canonical_recipient("garland")[1],
                         "Department of Justice")
        self.assertEqual(canonical_recipient("president trump")[1],
                         "The President")

    def test_leaves_an_ambiguous_surname_alone(self):
        # Wheeler administered both the EPA and the FCC, so a bare surname
        # cannot be resolved to an office.
        self.assertEqual(canonical_recipient("wheeler")[1], "Wheeler")

    def test_rejects_initials_and_debris(self):
        # "sw hj letter to jc" is one office's file-naming scheme.
        self.assertIsNone(canonical_recipient("jc")[0])
        self.assertIsNone(canonical_recipient("re")[0])
        self.assertIsNone(canonical_recipient("v2")[0])

    def test_splits_a_recipient_that_ran_into_its_subject(self):
        # Slugs with no "on" or "re" run the name into the description. Where
        # the name is a known agency it can be peeled off exactly.
        name, canon, overflow = canonical_recipient(
            "eac election worker recruitment guidance")
        self.assertEqual(canon, "Election Assistance Commission")
        self.assertEqual(overflow, "election worker recruitment guidance")

    def test_refuses_to_guess_an_unknown_long_recipient(self):
        # Where the name is NOT known, taking the first word or two would be a
        # guess, and a wrong recipient is worse than none.
        name, canon, overflow = canonical_recipient(
            "sprocket widget authority annual compliance guidance")
        self.assertIsNone(canon)
        self.assertTrue(overflow)

    def test_refuses_prose_as_a_name(self):
        self.assertIsNone(canonical_recipient(
            "the many agencies that have not yet responded to us")[0])

    def test_prefers_the_longest_known_name(self):
        name, canon, _ = canonical_recipient("department of justice")
        self.assertEqual(canon, "Department of Justice")


class Roles(unittest.TestCase):
    def test_a_topic_word_is_not_a_cosigner(self):
        # "2022 crab disaster letter" once yielded three co-signers named
        # 2022, crab and disaster, because anything unrecognised was taken as
        # a name. A co-signer has to be on the roster.
        role, cosigners, _, _, topic = split_role(
            "2022 crab disaster letter", "Cantwell",
            frozenset({"cantwell", "murray"}))
        self.assertEqual(cosigners, [])
        self.assertIn("crab", topic)

    def test_reads_lead_and_cosigners(self):
        role, cosigners, qualifiers, rest, _topic = split_role(
            "barrasso arrington lead bicameral letter to biden admin on mexico",
            "Barrasso", frozenset({"barrasso", "arrington"}))
        self.assertEqual(role, "leads")
        self.assertIn("arrington", cosigners)
        self.assertNotIn("barrasso", cosigners)
        self.assertIn("bicameral", qualifiers)
        self.assertTrue(rest.startswith("letter to"))

    def test_reads_joins(self):
        role, _, _, _, _ = split_role(
            "barrasso joins letter asking justice dept to investigate",
            "Barrasso", frozenset({"barrasso"}))
        self.assertEqual(role, "joins")

    def test_reads_a_verb_after_the_word_letter(self):
        role, _, _, _, _ = split_role(
            "raskin joins letter urging trump raise khashoggi", "Raskin",
            frozenset({"raskin"}))
        self.assertEqual(role, "joins")

    def test_handles_a_slug_with_no_role(self):
        role, cosigners, _, rest, _topic = split_role(
            "letter to mcgahn on law enforcement contacts", "Whitehouse",
            frozenset({"whitehouse"}))
        self.assertIsNone(role)
        self.assertEqual(cosigners, [])
        self.assertTrue(rest.startswith("letter to"))


class EndToEnd(unittest.TestCase):
    def test_parses_a_rich_slug(self):
        out = parse(letter(
            "cantwell.senate.gov/download/02142020-letter-to-epa-on-pfas-"
            "action-plan", mimetype="text/html"), "Cantwell")
        self.assertEqual(out["date"], "2020-02-14")
        self.assertEqual(out["date_precision"], "day")
        self.assertEqual(out["recipient_canonical"],
                         "Environmental Protection Agency")
        self.assertIn("pfas", out["subject"])

    def test_dates_an_undated_letter_by_its_capture(self):
        out = parse(letter("cantwell.senate.gov/imo/media/doc/dot_letter.pdf",
                           timestamp="20160312094500"), "Cantwell")
        self.assertIsNone(out["date"])
        self.assertEqual(out["date_best"], "2016-03-12")
        self.assertEqual(out["date_precision"], "captured-by")

    def test_leaves_a_topic_named_letter_without_a_recipient(self):
        # "2022 crab disaster letter" names no recipient, and inventing one
        # would be worse than leaving it empty.
        out = parse(letter(
            "cantwell.senate.gov/imo/media/doc/2022 crab disaster letter.pdf"),
            "Cantwell", frozenset({"cantwell"}))
        self.assertIsNone(out["recipient_canonical"])
        self.assertIsNotNone(out["subject"])


if __name__ == "__main__":
    unittest.main()
