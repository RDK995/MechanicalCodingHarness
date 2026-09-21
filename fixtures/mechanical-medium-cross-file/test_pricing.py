import unittest

from src.api import quote


class PricingTests(unittest.TestCase):
    def test_caller_rates_reach_tax_calculation(self):
        self.assertAlmostEqual(quote(100, 0.10), 110)
        self.assertAlmostEqual(quote(100, 0.20), 120)


if __name__ == "__main__":
    unittest.main()
