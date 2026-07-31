"""Tests for the aggregation-question router (cognitive_memory.answer)."""

import unittest

from cognitive_memory.answer import is_aggregation_question


class AggregationRouterTests(unittest.TestCase):
    def test_list_union_questions_match(self):
        for q in (
            "Which cities has Jon visited?",
            "What items has Melanie bought?",
            "What musical artists/bands has Melanie seen?",
            "Did Jon and Gina both participate in dance competitions?",
            "How many times did Caroline visit the support group?",
            "What are the names of Melanie's kids?",
            "Name all the places John traveled to.",
        ):
            self.assertTrue(is_aggregation_question(q), q)

    def test_single_fact_questions_do_not_match(self):
        for q in (
            "What is Caroline's identity?",
            "When did Caroline go to the LGBTQ support group?",
            "Where was John in July 2023?",
            "What did Caroline research?",
            "Who supports Caroline when she has a negative experience?",
            "Is Melanie a painter?",
        ):
            self.assertFalse(is_aggregation_question(q), q)


if __name__ == "__main__":
    unittest.main()
