import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import statement_analysis as sa
from tests import fixtures as fx


class TestSelection(unittest.TestCase):
    def test_policy_statement_titles(self):
        self.assertTrue(sa.is_policy_statement("FED", "Federal Reserve issues FOMC statement"))
        self.assertTrue(sa.is_policy_statement("ECB", "Monetary policy decisions"))
        self.assertTrue(sa.is_policy_statement("BOC", "Bank of Canada maintains policy rate at 2.75%"))
        self.assertTrue(sa.is_policy_statement("SNB", "Monetary policy assessment of 19 June 2025"))

    def test_non_statements_excluded(self):
        self.assertFalse(sa.is_policy_statement("FED", "Minutes of the Federal Open Market Committee"))
        self.assertFalse(sa.is_policy_statement("FED", "Chair Powell speech on monetary policy"))
        self.assertFalse(sa.is_policy_statement("FED", "Federal Reserve Board announces approval of application"))
        self.assertFalse(sa.is_policy_statement("RBA", "Statement on Monetary Policy - August 2025"))
        self.assertFalse(sa.is_policy_statement("BOE", "Monetary Policy Report - May 2025"))
        self.assertFalse(sa.is_policy_statement("FED", ""))
        self.assertFalse(sa.is_policy_statement("FED", None))

    def test_speech_with_policy_title_is_excluded_by_its_link(self):
        title = "Economic Outlook and Monetary Policy"
        self.assertFalse(sa.is_policy_statement("FED", title, "https://www.federalreserve.gov/newsevents/speech/x20250917a.htm"))
        self.assertFalse(sa.is_policy_statement("FED", title, "https://www.federalreserve.gov/newsevents/testimony/x.htm"))
        self.assertFalse(sa.is_policy_statement("ECB", title, "https://www.ecb.europa.eu/press/key/date/2025/html/ecb.sp250911~abc.en.html"))
        self.assertTrue(sa.is_policy_statement("FED", title, "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm"))
        self.assertTrue(sa.is_policy_statement("ECB", "Monetary policy decisions", "https://www.ecb.europa.eu/press/pr/date/2025/html/ecb.mp250911~abc.en.html"))

    def test_select_statements_uses_links(self):
        rows = [
            {"title": "Economic Outlook and Monetary Policy", "published_at": "2025-09-18", "link": "https://fed/newsevents/speech/a.htm"},
            {"title": "Federal Reserve issues FOMC statement", "published_at": "2025-09-17", "link": "https://fed/newsevents/pressreleases/m.htm"},
        ]
        out = sa.select_statements("FED", rows)
        self.assertEqual([r["link"] for r in out], ["https://fed/newsevents/pressreleases/m.htm"])

    def test_boj_statement_on_monetary_policy_is_kept(self):
        # BOJ's decision statement is literally titled this; only RBA's is the quarterly report.
        self.assertTrue(sa.is_policy_statement("BOJ", "Statement on Monetary Policy"))

    def test_date_parsing_formats(self):
        d1 = sa.parse_pub_date("Wed, 17 Sep 2025 14:00:00 EDT")
        d2 = sa.parse_pub_date("2025-09-17T18:00:00Z")
        d3 = sa.parse_pub_date("2025-09-17")
        self.assertEqual((d1.year, d1.month, d1.day), (2025, 9, 17))
        self.assertEqual((d2.year, d2.month, d2.day), (2025, 9, 17))
        self.assertEqual((d3.year, d3.month, d3.day), (2025, 9, 17))
        self.assertIsNone(sa.parse_pub_date("not a date"))
        self.assertIsNone(sa.parse_pub_date(None))

    def test_sorts_newest_first_by_real_date_not_alphabetically(self):
        # Alphabetically "Wed..." > "Fri..." > "Mon...", which is NOT chronological.
        rows = [
            {"title": "FOMC statement A", "published_at": "Wed, 18 Dec 2024 14:00:00 EST", "link": "a"},
            {"title": "FOMC statement B", "published_at": "Mon, 17 Mar 2025 14:00:00 EDT", "link": "b"},
            {"title": "FOMC statement C", "published_at": "Fri, 02 May 2025 14:00:00 EDT", "link": "c"},
        ]
        out = sa.select_statements("FED", rows)
        self.assertEqual([r["link"] for r in out], ["c", "b", "a"])

    def test_dedup_and_same_meeting_collapse(self):
        rows = [
            {"title": "Monetary policy decisions", "published_at": "Thu, 05 Jun 2025 13:45:00 +0200", "link": "d1"},
            {"title": "Monetary policy statement", "published_at": "Thu, 05 Jun 2025 14:45:00 +0200", "link": "s1"},
            {"title": "Monetary policy decisions", "published_at": "Thu, 17 Apr 2025 13:45:00 +0200", "link": "d0"},
            {"title": "Monetary policy decisions", "published_at": "Thu, 17 Apr 2025 13:45:00 +0200", "link": "d0"},  # duplicate
        ]
        out = sa.select_statements("ECB", rows)
        self.assertEqual([r["link"] for r in out], ["d1", "d0"])  # same-day pair collapsed, dup removed


