"""F05: deterministic RRF fusion, stable ordering and dedupe counterexamples."""

from __future__ import annotations

import unittest

from personal_knowledge.search import rrf_fuse

A = ("src-a", "v1", 0)
B = ("src-b", "v1", 1)
C = ("src-c", "v1", 2)


class RrfFusionTests(unittest.TestCase):
    def test_single_ranking_preserves_order(self) -> None:
        self.assertEqual([A, B, C], [key for key, _score in rrf_fuse([[A, B, C]])])

    def test_agreement_ranks_above_single_arm_hits(self) -> None:
        fused = rrf_fuse([[A, B], [B, C]])
        self.assertEqual(B, fused[0][0])

    def test_ties_are_broken_by_stable_key_order(self) -> None:
        fused = rrf_fuse([[A, B], [B, A]])
        self.assertEqual([A, B], [key for key, _score in fused])
        self.assertEqual(fused[0][1], fused[1][1])

    def test_identical_rankings_are_deduplicated(self) -> None:
        fused = rrf_fuse([[A, B], [A, B]])
        self.assertEqual([A, B], [key for key, _score in fused])
        self.assertEqual(1, len([key for key, _score in fused if key == A]))

    def test_empty_rankings_produce_no_results(self) -> None:
        self.assertEqual([], rrf_fuse([[], []]))
        self.assertEqual([], rrf_fuse([]))

    def test_scores_are_monotonic_non_increasing(self) -> None:
        fused = rrf_fuse([[A, B, C], [C, B]])
        scores = [score for _key, score in fused]
        self.assertEqual(sorted(scores, reverse=True), scores)

    def test_order_is_independent_of_dict_iteration(self) -> None:
        first = rrf_fuse([[A, B, C], [C, A]])
        second = rrf_fuse([[C, A], [A, B, C]])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
