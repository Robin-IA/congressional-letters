"""Tests for the subject the explorer shows beside a letter.

The description is cut from the file name, so it carries whatever the person
saving the file happened to type. The case that prompted this::

    Mn liheap funds version 04 50 pm

where the only words that mean anything are "liheap funds" â€” LIHEAP being the
Low Income Home Energy Assistance Program, which is what the letter is about.
"version 04 50 pm" is the moment somebody pressed save.

Half of these tests are the opposite case: things that look like debris and
are not. A two-letter token at the front is usually meaningful, and stripping
it loses the subject.

    python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

from build_site import tidy_subject  # noqa: E402


class Debris(unittest.TestCase):
    def test_the_case_that_started_this(self):
        self.assertEqual(
            tidy_subject("mn liheap funds version 04 50 pm"), "LIHEAP Funds")

    def test_strips_a_save_timestamp(self):
        # 469 subjects carried a "version", 259 a clock time.
        self.assertEqual(tidy_subject("vitamin version 03 47 pm"), "Vitamin")
        self.assertEqual(
            tidy_subject("terminations version 10 07 am"), "Terminations")

    def test_the_clock_is_read_before_the_version(self):
        # The version pattern's trailing \d* used to eat the hour and leave
        # the minutes behind: "...version 04 50 pm" tidied to "Liheap funds
        # 50", which is worse than the string it started from.
        self.assertNotIn("50", tidy_subject("liheap funds version 04 50 pm"))

    def test_strips_a_document_system_id(self):
        self.assertEqual(
            tidy_subject("quill l19597 uccs afrotc version 01 35 pm"),
            "Uccs AFROTC")

    def test_strips_a_trailing_date(self):
        # The letter's own date column already shows this.
        self.assertEqual(tidy_subject("rural health march 30 2020"),
                         "Rural Health")
        self.assertEqual(tidy_subject("energy june 2020"), "Energy")

    def test_strips_a_trailing_id(self):
        self.assertEqual(tidy_subject("cjs nasa update 0418071"),
                         "Cjs NASA Update")

    def test_drops_a_subject_that_is_only_digits(self):
        # "7460" and "529 4350" are fragments of a file name. Showing nothing
        # is better than showing that.
        self.assertIsNone(tidy_subject("7460"))
        self.assertIsNone(tidy_subject("529 4350"))

    def test_a_single_word_is_a_fine_subject(self):
        self.assertEqual(tidy_subject("wildfires"), "Wildfires")


class NotDebris(unittest.TestCase):
    """Things that look strippable and are not.

    Across the 2,588 subjects starting with a two-letter token, the commonest
    are Ig, Us, Fy, Ai, Un, Va, Ag, Co and No â€” none of them states. VA is
    Veterans Affairs far more often than Virginia.
    """

    def test_keeps_a_meaningful_two_letter_prefix(self):
        for subject in ("ig report on fema", "va hospital wait times",
                        "ai safety framework", "un human rights council",
                        "ag garland oversight", "us trade representative"):
            self.assertTrue(tidy_subject(subject).lower().startswith(
                subject.split()[0]), subject)

    def test_keeps_a_fiscal_year(self):
        self.assertEqual(tidy_subject("fy23 liheap funds"), "FY23 LIHEAP Funds")

    def test_keeps_a_statute_number(self):
        # Short numbers carry meaning â€” and topics.py tags Tariffs on this one,
        # so losing the digits would lose the tag too.
        self.assertEqual(tidy_subject("section 301 tariffs"),
                         "Section 301 Tariffs")

    def test_keeps_quill_when_it_is_not_an_id(self):
        # Quill Corp v. North Dakota is a real subject for a letter about tax.
        self.assertEqual(tidy_subject("quill bonding"), "Quill Bonding")

    def test_strips_only_an_unambiguous_state_prefix(self):
        self.assertEqual(tidy_subject("mn liheap funds"), "LIHEAP Funds")
        # ME is Maine and also a word; OK, IN, OR, CA, MA, PA likewise.
        self.assertEqual(tidy_subject("ok disaster declaration"),
                         "Ok Disaster Declaration")

    def test_does_not_strip_the_only_word(self):
        self.assertEqual(tidy_subject("mn"), "MN")


if __name__ == "__main__":
    unittest.main()


class Capitals(unittest.TestCase):
    """Slugs are lower case throughout, so every subject arrived looking like
    a file name. Title case with the abbreviations left as capitals reads as a
    caption instead."""

    def test_agency_abbreviations_shout(self):
        self.assertEqual(
            tidy_subject("odni doj fbi dhs nsa election security briefings"),
            "ODNI DOJ FBI DHS NSA Election Security Briefings")

    def test_names_are_capitalised(self):
        self.assertEqual(tidy_subject("kash patel jtf october"),
                         "Kash Patel JTF October")
        self.assertEqual(tidy_subject("todd blanche nyc mayor office meeting"),
                         "Todd Blanche NYC Mayor Office Meeting")

    def test_two_letter_words_that_are_really_abbreviations(self):
        # In letters from congressional offices "us" is the United States,
        # "ag" the Attorney General, "va" Veterans Affairs, "un" the UN.
        for given, want in (("ai safety framework", "AI Safety Framework"),
                            ("un human rights council", "UN Human Rights Council"),
                            ("va hospital wait times", "VA Hospital Wait Times"),
                            ("ag garland oversight", "AG Garland Oversight"),
                            ("us trade representative", "US Trade Representative")):
            self.assertEqual(tidy_subject(given), want)

    def test_matches_whole_tokens_only(self):
        # "un" is the United Nations; "unavailable" is a word.
        self.assertEqual(tidy_subject("unavailable records request"),
                         "Unavailable Records Request")

    def test_fiscal_year_and_state_code(self):
        self.assertEqual(tidy_subject("fy23 liheap funds"), "FY23 LIHEAP Funds")
        # A state code is shouted where it survives. At the FRONT it does not
        # survive at all — the office filed the letter under its own state and
        # the reader is already on that member's page.
        self.assertEqual(tidy_subject("wildfire funds mn"), "Wildfire Funds MN")
        self.assertEqual(tidy_subject("mn wildfire funds"), "Wildfire Funds")


class RunTogether(unittest.TestCase):
    def test_splits_a_slug_with_no_separators(self):
        self.assertEqual(tidy_subject("todojonmncivilrightsinvestigation"),
                         "DOJ MN Civil Rights Investigation")

    def test_leaves_long_words_alone(self):
        # The reason splitting is deliberately hard to satisfy: most long
        # tokens here are simply long words, and mangling those to rescue the
        # handful of real run-togethers would be a bad trade.
        for word in ("appropriations request", "infrastructure implementation",
                     "transportation reauthorization", "accountability office",
                     "reconciliation instructions"):
            self.assertEqual(tidy_subject(word).lower(), word.lower(), word)
