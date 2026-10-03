"""Smallest possible DeepEval example: one metric, one good and one bad answer.

    python scripts/deepeval_quickstart.py
"""

from deepeval import evaluate
from deepeval.metrics import AnswerRelevancyMetric
from deepeval.test_case import LLMTestCase
from dotenv import load_dotenv

load_dotenv()

good = LLMTestCase(
    input="What is the capital of France?",
    actual_output="The capital of France is Paris.",
)
off_topic = LLMTestCase(
    input="What is the capital of France?",
    actual_output="France is a beautiful country famous for its food and wine.",
)

metric = AnswerRelevancyMetric(threshold=0.7, model="gpt-4.1", include_reason=True)
evaluate(test_cases=[good, off_topic], metrics=[metric])
