import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db
import bank_analysis_service as svc
from tests import fixtures as fx

FILLER = ("The Committee will continue to monitor developments and assess incoming information carefully. "
          "Members discussed the economic outlook, financial markets and the implications for policy. ") * 8


def pad(t):
    return t + " " + FILLER  # >1200 chars, no lexicon hits in the filler


class ServiceTests(unittest.TestCase):
    def setUp(self):
        for f in ("data/mac_economics.db",):
            if os.path.exists(f):
                os.remove(f)
        db.init_db()
        self.sid = db.upsert_source("T", "https://t", "central_bank", 1, "X")

    def add(self, bank, title, date, link, summary="", text=None, status="ok"):
        db.insert_policy_statement(bank, title, date, link, summary, self.sid)
        if text is not None:
            db.upsert_statement_text(link, text, status)

    # -------------------------------------------------------------- states
    def test_unknown_bank(self):
        with self.assertRaises(KeyError):
            svc.build_bank_analysis("XXX")

    def test_no_data(self):
        r = svc.build_bank_analysis("ECB")
        self.assertEqual(r["status"], "no_data")
        self.assertEqual(r["statement_count"], 0)
        json.dumps(r)

    def test_non_statement_items_ignored(self):
        self.add("FED", "Chair speech on the economy", "Wed, 17 Sep 2025 10:00:00 EDT", "u1")
        self.add("FED", "Board approves application", "Wed, 17 Sep 2025 11:00:00 EDT", "u2")
        self.assertEqual(svc.build_bank_analysis("FED")["status"], "no_data")

    def test_single_statement_is_insufficient_but_still_scored(self):
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 17 Sep 2025 14:00:00 EDT", "a", text=pad(fx.HAWKISH_HIKE))
        r = svc.build_bank_analysis("FED")
        self.assertEqual(r["status"], "insufficient")
        self.assertEqual(r["analysis"]["stance"]["label_latest"], "HAWKISH")
        self.assertIsNone(r["previous"])
        self.assertEqual(r["confidence"]["level"], "MEDIUM")
        json.dumps(r)

    # ---------------------------------------------------------------- full
    def seed_three_fed(self):
        # Inserted deliberately OUT of chronological order, with dates whose
        # alphabetical order differs from chronological order.
        self.add("FED", "Federal Reserve issues FOMC statement", "Fri, 02 May 2025 14:00:00 EDT", "may",
                 text=pad(fx.HAWKISH_HIKE))
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 17 Sep 2025 14:00:00 EDT", "sep",
                 text=pad(fx.LATEST_DOVISH_CUT))
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 30 Jul 2025 14:00:00 EDT", "jul",
                 text=pad(fx.PREV_HAWKISH_HOLD))

    def test_latest_and_previous_chosen_by_real_date(self):
        self.seed_three_fed()
        r = svc.build_bank_analysis("FED")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["latest"]["link"], "sep")
        self.assertEqual(r["previous"]["link"], "jul")
        self.assertEqual([h["link"] for h in r["history"]], ["jul", "may"])
        self.assertEqual(r["latest"]["date"], "17 Sep 2025")

    def test_analysis_content(self):
        self.seed_three_fed()
        r = svc.build_bank_analysis("FED")
        st = r["analysis"]["stance"]
        self.assertEqual(st["shift_label"], "more dovish")
        self.assertEqual(r["analysis"]["decision"]["latest"]["label"], "CUT")
        self.assertEqual(r["analysis"]["decision"]["previous"]["label"], "HOLD")
        text = " ".join(r["narrative"])
        self.assertIn("more dovish", text)
        self.assertIn("CUT", text)
        self.assertIn("HOLD", text)
        self.assertEqual(r["expectation"]["bias"], "DOVISH")
        self.assertTrue(r["expectation"]["reasons"])
        self.assertEqual(r["implications"]["direction"], "dovish")
        self.assertEqual(r["confidence"]["level"], "HIGH")
        json.dumps(r)  # must be JSON-serialisable for the API

    def test_compare_with_older_statement_and_clamping(self):
        self.seed_three_fed()
        r2 = svc.build_bank_analysis("FED", compare=2)
        self.assertEqual(r2["previous"]["link"], "may")
        self.assertEqual(r2["compare"], 2)
        # the May statement is hawkish -> the Sep cut is a big dovish move
        self.assertEqual(r2["analysis"]["stance"]["shift_label"], "more dovish")
        self.assertEqual(svc.build_bank_analysis("FED", compare=99)["compare"], 2)   # clamped to oldest
        self.assertEqual(svc.build_bank_analysis("FED", compare=0)["compare"], 1)    # clamped to min 1

    def test_summary_fallback_lowers_confidence_and_says_so(self):
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 17 Sep 2025 14:00:00 EDT", "sep",
                 summary="<p>The Committee decided to lower the target range for the policy rate. Inflation has eased.</p>",
                 text=None)
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 30 Jul 2025 14:00:00 EDT", "jul",
                 summary="The Committee decided to maintain the policy rate. Inflation remains elevated.",
                 text=None, status="robots_disallowed")
        r = svc.build_bank_analysis("FED")
        self.assertEqual(r["latest"]["text_source"], "summary")
        self.assertEqual(r["confidence"]["level"], "LOW")
        self.assertIn("summary only", " ".join(r["confidence"]["reasons"]))
        self.assertNotIn("<p>", json.dumps(r["analysis"]["diff"]))   # HTML stripped from summaries
        self.assertEqual(r["analysis"]["decision"]["latest"]["label"], "CUT")

    def test_mixed_full_and_summary_is_medium(self):
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 17 Sep 2025 14:00:00 EDT", "sep", text=pad(fx.LATEST_DOVISH_CUT))
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 30 Jul 2025 14:00:00 EDT", "jul",
                 summary="The Committee decided to maintain the policy rate.")
        self.assertEqual(svc.build_bank_analysis("FED")["confidence"]["level"], "MEDIUM")

    def test_too_short_full_text_not_trusted(self):
        self.add("FED", "Federal Reserve issues FOMC statement", "Wed, 17 Sep 2025 14:00:00 EDT", "sep",
                 summary="s", text="tiny", status="too_short")
        r = svc.build_bank_analysis("FED")
        self.assertEqual(r["latest"]["text_source"], "summary")

    # ------------------------------------------------------ bank specifics
    def test_fed_catalysts_from_release_calendar(self):
        self.seed_three_fed()
        for rid, name, date in [(10, "Consumer Price Index", "2025-10-15"), (50, "Employment Situation", "2025-10-03"),
                                (99, "Some Obscure Release", "2025-10-04"), (53, "Gross Domestic Product", "2025-09-01")]:
            db.insert_release_date(rid, name, date, self.sid)
        r = svc.build_bank_analysis("FED", today="2025-09-20")
        names = [c["release"] for c in r["expectation"]["catalysts"]]
        self.assertEqual(names, ["Employment Situation", "Consumer Price Index"])  # sorted, relevant only, future only

    def test_non_fed_has_no_catalysts_but_has_watch_items(self):
        self.add("ECB", "Monetary policy decisions", "Thu, 05 Jun 2025 13:45:00 +0200", "e1", text=pad(fx.HAWKISH_HIKE))
        self.add("ECB", "Monetary policy decisions", "Thu, 17 Apr 2025 13:45:00 +0200", "e0", text=pad(fx.NEUTRAL_TEXT))
        r = svc.build_bank_analysis("ECB")
        self.assertEqual(r["expectation"]["catalysts"], [])
        self.assertTrue(any("HICP" in w for w in r["expectation"]["watch_items"]))
        rows = {x["asset"]: x for x in r["implications"]["rows"]}
        self.assertEqual(rows["EUR/USD"]["hawkish"], "Tends to rise")     # EUR is the base currency

    def test_inverse_pair_logic_for_jpy(self):
        rows = {x["asset"]: x for x in svc.build_implications(svc.BANK_META["BOJ"],
                {"stance": {"shift": 20}})["rows"]}
        self.assertEqual(rows["USD/JPY"]["hawkish"], "Tends to fall")      # stronger JPY = lower USD/JPY
        self.assertEqual(rows["USD/JPY"]["dovish"], "Tends to rise")

    def test_every_bank_builds_without_error(self):
        for code in svc.BANK_META:
            self.assertEqual(svc.build_bank_analysis(code)["status"], "no_data")
        for code, m in svc.BANK_META.items():
            self.assertTrue(m["watch"] and m["rates"] and m["equity"] and m["ccy"])

    # ------------------------------------------------------------- safety
    def test_no_trade_instruction_language_and_disclaimer_present(self):
        self.seed_three_fed()
        r = svc.build_bank_analysis("FED")
        blob = json.dumps({k: r[k] for k in ("narrative", "implications", "expectation")}).lower()
        for banned in ("you should buy", "you should sell", "guaranteed", "will definitely", "go long", "go short",
                       "buy now", "sell now", "can't lose", "risk-free"):
            self.assertNotIn(banned, blob)
        self.assertIn("not financial advice", r["implications"]["disclaimer"].lower())
        self.assertIn("priced in", blob)       # the pricing caveat must be there

    def test_neutral_with_shift_becomes_leaning(self):
        analysis = {"stance": {"label_latest": "NEUTRAL", "shift": 12.0}, "evidence": []}
        self.assertEqual(svc.build_expectation(svc.BANK_META["FED"], analysis, [])["bias"], "LEANING HAWKISH")
        analysis["stance"]["shift"] = -12.0
        self.assertEqual(svc.build_expectation(svc.BANK_META["FED"], analysis, [])["bias"], "LEANING DOVISH")


if __name__ == "__main__":
    unittest.main(verbosity=2)
