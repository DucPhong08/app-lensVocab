import unittest

from app.services.degradation_service import (
    _normalize_threshold_percent,
)


class TestDegradationService(unittest.TestCase):
    def test_normalize_threshold_fraction_to_percent(self):
        # 0.5 -> 50%
        self.assertEqual(_normalize_threshold_percent(0.5), 50.0)
        self.assertEqual(_normalize_threshold_percent(0.85), 85.0)

    def test_normalize_threshold_already_percent(self):
        # 60.0 -> 60.0%
        self.assertEqual(_normalize_threshold_percent(60.0), 60.0)
        self.assertEqual(_normalize_threshold_percent(100.0), 100.0)

    def test_normalize_threshold_boundary_clamps(self):
        self.assertEqual(_normalize_threshold_percent(-5.0), 0.0)
        self.assertEqual(_normalize_threshold_percent(150.0), 100.0)


if __name__ == "__main__":
    unittest.main()
