"""Central settings for the RAG app and the eval suite."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# data
TRANSCRIPT_DIR = ROOT / "data" / "transcripts"
VECTOR_DB_DIR = ROOT / "chroma_store"

# chunking
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# retrieval
EMBEDDING_MODEL = "text-embedding-3-large"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
FETCH_K = 10   # candidates pulled from the vector store
TOP_K = 5      # chunks kept after reranking

# generation
LLM_MODEL = "gpt-4o-mini"
ABSTAIN_MESSAGE = "I don't have enough information in the course material to answer that."

# evaluation
JUDGE_MODEL = "gpt-4o-mini"
DEFAULT_THRESHOLD = 0.7
