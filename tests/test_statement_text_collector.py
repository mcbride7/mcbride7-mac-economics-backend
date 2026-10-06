import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import requests
import statement_text_collector as stc

LONG_PARA = ("Recent indicators suggest that economic activity has been expanding at a solid pace and the "
             "Committee decided to maintain the target range for the policy rate. ") * 4

PAGE = f"""
<html><head><title>x</title><script>var a = "SHOULD NOT APPEAR";</script><style>p{{}}</style></head>
<body>
 <nav><ul><li>Home</li><li>About the Fed and a long menu item that is long enough to pass</li></ul></nav>
 <header><p>Site header paragraph that should be skipped entirely by the parser.</p></header>
 <main>
   <h1>FOMC statement</h1>
   <p>{LONG_PARA}</p>
   <p>Share</p>
   <p>The Committee   will   continue to assess &amp; monitor incoming data.</p>
   <p>The Committee will continue to assess &amp; monitor incoming data.</p>
   <ul><li>Voting for the monetary policy action were several members of the Committee.</li></ul>
 </main>
 <footer><p>Footer paragraph that should be skipped entirely by the parser as well.</p></footer>
</body></html>
"""


def resp(status=200, text="", ctype="text/html; charset=utf-8"):
    m = MagicMock()
    m.status_code = status
    m.text = text
    m.headers = {"Content-Type": ctype}
    return m


class TestExtract(unittest.TestCase):
    def test_extracts_body_and_skips_chrome(self):
        t = stc.extract_text(PAGE)
        self.assertIn("expanding at a solid pace", t)
        self.assertIn("Voting for the monetary policy action", t)
        self.assertNotIn("SHOULD NOT APPEAR", t)
        self.assertNotIn("Site header paragraph", t)
        self.assertNotIn("Footer paragraph", t)
        self.assertNotIn("menu item", t)
        self.assertNotIn("\nShare\n", "\n" + t + "\n")

    def test_entities_whitespace_and_dedup(self):
        t = stc.extract_text(PAGE)
        self.assertIn("assess & monitor incoming data.", t)       # &amp; decoded, spaces collapsed
        self.assertEqual(t.count("assess & monitor incoming data."), 1)  # duplicate paragraph dropped

    def test_malformed_html_does_not_crash(self):
        self.assertIsInstance(stc.extract_text("<p>unclosed <b>tags <p>more"), str)
        self.assertEqual(stc.extract_text(""), "")
        self.assertEqual(stc.extract_text(None), "")

    def test_nul_bytes_removed(self):
        html_ = "<p>" + "A perfectly normal sentence with a stray \x00 NUL byte inside it. " * 3 + "</p>"
        self.assertNotIn("\x00", stc.extract_text(html_))

    def test_length_capped(self):
        big = "<p>" + ("A sentence that is reasonably long. " * 5000) + "</p>"
        self.assertLessEqual(len(stc.extract_text(big)), stc.MAX_CHARS)


class TestRobots(unittest.TestCase):
    def test_disallowed_path(self):
        with patch.object(stc.requests, "get", return_value=resp(200, "User-agent: *\nDisallow: /secret/")):
            r = stc.RobotsCache()
            self.assertEqual(r.allowed("https://bank.example/secret/doc.htm"), (False, "robots_disallowed"))
            self.assertEqual(r.allowed("https://bank.example/public/doc.htm"), (True, None))

    def test_missing_robots_means_allowed(self):
        with patch.object(stc.requests, "get", return_value=resp(404)):
            self.assertEqual(stc.RobotsCache().allowed("https://bank.example/x"), (True, None))

    def test_server_error_means_not_allowed(self):
        with patch.object(stc.requests, "get", return_value=resp(503)):
            self.assertEqual(stc.RobotsCache().allowed("https://bank.example/x"), (False, "robots_unreachable"))

    def test_network_error_means_not_allowed(self):
        with patch.object(stc.requests, "get", side_effect=requests.ConnectionError("boom")):
            self.assertEqual(stc.RobotsCache().allowed("https://bank.example/x"), (False, "robots_unreachable"))

    def test_robots_fetched_once_per_host(self):
        with patch.object(stc.requests, "get", return_value=resp(404)) as g:
            r = stc.RobotsCache()
            for i in range(5):
                r.allowed(f"https://bank.example/{i}")
            self.assertEqual(g.call_count, 1)

    def test_blanket_disallow(self):
        with patch.object(stc.requests, "get", return_value=resp(200, "User-agent: *\nDisallow: /")):
            self.assertEqual(stc.RobotsCache().allowed("https://bank.example/a")[0], False)


