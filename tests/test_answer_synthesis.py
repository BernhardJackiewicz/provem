import unittest

from cognitive_memory.answer import extractive_span, question_type


class QuestionTypeTests(unittest.TestCase):
    def test_types(self):
        self.assertEqual(question_type("Where did Noah camp?"), "location")
        self.assertEqual(question_type("Who is Maya dating?"), "person")
        self.assertEqual(question_type("When did Caroline join the group?"), "temporal")
        self.assertEqual(question_type("How many siblings does Ben have?"), "count")
        self.assertEqual(question_type("Is Maya moving to Oslo?"), "yes_no")


class ExtractiveSpanTests(unittest.TestCase):
    def test_location_after_preposition(self):
        span = extractive_span(
            "Where did Noah camp?",
            "I just took my family camping near Cedar Lake last week.",
        )
        self.assertIn("cedar lake", span.lower())
        self.assertLessEqual(len(span.split()), 12)

    def test_location_visited(self):
        span = extractive_span(
            "Where did Noah take the class?",
            "Yesterday I took the class to the Observatory.",
        )
        self.assertIn("observatory", span.lower())

    def test_person_after_relationship_word(self):
        span = extractive_span(
            "Who is Maya dating?",
            "Maya said she is dating Alex now.",
        )
        self.assertIn("alex", span.lower())
        self.assertNotIn("maya", span.lower())

    def test_temporal_weekday(self):
        span = extractive_span(
            "When is the support group?",
            "The support group is on Friday.",
        )
        self.assertEqual(span.lower(), "friday")

    def test_temporal_month_day(self):
        span = extractive_span(
            "When did Caroline go to the group?",
            "Caroline went to the group on 7 May, 2023.",
        )
        self.assertIn("7 may", span.lower())

    def test_count_numeric(self):
        span = extractive_span("How many dogs does Ben have?", "Ben has 3 dogs at home.")
        self.assertEqual(span, "3")

    def test_span_never_exceeds_max_tokens(self):
        long_text = "word " * 50 + "near Cedar Lake"
        span = extractive_span("Where did they go?", long_text, max_tokens=12)
        self.assertLessEqual(len(span.split()), 12)

    def test_empty_text_returns_empty(self):
        self.assertEqual(extractive_span("Where?", ""), "")

    def test_object_novel_phrase(self):
        span = extractive_span(
            "What instrument does Noah play?",
            "Noah plays the tenor saxophone.",
        )
        self.assertIn("saxophone", span.lower())


if __name__ == "__main__":
    unittest.main()