class TestSentences(unittest.TestCase):
    def test_abbreviations_do_not_split(self):
        s = sa.split_sentences("The U.S. economy grew. Inflation fell, e.g. in services. It rose at 2 p.m. today.")
        self.assertEqual(len(s), 3, s)
        self.assertTrue(s[0].startswith("The U.S. economy"))

    def test_empty(self):
        self.assertEqual(sa.split_sentences(""), [])
        self.assertEqual(sa.split_sentences(None), [])


class TestDiff(unittest.TestCase):
    def setUp(self):
        self.d = sa.diff_statements(fx.PREV_HAWKISH_HOLD, fx.LATEST_DOVISH_CUT)

    def test_identical_sentences_marked_same(self):
        same = [s["t"] for s in self.d["segments"] if s["k"] == "same"]
        self.assertTrue(any("expanding at a solid pace" in t for t in same))
        self.assertTrue(any("roughly in balance" in t for t in same))

    def test_new_and_changed_detected(self):
        kinds = {s["k"] for s in self.d["segments"]}
        self.assertIn("new", kinds)
        texts_new = " ".join(s["t"] for s in self.d["segments"] if s["k"] in ("new", "changed"))
        self.assertIn("greater confidence", texts_new)
        self.assertIn("lower the target range", texts_new)

    def test_removed_sentences_reported(self):
        removed = " ".join(self.d["removed"])
        self.assertIn("sufficiently restrictive", removed)

    def test_changed_sentence_word_marks(self):
        changed = [s for s in self.d["segments"] if s["k"] == "changed"]
        self.assertTrue(changed, "expected at least one paired/changed sentence")
        for seg in changed:
            kinds = {w["k"] for w in seg["words"]}
            self.assertIn("new", kinds)
            self.assertIn("same", kinds)
            # words rebuild the sentence exactly
            self.assertEqual(" ".join(w["t"] for w in seg["words"]), seg["t"])

    def test_decision_sentence_word_level(self):
        seg = next(s for s in self.d["segments"] if "decided to" in s["t"])
        new_words = [w["t"] for w in seg["words"] if w["k"] == "new"]
        self.assertIn("lower", new_words)
        old_hold_word_present = any(w["t"] == "maintain" for w in seg["words"])
        self.assertFalse(old_hold_word_present)

    def test_identical_texts_have_no_changes(self):
        d = sa.diff_statements(fx.NEUTRAL_TEXT, fx.NEUTRAL_TEXT)
        self.assertEqual(d["stats"]["new"] + d["stats"]["changed"] + d["stats"]["removed"], 0)

    def test_stats_consistent(self):
        n = len(self.d["segments"])
        st = self.d["stats"]
        self.assertEqual(st["same"] + st["changed"] + st["new"], n)


class TestScoring(unittest.TestCase):
    def test_hawkish_text_scores_hawkish(self):
        r = sa.score_text(fx.HAWKISH_HIKE)
        self.assertGreaterEqual(r["score"], 60)
        self.assertEqual(r["label"], "HAWKISH")

    def test_dovish_text_scores_dovish(self):
        r = sa.score_text(fx.LATEST_DOVISH_CUT)
        self.assertLessEqual(r["score"], 40)
        self.assertEqual(r["label"], "DOVISH")

    def test_neutral_text_near_50(self):
        r = sa.score_text(fx.NEUTRAL_TEXT)
        self.assertEqual(r["label"], "NEUTRAL")
        self.assertAlmostEqual(r["score"], 50, delta=5)

    def test_empty_text_is_neutral_50(self):
        self.assertEqual(sa.score_text("")["score"], 50.0)
        self.assertEqual(sa.score_text(None)["score"], 50.0)

    def test_score_bounded(self):
        monster = " ".join([fx.HAWKISH_HIKE] * 50)
        r = sa.score_text(monster)
        self.assertLessEqual(r["score"], 100)
        self.assertGreaterEqual(r["score"], 0)

    def test_repetition_does_not_inflate(self):
        once = sa.score_text("Inflation remains elevated.")["score"]
        many = sa.score_text("Inflation remains elevated. " * 20)["score"]
        self.assertEqual(once, many)

    def test_less_restrictive_not_counted_hawkish(self):
        r = sa.score_text("Policy will become less restrictive over time.")
        self.assertNotIn("policy described as restrictive", r["phrases"])
        self.assertLess(r["score"], 50)

    def test_downside_risks_to_inflation_not_double_counted_as_growth(self):
        r = sa.score_text("There are downside risks to inflation.")
        labels = set(r["phrases"])
        self.assertIn("downside risks / inflation below target", labels)
        self.assertNotIn("downside risks to growth/outlook", labels)

    def test_monotonic_more_dovish_words_lower_score(self):
        a = sa.score_text("Inflation has eased.")["score"]
        b = sa.score_text("Inflation has eased. The labour market has softened.")["score"]
        self.assertLess(b, a)


