"""Tests for the subject the explorer shows beside a letter.

The description is cut from the file name, so it carries whatever the person
saving the file happened to type. The case that prompted this::

    Mn liheap funds version 04 50 pm

where the only words that mean anything are "liheap funds" — LIHEAP being the
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
            tidy_subject("mn liheap funds version 04 50 pm"), "Liheap funds")

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
            "Uccs afrotc")

    def test_strips_a_trailing_date(self):
        # The letter's own date column already shows this.
        self.assertEqual(tidy_subject("rural health march 30 2020"),
                         "Rural health")
        self.assertEqual(tidy_subject("energy june 2020"), "Energy")

    def test_strips_a_trailing_id(self):
        self.assertEqual(tidy_subject("cjs nasa update 0418071"),
                         "Cjs nasa update")

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
    are Ig, Us, Fy, Ai, Un, Va, Ag, Co and No — none of them states. VA is
    Veterans Affairs far more often than Virginia.
    """

    def test_keeps_a_meaningful_two_letter_prefix(self):
        for subject in ("ig report on fema", "va hospital wait times",
                        "ai safety framework", "un human rights council",
                        "ag garland oversight", "us trade representative"):
            self.assertTrue(tidy_subject(subject).lower().startswith(
                subject.split()[0]), subject)

    def test_keeps_a_fiscal_year(self):
        self.assertEqual(tidy_subject("fy23 liheap funds"), "Fy23 liheap funds")

    def test_keeps_a_statute_number(self):
        # Short numbers carry meaning — and topics.py tags Tariffs on this one,
        # so losing the digits would lose the tag too.
        self.assertEqual(tidy_subject("section 301 tariffs"),
                         "Section 301 tariffs")

    def test_keeps_quill_when_it_is_not_an_id(self):
        # Quill Corp v. North Dakota is a real subject for a letter about tax.
        self.assertEqual(tidy_subject("quill bonding"), "Quill bonding")

    def test_strips_only_an_unambiguous_state_prefix(self):
        self.assertEqual(tidy_subject("mn liheap funds"), "Liheap funds")
        # ME is Maine and also a word; OK, IN, OR, CA, MA, PA likewise.
        self.assertEqual(tidy_subject("ok disaster declaration"),
                         "Ok disaster declaration")

    def test_does_not_strip_the_only_word(self):
        self.assertEqual(tidy_subject("mn"), "Mn")


if __name__ == "__main__":
    unittest.main()
