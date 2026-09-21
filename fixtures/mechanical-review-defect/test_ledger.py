import unittest

from inventory.ledger import InsufficientStock, Ledger


class LedgerTests(unittest.TestCase):
    def test_oversized_withdrawal_raises(self):
        ledger = Ledger()
        ledger.apply(3, "delivery")
        with self.assertRaises(InsufficientStock):
            ledger.apply(-5, "sale")


if __name__ == "__main__":
    unittest.main()