class TestDecision(unittest.TestCase):
    def test_cut(self):
        self.assertEqual(sa.detect_decision(fx.LATEST_DOVISH_CUT)["label"], "CUT")

    def test_hold(self):
        d = sa.detect_decision(fx.PREV_HAWKISH_HOLD)
        self.assertEqual(d["label"], "HOLD")
        self.assertIn("maintain the target range", d["evidence"])

    def test_hike(self):
        self.assertEqual(sa.detect_decision(fx.HAWKISH_HIKE)["label"], "HIKE")

    def test_unchanged_wording_is_hold(self):
        self.assertEqual(sa.detect_decision(fx.NEUTRAL_TEXT)["label"], "HOLD")

    def test_balance_sheet_reduction_is_not_a_rate_cut(self):
        txt = "The Committee decided to reduce its holdings of securities at a slower pace. The policy rate is unchanged."
        self.assertNotEqual(sa.detect_decision(txt)["label"], "CUT")

    def test_no_decision(self):
        d = sa.detect_decision("The economy is growing.")
        self.assertEqual(d["label"], "UNCLEAR")
        self.assertIsNone(d["evidence"])


class TestAnalyzePair(unittest.TestCase):
    def setUp(self):
        self.a = sa.analyze_pair(fx.PREV_HAWKISH_HOLD, fx.LATEST_DOVISH_CUT)

    def test_shift_direction(self):
        s = self.a["stance"]
        self.assertEqual(s["shift_label"], "more dovish")
        self.assertLess(s["shift"], -sa.SHIFT_THRESHOLD)
        self.assertEqual(s["label_latest"], "DOVISH")
        self.assertEqual(s["label_previous"], "HAWKISH")
        self.assertAlmostEqual(s["shift"], s["latest"] - s["previous"], places=1)

    def test_decision_change_detected(self):
        self.assertEqual(self.a["decision"]["previous"]["label"], "HOLD")
        self.assertEqual(self.a["decision"]["latest"]["label"], "CUT")

    def test_evidence_statuses(self):
        by = {e["phrase"]: e for e in self.a["evidence"]}
        self.assertEqual(by["DECISION: cut rates"]["status"], "new")
        self.assertEqual(by["policy described as restrictive"]["status"], "removed")
        self.assertEqual(by["inflation easing/moderating"]["status"], "new")
        self.assertEqual(by["solid/resilient growth"]["status"], "unchanged")

    def test_driver_deltas_sum_matches_score_raw(self):
        total = sum(d["latest"] for d in self.a["drivers"])
        raw = sa.score_text(fx.LATEST_DOVISH_CUT)["raw"]
        self.assertEqual(total, raw)

    def test_drivers_sorted_by_change(self):
        deltas = [abs(d["delta"]) for d in self.a["drivers"]]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_reverse_pair_is_more_hawkish(self):
        r = sa.analyze_pair(fx.LATEST_DOVISH_CUT, fx.PREV_HAWKISH_HOLD)
        self.assertEqual(r["stance"]["shift_label"], "more hawkish")
        self.assertAlmostEqual(r["stance"]["shift"], -self.a["stance"]["shift"], places=1)

    def test_same_text_little_changed(self):
        r = sa.analyze_pair(fx.NEUTRAL_TEXT, fx.NEUTRAL_TEXT)
        self.assertEqual(r["stance"]["shift_label"], "little changed")
        self.assertEqual(r["stance"]["shift"], 0)

    def test_single(self):
        s = sa.analyze_single(fx.HAWKISH_HIKE)
        self.assertEqual(s["stance"]["label_latest"], "HAWKISH")
        self.assertIsNone(s["diff"])
        self.assertEqual(s["decision"]["latest"]["label"], "HIKE")


class TestRegressions(unittest.TestCase):
    """Bugs found by spot-checking real-world phrasing; each must stay fixed."""

    def test_hypothetical_cut_is_not_a_decision(self):
        t = "It would be premature to cut rates until inflation is sustainably at target."
        self.assertEqual(sa.detect_decision(t)["label"], "UNCLEAR")
        self.assertNotIn("DECISION: cut rates (past tense)", sa.score_text(t)["phrases"])
        self.assertIn("too early to ease", sa.score_text(t)["phrases"])

    def test_passive_and_present_tense_decisions(self):
        self.assertEqual(sa.detect_decision("Policy rate was kept at 0.5%.")["label"], "HOLD")
        self.assertEqual(sa.detect_decision("The Bank of Canada maintains its policy rate at 2.75%.")["label"], "HOLD")
        self.assertEqual(sa.detect_decision("The Bank raised its policy rate by 50 basis points to 5.00%.")["label"], "HIKE")
        self.assertEqual(sa.detect_decision("The Bank lowered its policy rate to 4.75%.")["label"], "CUT")

    def test_unemployment_rate_is_not_the_policy_rate(self):
        t = "The unemployment rate has risen, and the Committee will meet again."
        self.assertEqual(sa.detect_decision(t)["label"], "UNCLEAR")

    def test_but_remains_elevated_keeps_hawkish_lean(self):
        p = sa.score_text("Inflation has declined but remains somewhat elevated.")["phrases"]
        self.assertIn("inflation easing/moderating", p)
        self.assertIn("inflation still elevated/above target despite easing", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
