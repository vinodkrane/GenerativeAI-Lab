"""Retriever eval: contextual recall and contextual precision.

Only the retriever is under test here. The generator is not called, so a low
score points at retrieval (chunking, embeddings, reranking) and nothing else.

    python -m evals.quality.retrieval
"""

from dotenv import load_dotenv
from deepeval import evaluate
from deepeval.metrics import ContextualPrecisionMetric, ContextualRecallMetric
from deepeval.test_case import LLMTestCase

from app import config
from app.retriever import RerankingRetriever
from evals.common import load_goldens, print_summary, summarize_by_metric

load_dotenv()

DATASET = "datasets/retrieval.json"
JUDGE_MODEL = config.JUDGE_MODEL
THRESHOLD = config.DEFAULT_THRESHOLD


def run(retriever):
    goldens = load_goldens(DATASET)

    test_cases = []
    for g in goldens:
        docs = retriever.invoke(g["query"])
        test_cases.append(
            LLMTestCase(
                input=g["query"],
                expected_output=g["ideal_answer"],
                retrieval_context=[d.page_content for d in docs],
                actual_output="",  # not used by the retrieval metrics
            )
        )

    metrics = [
        ContextualRecallMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        ContextualPrecisionMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
    ]

    # the config travels with the run so reports are tagged with what was tested
    result = evaluate(
        test_cases=test_cases,
        metrics=metrics,
        hyperparameters={
            "embedding_model": config.EMBEDDING_MODEL,
            "reranker": config.RERANKER_MODEL,
            "chunk_size": config.CHUNK_SIZE,
            "chunk_overlap": config.CHUNK_OVERLAP,
            "fetch_k": retriever.fetch_k,
            "top_k": retriever.top_k,
            "judge_model": JUDGE_MODEL,
            "dataset": DATASET,
        },
    )
    return summarize_by_metric(result)


if __name__ == "__main__":
    print_summary("retriever", run(RerankingRetriever()))
