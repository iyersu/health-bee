"""Offline checks for bounded, source-linked local weekly reviews."""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from journal import review, store


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "review.db"
        store.init_db(self.db)

    def add(self, day, **fields):
        return store.add_entry(self.db, "Synthetic entry", occurred_at=datetime.fromisoformat(day + "T12:00:00-05:00"), **fields)

    def test_sql_counts_missing_dates_and_optional_summary_link_to_entries(self):
        first = self.add("2026-10-01", mood="calm", sleep_hours=8, energy=7,
                         observations=[{"symptom": "headache"}])
        second = self.add("2026-10-03", mood="tender", sleep_hours=6, energy=3,
                          observations=[{"symptom": "headache"}, {"symptom": "cramps"}])
        result = review.build_review(self.db, since="2026-10-01", until="2026-10-08", include_summary=True)
        self.assertEqual((result["entries"], result["logged_days"]), (2, 2))
        self.assertIn("2026-10-02", result["missing_dates"])
        self.assertEqual((result["average_sleep_hours"], result["average_energy"]), (7.0, 5.0))
        self.assertEqual(result["symptoms"][0], {"value": "headache", "count": 2})
        self.assertTrue(result["summary"])
        linked = {entry["id"] for entry in result["source_entries"]}
        self.assertEqual(linked, {first, second})
        self.assertTrue(all(set(statement["source_ids"]) <= linked for statement in result["summary"]))

    def test_summary_is_optional_and_avoids_medical_or_fertility_claims(self):
        self.add("2026-10-01", observations=[{"symptom": "fatigue"}])
        without_summary = review.build_review(self.db, since="2026-10-01", until="2026-10-08", include_summary=False)
        with_summary = review.build_review(self.db, since="2026-10-01", until="2026-10-08", include_summary=True)
        self.assertEqual(without_summary["summary"], [])
        text = " ".join(item["text"].lower() for item in with_summary["summary"])
        for prohibited in ("diagnos", "cause", "treatment", "fertil", "ovulat", "recommend"):
            self.assertNotIn(prohibited, text)

    def test_source_links_are_bounded_and_ranges_are_limited(self):
        for _ in range(review.MAX_SOURCE_LINKS + 2):
            self.add("2026-10-01")
        result = review.build_review(self.db, since="2026-10-01", until="2026-10-08", include_summary=True)
        self.assertEqual(len(result["source_entries"]), review.MAX_SOURCE_LINKS)
        self.assertTrue(result["source_links_truncated"])
        with self.assertRaises(ValueError):
            review.build_review(self.db, since="2026-10-01", until="2026-11-02", include_summary=False)


if __name__ == "__main__":
    unittest.main()
