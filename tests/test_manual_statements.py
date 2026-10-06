import base64
import io
import json
import os
import sys
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db
import manual_statements as ms
import bank_analysis_service as svc
import statement_analysis as sa
from tests import fixtures as fx

FILLER = ("The Committee will continue to monitor developments and assess incoming information carefully. "
          "Members discussed the economic outlook, financial markets and the implications for policy. ") * 8


def pad(t):
    return t + " " + FILLER


def b64(raw):
    return base64.b64encode(raw).decode()


def make_pdf_lines(lines):
    """One page, with EXACTLY these printed lines (to reproduce hyphenation at line ends)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    for line in lines:
        c.drawString(50, y, line)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def make_pdf(pages, wrap=85, footer=True):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i, text in enumerate(pages, 1):
        y = 740
        for line in textwrap.wrap(text, wrap, break_long_words=False):
            c.drawString(50, y, line)
            y -= 14
        if footer:
            c.drawString(280, 30, f"Page {i} of {len(pages)}")
        c.showPage()
    c.save()
    return buf.getvalue()


class TestToken(unittest.TestCase):
    def test_states(self):
        good = "a-long-random-token-1234"
        self.assertEqual(ms.admin_token_state("", "x"), "disabled")
        self.assertEqual(ms.admin_token_state(None, "x"), "disabled")
        self.assertEqual(ms.admin_token_state("short", "short"), "disabled")          # too weak to trust
        self.assertEqual(ms.admin_token_state(good, None), "denied")
        self.assertEqual(ms.admin_token_state(good, ""), "denied")
        self.assertEqual(ms.admin_token_state(good, "wrong-token-wrong-12"), "denied")
        self.assertEqual(ms.admin_token_state(good, good + " "), "denied")             # no sloppy matching
        self.assertEqual(ms.admin_token_state(good, good), "ok")

    def test_unicode_token_does_not_crash(self):
        t = "pässwörd-with-ünicode-1234"
        self.assertEqual(ms.admin_token_state(t, t), "ok")
        self.assertEqual(ms.admin_token_state(t, "other-ünicode-token-9999"), "denied")


class TestCleaning(unittest.TestCase):
    def test_pasted_text_normalised(self):
        raw = "Line one  with\tspaces.\r\n\r\n\r\n\r\nSecond\u00a0paragraph.\r\n"
        out = ms.clean_pasted_text(raw)
        self.assertEqual(out, "Line one with spaces.\n\nSecond paragraph.")

    def test_nul_stripped(self):
        self.assertNotIn("\x00", ms.clean_pasted_text("a\x00b"))
        self.assertNotIn("\x00", ms.clean_pdf_text("a\x00b"))

    def test_pdf_cleanup_hyphen_pagenumbers_linebreaks(self):
        raw = "Inflation remains eleva-\nted and the Commit-\ntee is vigilant.\n3\n\nPage 2 of 5\nNext paragraph here.\n12 / 40"
        out = ms.clean_pdf_text(raw)
        self.assertIn("elevated", out)
        self.assertIn("Committee", out)
        self.assertNotIn("Page 2 of 5", out)
        self.assertNotIn("12 / 40", out)
        self.assertIn("Inflation remains elevated and the Committee is vigilant.", out)  # lines re-joined


class TestPdfExtraction(unittest.TestCase):
    def test_multipage_pdf_roundtrip(self):
        pages = [fx.PREV_HAWKISH_HOLD[:380], fx.PREV_HAWKISH_HOLD[380:] + " " + FILLER[:300]]
        text = ms.extract_pdf_text(b64(make_pdf(pages)))
        self.assertIn("Inflation remains elevated.", text)
        self.assertIn("decided to maintain the target range", text)
        self.assertNotIn("Page 1 of 2", text)
        self.assertNotIn("Page 2 of 2", text)
        # and the result is analysable end to end
        self.assertEqual(sa.detect_decision(text)["label"], "HOLD")
        self.assertIn("policy described as restrictive", sa.score_text(text)["phrases"])

    def test_data_url_prefix_accepted(self):
        raw = make_pdf([pad(fx.HAWKISH_HIKE)])
        self.assertIn("Inflation remains elevated", ms.extract_pdf_text("data:application/pdf;base64," + b64(raw)))

    def test_hyphenated_linebreak_in_real_pdf(self):
        lines = ["Inflation remains eleva-", "ted and the labour market remains tight. The Commit-",
                 "tee will continue to monitor developments and assess incoming information carefully."]
        t = ms.extract_pdf_text(b64(make_pdf_lines(lines)))
        self.assertIn("Inflation remains elevated and the labour market remains tight.", t)
        self.assertIn("The Committee will continue", t)
        self.assertNotIn("eleva-", t)
        self.assertEqual(sa.score_text(t)["phrases"].get("inflation remains elevated/high", {}).get("weight"), 2)

    def test_bad_inputs_have_clear_messages(self):
        with self.assertRaisesRegex(ValueError, "corrupted"):
            ms.extract_pdf_text("this is not base64 !!!")
        with self.assertRaisesRegex(ValueError, "doesn't look like a PDF"):
            ms.extract_pdf_text(b64(b"hello, definitely not a pdf"))
        with self.assertRaisesRegex(ValueError, "No PDF data"):
            ms.extract_pdf_text("")
        with self.assertRaisesRegex(ValueError, "larger than"):
            ms.extract_pdf_text(b64(b"%PDF-" + b"0" * (ms.MAX_PDF_BYTES + 10)))

    def test_image_only_pdf_explains_it_is_a_scan(self):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.rect(50, 50, 400, 400, fill=1)      # drawing only, no text
        c.showPage(); c.save()
        with self.assertRaisesRegex(ValueError, "no selectable text"):
            ms.extract_pdf_text(b64(buf.getvalue()))

    def test_truncated_pdf_is_handled_not_crashed(self):
        raw = make_pdf([pad(fx.HAWKISH_HIKE)])
        with self.assertRaises(ValueError):
            ms.extract_pdf_text(b64(raw[: len(raw) // 3]))

    def test_encrypted_pdf_is_reported(self):
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter()
        for p in PdfReader(io.BytesIO(make_pdf([pad(fx.HAWKISH_HIKE)]))).pages:
            w.add_page(p)
        try:
            w.encrypt("secret", algorithm="RC4-128")
        except Exception as e:  # encryption backend missing in this environment
            self.skipTest(f"cannot build an encrypted PDF here: {e}")
        buf = io.BytesIO(); w.write(buf)
        with self.assertRaisesRegex(ValueError, "password"):
            ms.extract_pdf_text(b64(buf.getvalue()))

    def test_missing_pypdf_package_degrades_gracefully(self):
        raw = make_pdf([pad(fx.HAWKISH_HIKE)])
        with patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaisesRegex(ValueError, "pypdf"):
                ms.extract_pdf_text(b64(raw))


class TestValidation(unittest.TestCase):
    def test_text_limits(self):
        with self.assertRaisesRegex(ValueError, "too short"):
            ms._validate_text("tiny")
        with self.assertRaisesRegex(ValueError, "too long"):
            ms._validate_text("word " * (ms.MAX_TEXT_CHARS // 4))
        with self.assertRaisesRegex(ValueError, "doesn't look like statement text"):
            ms._validate_text("1234 5678 " * 30)
        ms._validate_text(fx.HAWKISH_HIKE)   # fine

    def test_dates(self):
        self.assertEqual(ms._validate_date("2025-09-17"), "2025-09-17")
        for bad in (None, "", "17/09/2025", "2025-9-7", "2025-13-45", "yesterday"):
            with self.assertRaises(ValueError, msg=bad):
                ms._validate_date(bad)
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        with self.assertRaisesRegex(ValueError, "future"):
            ms._validate_date(future)
        with self.assertRaisesRegex(ValueError, "past"):
            ms._validate_date("1985-01-01")

    def test_urls(self):
        self.assertIsNone(ms._validate_url(""))
        self.assertIsNone(ms._validate_url(None))
        self.assertEqual(ms._validate_url(" https://x.org/a "), "https://x.org/a")
        for bad in ("javascript:alert(1)", "ftp://x.org/a", "x.org/a", "data:text/html,hi", "https://"):
            with self.assertRaises(ValueError, msg=bad):
                ms._validate_url(bad)

    def test_title_normalisation(self):
        self.assertIn("monetary policy", ms._normalise_title("", "Federal Reserve").lower())
        self.assertEqual(ms._normalise_title("FOMC statement", "Federal Reserve"), "FOMC statement")  # already recognisable? no -> prefixed
        self.assertTrue(ms._normalise_title("Sept meeting", "Federal Reserve").startswith("Monetary policy statement"))
        self.assertLessEqual(len(ms._normalise_title("x" * 999, "B")), 240)


class DbCase(unittest.TestCase):
    def setUp(self):
        if os.path.exists("data/mac_economics.db"):
            os.remove("data/mac_economics.db")
        db.init_db()
        self.sid = db.upsert_source("Feed", "https://feed", "central_bank", 1, "X")

    def auto(self, bank, date, link, title="Federal Reserve issues FOMC statement", summary="", text=None, status="ok"):
        db.insert_policy_statement(bank, title, date, link, summary, self.sid)
        if text is not None:
            db.upsert_statement_text(link, text, status)

    def rows(self, bank):
        return db.get_policy_statements_raw(bank)


class TestOperations(DbCase):
    def test_add_new_statement_end_to_end_on_a_bank_with_no_feed(self):
        # RBNZ currently has no feed at all — this is the use case.
        old = ms.save_manual_statement("RBNZ", "Reserve Bank of New Zealand", text=pad(fx.PREV_HAWKISH_HOLD),
                                       date="2025-07-09", title="OCR decision July")
        new = ms.save_manual_statement("RBNZ", "Reserve Bank of New Zealand", text=pad(fx.LATEST_DOVISH_CUT),
                                       date="2025-08-20")
        self.assertEqual((old["mode"], new["mode"]), ("added", "added"))
        self.assertTrue(new["link"].startswith("manual://RBNZ/"))
        r = svc.build_bank_analysis("RBNZ")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["analysis"]["stance"]["shift_label"], "more dovish")
        self.assertEqual(r["latest"]["text_source"], "manual")
        self.assertTrue(r["latest"]["entered_manually"])
        self.assertEqual(r["confidence"]["level"], "HIGH")
        self.assertIn("pasted or uploaded", " ".join(r["confidence"]["reasons"]))
        self.assertEqual(r["analysis"]["decision"]["latest"]["label"], "CUT")
        json.dumps(r)

    def test_response_includes_helpful_preview(self):
        r = ms.save_manual_statement("FED", "Federal Reserve", text=pad(fx.LATEST_DOVISH_CUT), date="2025-09-17")
        self.assertEqual(r["preview"]["tone"], "DOVISH")
        self.assertEqual(r["preview"]["decision"], "CUT")
        self.assertEqual(r["warnings"], [])

    def test_same_text_twice_is_not_duplicated(self):
        a = ms.save_manual_statement("FED", "F", text=pad(fx.HAWKISH_HIKE), date="2025-09-17")
        b = ms.save_manual_statement("FED", "F", text=pad(fx.HAWKISH_HIKE), date="2025-09-17")
        self.assertEqual(a["link"], b["link"])
        self.assertEqual(len(self.rows("FED")), 1)

    def test_attach_fixes_a_summary_only_statement(self):
        self.auto("FED", "Wed, 17 Sep 2025 14:00:00 EDT", "https://fed/sep", summary="Rates lowered.")
        self.auto("FED", "Wed, 30 Jul 2025 14:00:00 EDT", "https://fed/jul", summary="Rates held.")
        before = svc.build_bank_analysis("FED")
        self.assertEqual(before["confidence"]["level"], "LOW")
        ms.save_manual_statement("FED", "F", text=pad(fx.LATEST_DOVISH_CUT), attach_to_link="https://fed/sep")
        ms.save_manual_statement("FED", "F", text=pad(fx.PREV_HAWKISH_HOLD), attach_to_link="https://fed/jul")
        after = svc.build_bank_analysis("FED")
        self.assertEqual(after["confidence"]["level"], "HIGH")
        self.assertEqual(after["latest"]["text_source"], "manual")
        self.assertFalse(after["latest"]["entered_manually"])         # statement came from the feed; only its text is manual
        self.assertEqual(len(self.rows("FED")), 2)                    # nothing duplicated

    def test_attach_rejects_other_banks_and_unknown_links(self):
        self.auto("ECB", "Thu, 05 Jun 2025 13:45:00 +0200", "https://ecb/a", title="Monetary policy decisions")
        with self.assertRaisesRegex(ValueError, "isn't in this bank"):
            ms.save_manual_statement("FED", "F", text=pad(fx.HAWKISH_HIKE), attach_to_link="https://ecb/a")
        with self.assertRaisesRegex(ValueError, "isn't in this bank"):
            ms.save_manual_statement("FED", "F", text=pad(fx.HAWKISH_HIKE), attach_to_link="https://nope")
        self.assertEqual(db.get_statement_texts(["https://ecb/a"]), {})       # nothing written

    def test_new_with_existing_url_is_treated_as_attach(self):
        self.auto("FED", "Wed, 17 Sep 2025 14:00:00 EDT", "https://fed/sep", summary="x")
        r = ms.save_manual_statement("FED", "F", text=pad(fx.LATEST_DOVISH_CUT), date="2025-09-17", url="https://fed/sep")
        self.assertEqual(r["mode"], "attached")
        self.assertEqual(len(self.rows("FED")), 1)

    def test_manual_beats_fetched_for_the_same_meeting(self):
        self.auto("FED", "Wed, 17 Sep 2025 14:00:00 EDT", "https://fed/sep", summary="Rates lowered.")
        ms.save_manual_statement("FED", "F", text=pad(fx.LATEST_DOVISH_CUT), date="2025-09-17")
        r = svc.build_bank_analysis("FED")
        self.assertEqual(r["statement_count"], 1)                     # collapsed to one meeting
        self.assertTrue(r["latest"]["entered_manually"])

    def test_manual_statement_with_odd_title_and_url_still_listed(self):
        ms.save_manual_statement("FED", "F", text=pad(fx.LATEST_DOVISH_CUT), date="2025-09-17",
                                 title="My notes", url="https://example.org/speech/not-a-speech.pdf")
        r = svc.build_bank_analysis("FED")
        self.assertEqual(r["statement_count"], 1)                     # not filtered out as a "speech"
        self.assertTrue(r["latest"]["title"].startswith("Monetary policy statement"))

    def test_pdf_flow_end_to_end(self):
        pages = [fx.PREV_HAWKISH_HOLD, FILLER[:700]]
        r = ms.save_manual_statement("BOE", "Bank of England", pdf_base64=b64(make_pdf(pages)), date="2025-05-08")
        self.assertEqual(r["source"], "pdf")
        self.assertEqual(r["preview"]["decision"], "HOLD")
        self.assertEqual(svc.build_bank_analysis("BOE")["latest"]["text_source"], "manual")

    def test_warnings(self):
        short = ms.save_manual_statement("FED", "F", text="The Committee decided to lower the policy rate by 25 basis points today, citing slower job gains and easing price pressures across the economy.",
                                         date="2025-09-17")
        self.assertTrue(any("short" in w for w in short["warnings"]))
        notpolicy = ms.save_manual_statement("FED", "F", text="The weather this week was lovely and we visited several museums, parks and cafes around town. " * 6,
                                             date="2025-09-10")
        self.assertTrue(any("No hawkish or dovish wording" in w for w in notpolicy["warnings"]))

    def test_validation_errors_write_nothing(self):
        for kwargs in (dict(text="too short", date="2025-09-17"),
                       dict(text=pad(fx.HAWKISH_HIKE), date=None),
                       dict(text=pad(fx.HAWKISH_HIKE), date="2025-09-17", url="javascript:alert(1)")):
            with self.assertRaises(ValueError):
                ms.save_manual_statement("FED", "F", **kwargs)
        self.assertEqual(self.rows("FED"), [])


class TestRemove(DbCase):
    def test_remove_hand_added_statement(self):
        a = ms.save_manual_statement("FED", "F", text=pad(fx.HAWKISH_HIKE), date="2025-09-17")
        self.assertEqual(ms.remove_manual("FED", a["link"]), "deleted")
        self.assertEqual(self.rows("FED"), [])
        self.assertEqual(db.get_statement_texts([a["link"]]), {})

    def test_remove_pasted_text_reverts_a_fetched_statement(self):
        self.auto("FED", "Wed, 17 Sep 2025 14:00:00 EDT", "https://fed/sep", summary="s")
        ms.save_manual_statement("FED", "F", text=pad(fx.LATEST_DOVISH_CUT), attach_to_link="https://fed/sep")
        self.assertEqual(ms.remove_manual("FED", "https://fed/sep"), "reverted")
        self.assertEqual(len(self.rows("FED")), 1)                    # statement itself untouched
        self.assertEqual(svc.build_bank_analysis("FED")["latest"]["text_source"], "summary")

    def test_cannot_delete_fetched_statements_or_their_fetched_text(self):
        self.auto("FED", "Wed, 17 Sep 2025 14:00:00 EDT", "https://fed/sep", text=pad(fx.LATEST_DOVISH_CUT), status="ok")
        self.assertIsNone(ms.remove_manual("FED", "https://fed/sep"))
        self.assertEqual(len(self.rows("FED")), 1)
        self.assertEqual(db.get_statement_texts(["https://fed/sep"])["https://fed/sep"]["status"], "ok")

    def test_cannot_remove_across_banks_or_blank(self):
        a = ms.save_manual_statement("FED", "F", text=pad(fx.HAWKISH_HIKE), date="2025-09-17")
        self.assertIsNone(ms.remove_manual("ECB", a["link"]))
        self.assertIsNone(ms.remove_manual("FED", ""))
        self.assertIsNone(ms.remove_manual("FED", None))
        self.assertEqual(len(self.rows("FED")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
