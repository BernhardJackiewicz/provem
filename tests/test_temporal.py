"""Tests for deterministic relative-date annotation (cognitive_memory.temporal)."""

import unittest
from datetime import date, datetime

from cognitive_memory.temporal import annotate_relative_dates


class AnnotateRelativeDatesTests(unittest.TestCase):
    ANCHOR = date(2023, 5, 8)  # a Monday

    def test_yesterday(self):
        out = annotate_relative_dates("I went to a support group yesterday.", self.ANCHOR)
        self.assertIn("yesterday [=7 May 2023]", out)

    def test_day_before_yesterday_wins_over_yesterday(self):
        out = annotate_relative_dates("we met the day before yesterday!", self.ANCHOR)
        self.assertIn("the day before yesterday [=6 May 2023]", out)
        self.assertNotIn("yesterday [=7 May 2023]", out)

    def test_last_weekday(self):
        out = annotate_relative_dates("The race was last Saturday.", self.ANCHOR)
        self.assertIn("last Saturday [=6 May 2023]", out)
        # same weekday as anchor resolves a full week back, never day 0
        out = annotate_relative_dates("saw them last Monday.", self.ANCHOR)
        self.assertIn("last Monday [=1 May 2023]", out)

    def test_last_week_month_year(self):
        out = annotate_relative_dates("started last week, quit last month, moved last year.", self.ANCHOR)
        self.assertIn("last week [=week of 1 May 2023]", out)
        self.assertIn("last month [=April 2023]", out)
        self.assertIn("last year [=2022]", out)

    def test_days_ago_numeric_and_words(self):
        out = annotate_relative_dates("adopted him 3 days ago; called a few days ago.", self.ANCHOR)
        self.assertIn("3 days ago [=5 May 2023]", out)
        self.assertIn("a few days ago [=5 May 2023]", out)

    def test_verbatim_preserved_and_no_double_annotation(self):
        text = "I went yesterday."
        once = annotate_relative_dates(text, self.ANCHOR)
        twice = annotate_relative_dates(once, self.ANCHOR)
        self.assertEqual(once, twice)  # idempotent: annotated spans not re-matched
        self.assertTrue(once.startswith("I went yesterday"))

    def test_datetime_anchor_and_none(self):
        out = annotate_relative_dates("saw it yesterday", datetime(2023, 5, 8, 14, 30))
        self.assertIn("[=7 May 2023]", out)
        self.assertEqual(annotate_relative_dates("yesterday", None), "yesterday")

    def test_plain_words_untouched(self):
        text = "Yesterdays news is history; the last years were hard."
        self.assertEqual(annotate_relative_dates(text, self.ANCHOR), text)


if __name__ == "__main__":
    unittest.main()
