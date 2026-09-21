import unittest

from counter import increment


class CounterTests(unittest.TestCase):
    def test_supported_integer_domain(self):
        self.assertEqual(increment(4), 5)
        self.assertEqual(increment(0), 1)
        self.assertEqual(increment(-3), -2)


if __name__ == "__main__":
    unittest.main()
