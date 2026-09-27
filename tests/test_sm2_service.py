import unittest
from datetime import date, timedelta

from app.services.sm2_service import compute_sm2, slice_review_queue


class TestSM2Service(unittest.TestCase):
    def setUp(self):
        self.fixed_today = date(2026, 9, 27)

    def test_first_successful_review(self):
        res = compute_sm2(
            quality=4,
            interval=1,
            repetitions=0,
            efactor=2.5,
            today=self.fixed_today,
        )
        self.assertEqual(res.repetitions, 1)
        self.assertEqual(res.interval, 1)
        self.assertEqual(res.next_review_date, self.fixed_today + timedelta(days=1))
        self.assertEqual(res.efactor, 2.5)

    def test_second_successful_review(self):
        res = compute_sm2(
            quality=4,
            interval=1,
            repetitions=1,
            efactor=2.5,
            today=self.fixed_today,
        )
        self.assertEqual(res.repetitions, 2)
        self.assertEqual(res.interval, 6)
        self.assertEqual(res.next_review_date, self.fixed_today + timedelta(days=6))

    def test_third_successful_review(self):
        res = compute_sm2(
            quality=5,
            interval=6,
            repetitions=2,
            efactor=2.5,
            today=self.fixed_today,
        )
        self.assertEqual(res.repetitions, 3)
        # efactor increases when quality is 5: 2.5 + 0.1 = 2.6
        self.assertEqual(res.efactor, 2.6)
        self.assertEqual(res.interval, round(6 * 2.6))
        self.assertEqual(res.next_review_date, self.fixed_today + timedelta(days=round(6 * 2.6)))

    def test_failed_review_resets_streak(self):
        res = compute_sm2(
            quality=1,
            interval=15,
            repetitions=4,
            efactor=2.5,
            today=self.fixed_today,
        )
        self.assertEqual(res.repetitions, 0)
        self.assertEqual(res.interval, 1)
        self.assertEqual(res.next_review_date, self.fixed_today + timedelta(days=1))
        # efactor decreases on failure
        self.assertLess(res.efactor, 2.5)

    def test_min_efactor_floor(self):
        # Repeated severe failures should not drop efactor below 1.3
        res = compute_sm2(
            quality=0,
            interval=1,
            repetitions=0,
            efactor=1.3,
            today=self.fixed_today,
        )
        self.assertEqual(res.efactor, 1.3)

    def test_invalid_quality_raises_error(self):
        with self.assertRaises(ValueError):
            compute_sm2(quality=-1, interval=1, repetitions=0, efactor=2.5)
        with self.assertRaises(ValueError):
            compute_sm2(quality=6, interval=1, repetitions=0, efactor=2.5)

    def test_slice_review_queue_cap(self):
        items = list(range(50))
        sliced = slice_review_queue(items, cap=15)
        self.assertEqual(len(sliced), 15)
        self.assertEqual(sliced, list(range(15)))

        small_items = [1, 2, 3]
        self.assertEqual(slice_review_queue(small_items, cap=15), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