class TestFetch(unittest.TestCase):
    def robots_ok(self):
        r = MagicMock()
        r.allowed.return_value = (True, None)
        return r

    def test_ok(self):
        with patch.object(stc, "get_with_retry", return_value=resp(200, PAGE)):
            status, text = stc.fetch_statement("https://b/x", self.robots_ok())
        self.assertEqual(status, "ok")
        self.assertIn("solid pace", text)

    def test_robots_blocks_without_fetching(self):
        r = MagicMock()
        r.allowed.return_value = (False, "robots_disallowed")
        with patch.object(stc, "get_with_retry") as g:
            self.assertEqual(stc.fetch_statement("https://b/x", r), ("robots_disallowed", None))
            g.assert_not_called()

    def test_pdf_skipped(self):
        with patch.object(stc, "get_with_retry", return_value=resp(200, "%PDF", "application/pdf")):
            self.assertEqual(stc.fetch_statement("https://b/x.pdf", self.robots_ok()), ("not_html", None))

    def test_too_short(self):
        with patch.object(stc, "get_with_retry", return_value=resp(200, "<p>Tiny page with one short sentence here.</p>")):
            status, _ = stc.fetch_statement("https://b/x", self.robots_ok())
        self.assertEqual(status, "too_short")

    def test_fetch_error(self):
        with patch.object(stc, "get_with_retry", side_effect=requests.HTTPError("403")):
            self.assertEqual(stc.fetch_statement("https://b/x", self.robots_ok()), ("fetch_error", None))


class TestPickCandidates(unittest.TestCase):
    def test_filters_non_statements_and_caps(self):
        rows = [{"central_bank": "FED", "title": "FOMC statement %d" % i,
                 "published_at": "2025-%02d-01" % (i % 12 + 1), "link": "f%d" % i} for i in range(20)]
        rows += [{"central_bank": "FED", "title": "Chair speech on monetary policy", "published_at": "2025-01-01", "link": "sp"}]
        rows += [{"central_bank": "FED", "title": "Board approves application", "published_at": "2025-01-01", "link": "ap"}]
        picked = stc.pick_candidates(rows)
        links = {p["link"] for p in picked}
        self.assertNotIn("sp", links)
        self.assertNotIn("ap", links)
        self.assertLessEqual(len(picked), stc.MAX_PER_BANK_PER_RUN)


class TestRunEndToEnd(unittest.TestCase):
    def setUp(self):
        # independent of whichever test module ran before it
        if os.path.exists("data/mac_economics.db"):
            os.remove("data/mac_economics.db")

    def test_full_run_records_every_outcome(self):
        import db
        db.init_db()
        sid = db.upsert_source("T", "https://t", "central_bank", 1, "X")
        db.insert_policy_statement("FED", "Federal Reserve issues FOMC statement", "Wed, 17 Sep 2025 14:00:00 EDT",
                                   "https://good.example/a", "s", sid)
        db.insert_policy_statement("ECB", "Monetary policy decisions", "Thu, 05 Jun 2025 13:45:00 +0200",
                                   "https://blocked.example/b", "s", sid)
        db.insert_policy_statement("BOE", "Monetary Policy Summary", "Thu, 01 May 2025 12:00:00 +0100",
                                   "https://broken.example/c", "s", sid)
        db.insert_policy_statement("FED", "Chair speech", "Wed, 17 Sep 2025 10:00:00 EDT",
                                   "https://good.example/speech", "s", sid)

        def fake_get(url, **kw):
            if url.endswith("/robots.txt"):
                if "blocked.example" in url:
                    return resp(200, "User-agent: *\nDisallow: /")
                return resp(404)
            raise AssertionError("blocked host must not be fetched: " + url)

        def fake_retry(url, **kw):
            if "broken.example" in url:
                raise requests.HTTPError("500")
            return resp(200, PAGE)

        with patch.object(stc.requests, "get", side_effect=fake_get), \
             patch.object(stc, "get_with_retry", side_effect=fake_retry), \
             patch.object(stc.time, "sleep"):
            stc.run()

        t = db.get_statement_texts(["https://good.example/a", "https://blocked.example/b",
                                    "https://broken.example/c", "https://good.example/speech"])
        self.assertEqual(t["https://good.example/a"]["status"], "ok")
        self.assertGreater(t["https://good.example/a"]["chars"], 400)
        self.assertEqual(t["https://blocked.example/b"]["status"], "robots_disallowed")
        self.assertIsNone(t["https://blocked.example/b"]["text"])
        self.assertEqual(t["https://broken.example/c"]["status"], "fetch_error")
        self.assertNotIn("https://good.example/speech", t)  # speeches are never fetched

        # second run: everything already attempted -> nothing fetched
        with patch.object(stc.requests, "get", side_effect=AssertionError("no network on 2nd run")), \
             patch.object(stc, "get_with_retry", side_effect=AssertionError("no network on 2nd run")):
            stc.run()


if __name__ == "__main__":
    unittest.main(verbosity=2)
