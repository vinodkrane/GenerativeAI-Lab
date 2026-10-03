"""Pipeline eval: the RAG triad on live retrieve -> rerank -> generate output.

    contextual relevancy : is the retrieved context on topic for the question?
    faithfulness         : is the answer supported by that context?
    answer relevancy     : does the answer address the question?

    python -m evals.quality.rag_triad
"""

from dotenv import load_dotenv
from deepeval import evaluate
from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.test_case import LLMTestCase

from app import config
from app.pipeline import RagPipeline
from evals.common import load_goldens, print_summary, summarize_by_metric

load_dotenv()

DATASET = "datasets/faithfulness.json"  # only the queries are used
JUDGE_MODEL = config.JUDGE_MODEL
THRESHOLD = config.DEFAULT_THRESHOLD


def run(rag):
    goldens = load_goldens(DATASET)

    test_cases = []
    for g in goldens:
        out = rag.invoke(g["query"])
        test_cases.append(
            LLMTestCase(
                input=g["query"],
                actual_output=out["answer"],
                retrieval_context=out["context"],
            )
        )

    metrics = [
        ContextualRelevancyMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        FaithfulnessMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        AnswerRelevancyMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
    ]
    return summarize_by_metric(evaluate(test_cases=test_cases, metrics=metrics))


if __name__ == "__main__":
    print_summary("rag triad", run(RagPipeline()))
