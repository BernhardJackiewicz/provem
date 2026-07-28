import unittest

from cognitive_memory.stats import (
    bootstrap_diff_ci,
    independence_baseline,
    mcnemar_exact,
    mcnemar_from_pairs,
    stdev,
    wilson_interval,
    wilson_point_and_interval,
)


class WilsonIntervalTests(unittest.TestCase):
    def test_empty_sample_is_maximally_uncertain(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_interval_brackets_point_estimate(self):
        low, high = wilson_interval(8, 10)
        self.assertLess(low, 0.8)
        self.assertGreater(high, 0.8)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)

    def test_all_success_upper_bound_is_one_but_lower_below_one(self):
        low, high = wilson_interval(20, 20)
        self.assertEqual(high, 1.0)
        self.assertLess(low, 1.0)
        self.assertGreater(low, 0.0)

    def test_all_failure_lower_bound_is_zero(self):
        low, high = wilson_interval(0, 20)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_known_value_matches_reference(self):
        # 50/100 at 95% -> approx (0.404, 0.596) per standard references.
        low, high = wilson_interval(50, 100)
        self.assertAlmostEqual(low, 0.4038, places=3)
        self.assertAlmostEqual(high, 0.5962, places=3)

    def test_larger_sample_narrows_interval(self):
        _, small_low, small_high = wilson_point_and_interval(8, 10)
        _, big_low, big_high = wilson_point_and_interval(800, 1000)
        self.assertLess(big_high - big_low, small_high - small_low)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            wilson_interval(11, 10)


class McNemarTests(unittest.TestCase):
    def test_no_discordant_pairs_is_not_significant(self):
        n, p = mcnemar_exact(0, 0)
        self.assertEqual(n, 0)
        self.assertEqual(p, 1.0)

    def test_symmetric_discordance_is_not_significant(self):
        n, p = mcnemar_exact(10, 10)
        self.assertEqual(n, 20)
        self.assertGreater(p, 0.9)

    def test_strong_asymmetry_is_significant(self):
        # A wins 20, B wins 0 -> exact two-sided p = 2 * 0.5^20, tiny.
        n, p = mcnemar_exact(20, 0)
        self.assertEqual(n, 20)
        self.assertLess(p, 0.01)

    def test_known_small_value(self):
        # b=8, c=1 -> two-sided exact p = 2 * (P(0)+P(1)) with n=9, p=.5
        # = 2 * (1 + 9)/512 = 20/512 = 0.0390625
        n, p = mcnemar_exact(8, 1)
        self.assertEqual(n, 9)
        self.assertAlmostEqual(p, 0.0390625, places=6)

    def test_p_value_never_exceeds_one(self):
        _, p = mcnemar_exact(3, 2)
        self.assertLessEqual(p, 1.0)

    def test_from_pairs_counts_discordance(self):
        a = [True, True, True, False, True]
        b = [False, False, True, False, False]
        # A-only-right: idx0,1,4 -> 3 ; B-only-right: none -> 0
        bb, c, n, p = mcnemar_from_pairs(a, b)
        self.assertEqual(bb, 3)
        self.assertEqual(c, 0)
        self.assertEqual(n, 3)
        self.assertLess(p, 0.3)

    def test_from_pairs_length_mismatch(self):
        with self.assertRaises(ValueError):
            mcnemar_from_pairs([True], [True, False])


class BootstrapTests(unittest.TestCase):
    def test_identical_systems_ci_contains_zero(self):
        a = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        observed, lo, hi = bootstrap_diff_ci(a, list(a), iterations=2000, seed=7)
        self.assertEqual(observed, 0.0)
        self.assertLessEqual(lo, 0.0)
        self.assertGreaterEqual(hi, 0.0)

    def test_large_consistent_gap_excludes_zero(self):
        a = [1.0] * 40
        b = [0.0] * 40
        observed, lo, hi = bootstrap_diff_ci(a, b, iterations=2000, seed=7)
        self.assertEqual(observed, 1.0)
        self.assertGreater(lo, 0.0)

    def test_deterministic_with_seed(self):
        a = [1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0]
        b = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
        first = bootstrap_diff_ci(a, b, iterations=1500, seed=99)
        second = bootstrap_diff_ci(a, b, iterations=1500, seed=99)
        self.assertEqual(first, second)

    def test_empty_is_zero(self):
        self.assertEqual(bootstrap_diff_ci([], [], iterations=100), (0.0, 0.0, 0.0))

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            bootstrap_diff_ci([1.0], [1.0, 0.0])


class CompoundingTests(unittest.TestCase):
    def test_independence_baseline_compounds(self):
        self.assertAlmostEqual(independence_baseline(0.9, 1), 0.9, places=6)
        self.assertAlmostEqual(independence_baseline(0.9, 10), 0.9 ** 10, places=6)
        self.assertAlmostEqual(independence_baseline(0.9, 20), 0.12157665, places=6)

    def test_perfect_step_never_compounds(self):
        self.assertEqual(independence_baseline(1.0, 50), 1.0)

    def test_stdev_of_constant_is_zero(self):
        self.assertEqual(stdev([0.5, 0.5, 0.5]), 0.0)

    def test_stdev_small_sample(self):
        self.assertEqual(stdev([0.5]), 0.0)


if __name__ == "__main__":
    unittest.main()
