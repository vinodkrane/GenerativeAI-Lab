"""Generator eval: faithfulness and answer relevancy, with the retriever removed.

The generator is fed the hand-picked ideal context from the dataset instead of
whatever the retriever returns. Context is known to be good, so a low score is
the generator's fault.

    python -m evals.quality.generation
"""

from dotenv import load_dotenv
from deepeval import evaluate
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from app import config
from app.generator import generate
from evals.common import load_goldens, print_summary, summarize_by_metric

load_dotenv()

DATASET = "datasets/faithfulness.json"
JUDGE_MODEL = config.JUDGE_MODEL
THRESHOLD = config.DEFAULT_THRESHOLD


def run():
    goldens = load_goldens(DATASET)

    test_cases = []
    for g in goldens:
        context = g["ideal_context"]
        test_cases.append(
            LLMTestCase(
                input=g["query"],
                actual_output=generate(g["query"], context),
                retrieval_context=context,
            )
        )

    metrics = [
        FaithfulnessMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        AnswerRelevancyMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
    ]
    return summarize_by_metric(evaluate(test_cases=test_cases, metrics=metrics))


if __name__ == "__main__":
    print_summary("generator", run())
