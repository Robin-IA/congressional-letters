"""Tests for the download-wrapper resolver.

The expensive part of this is network, and none of it is exercised here. What
is tested is the reasoning around the network: which captures are wrappers,
which wrapper already has its letter, and how a Wayback redirect is read —
because misreading one is what made the first version of this report every
Cantwell wrapper as having resolved to itself.

    python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

from resolve_wrappers import (  # noqa: E402
    WAYBACK_LOCATION,
    WRAPPER_PATH,
    name_key,
    path_of,
    requestable,
    settled,
    wrapper_slug,
    wrappers_and_targets,
)


class WrapperShape(unittest.TestCase):
    def test_recognises_the_senate_cms_wrapper(self):
        self.assertTrue(WRAPPER_PATH.match(
            "/download/20181109-letter-to-commerce-daeo-re-ross-conflict"))
        self.assertTrue(WRAPPER_PATH.match("/download/vpletter"))

    def test_does_not_claim_a_deeper_download_path(self):
        # /download/2019/letters/x.pdf is a file laid out in folders, not the
        # CMS endpoint, and it is already kept as a PDF.
        self.assertFalse(WRAPPER_PATH.match("/download/2019/letters/x.pdf"))

    def test_does_not_claim_an_announcement_path(self):
        self.assertFalse(WRAPPER_PATH.match("/oversight/letters/warren-asks"))

    def test_path_of_drops_the_host_and_query(self):
        self.assertEqual(
            path_of("https://www.warren.senate.gov/download/x?download=1"),
            "/download/x")


class NameKey(unittest.TestCase):
    def test_matches_a_slug_to_the_filename_it_names(self):
        # The wrapper slug and the file it redirects to are the same words
        # under different punctuation. This match is what settles 6,997 of
        # 13,708 wrappers without a single request.
        self.assertEqual(
            name_key("20181109-letter-to-commerce-daeo-re-ross-conflict"),
            name_key("2018.11.09 Letter to Commerce DAEO re Ross conflict.pdf"))

    def test_ignores_percent_encoding(self):
        self.assertEqual(name_key("letter%20to%20epa.pdf"),
                         name_key("Letter to EPA"))

    def test_keeps_two_different_letters_apart(self):
        self.assertNotEqual(name_key("letter-to-epa-on-pfas"),
                            name_key("letter-to-epa-on-lead"))

    def test_wrapper_slug_is_the_last_segment(self):
        self.assertEqual(
            wrapper_slug("https://www.schatz.senate.gov/download/vpletter"),
            "vpletter")


class ReadingARedirect(unittest.TestCase):
    """The bug this class exists for.

    Asking Wayback for a capture at a timestamp that is not an exact capture
    gets a 302 back to the nearest one OF THE SAME URL. The first version read
    that Location as the site's own redirect and recorded the wrapper as having
    resolved — to itself. Hence: follow hops, and believe only the bytes.
    """

    def test_reads_the_original_url_out_of_a_location(self):
        match = WAYBACK_LOCATION.match(
            "//web.archive.org/web/20200113183111id_/"
            "https://www.warren.senate.gov/imo/media/doc/letter.pdf")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("stamp"), "20200113183111")
        self.assertEqual(match.group("url"),
                         "https://www.warren.senate.gov/imo/media/doc/letter.pdf")

    def test_a_self_redirect_is_not_a_resolution(self):
        # Same URL, different capture. Reading this as the answer is the bug.
        wrapper = "https://www.cantwell.senate.gov/download/03122019-letter"
        match = WAYBACK_LOCATION.match(
            f"/web/20210101000000id_/{wrapper}&download=1")
        self.assertIsNotNone(match)
        self.assertTrue(match.group("url").startswith(wrapper))

    def test_ignores_a_location_that_is_not_a_wayback_url(self):
        self.assertIsNone(WAYBACK_LOCATION.match("/login?next=/download/x"))


class Requestable(unittest.TestCase):
    def test_encodes_the_spaces_senate_filenames_carry(self):
        # Wayback's Location repeats the original filename verbatim, and
        # urllib raises InvalidURL on a space. 15 of the first 22 wrappers
        # followed died this way.
        self.assertEqual(
            requestable("https://x.senate.gov/imo/media/doc/"
                        "2018.11.09 Letter to Commerce.pdf"),
            "https://x.senate.gov/imo/media/doc/"
            "2018.11.09%20Letter%20to%20Commerce.pdf")

    def test_does_not_escape_an_escape(self):
        already = "https://x.senate.gov/doc/letter%20to%20epa.pdf"
        self.assertEqual(requestable(already), already)

    def test_leaves_a_wayback_replay_url_alone(self):
        url = "https://web.archive.org/web/20200101000000id_/https://x.gov/a?b=1"
        self.assertEqual(requestable(url), url)


class WhatIsRemembered(unittest.TestCase):
    """A fact about the archive is cached. A fact about the afternoon is not.

    Caching a connection error as dead would quietly retire letters that are
    sitting right there — six of eighteen came back URLError during testing
    purely from how hard the host had just been hit.
    """

    def test_remembers_a_resolution(self):
        self.assertTrue(settled({"target": "https://x.gov/a.pdf"}))

    def test_remembers_a_404(self):
        # The crawler never followed the refresh; asking again cannot help.
        self.assertTrue(settled({"dead": "http-404"}))

    def test_remembers_a_body_that_is_not_a_pdf(self):
        self.assertTrue(settled({"dead": "not-a-pdf"}))

    def test_forgets_a_connection_error(self):
        self.assertFalse(settled({"dead": "URLError"}))
        self.assertFalse(settled({"dead": "timeout"}))

    def test_forgets_a_rate_limit_and_a_server_error(self):
        for dead in ("http-429", "http-503", "http-500"):
            self.assertFalse(settled({"dead": dead}), dead)


class Partition(unittest.TestCase):
    """The free pass, run against a cache built in a temporary directory."""

    def setUp(self):
        import json
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        host = "www.example.senate.gov"
        rows = [
            # A wrapper whose letter we already hold under its own URL.
            {"url": f"https://{host}/download/0101-letter-to-epa",
             "timestamp": "20200101000000", "mimetype": "text/html",
             "length": "8000", "digest": "A"},
            {"url": f"https://{host}/imo/media/doc/0101 Letter to EPA.pdf",
             "timestamp": "20200102000000", "mimetype": "application/pdf",
             "length": "90000", "digest": "B"},
            # A wrapper that is the only copy of its letter.
            {"url": f"https://{host}/download/0202-letter-to-fema",
             "timestamp": "20200201000000", "mimetype": "text/html",
             "length": "8000", "digest": "C"},
            # Not a wrapper at all.
            {"url": f"https://{host}/oversight/letters/asks-fema-for-answers",
             "timestamp": "20200301000000", "mimetype": "text/html",
             "length": "8000", "digest": "D"},
        ]
        (self.cache / f"{host}.json").write_text(
            json.dumps({"host": host, "rows": rows}), encoding="utf-8")
        self.members = [{"bioguide": "X000001", "host": host}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_separates_the_wrappers_that_need_a_request(self):
        orphans, matched = wrappers_and_targets(self.cache, self.members)
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(orphans), 1)
        self.assertTrue(orphans[0]["wrapper"].endswith("0202-letter-to-fema"))

    def test_an_announcement_page_is_not_a_wrapper(self):
        orphans, matched = wrappers_and_targets(self.cache, self.members)
        everything = [o["wrapper"] for o in orphans] + list(matched)
        self.assertFalse(any("oversight" in u for u in everything))

    def test_matches_against_pdfs_the_export_would_reject(self):
        # A target buried too deep for keep() is still the letter. The match
        # runs over the whole host cache for exactly this reason.
        import json

        host = "www.deep.senate.gov"
        (self.cache / f"{host}.json").write_text(json.dumps({"rows": [
            {"url": f"https://{host}/download/0303-letter-to-hhs",
             "timestamp": "20200301000000", "mimetype": "text/html",
             "length": "8000", "digest": "E"},
            {"url": f"https://{host}/a/letters/b/c/0303 letter to hhs.pdf",
             "timestamp": "20200302000000", "mimetype": "application/pdf",
             "length": "90000", "digest": "F"},
        ]}), encoding="utf-8")
        orphans, matched = wrappers_and_targets(
            self.cache, [{"bioguide": "X000002", "host": host}])
        self.assertEqual(len(orphans), 0)
        self.assertEqual(len(matched), 1)


if __name__ == "__main__":
    unittest.main()
