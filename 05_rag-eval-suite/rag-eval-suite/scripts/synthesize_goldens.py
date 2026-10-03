"""Draft a golden set from random transcript chunks using DeepEval's Synthesizer.

The output is a DRAFT. Review every row before using it: check that the question
is grounded in the chunk, trim padded answers, and fill in the source session.

    python scripts/synthesize_goldens.py
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepeval.synthesizer import Synthesizer
from dotenv import load_dotenv

from app.vector_store import load_transcripts, split_documents

load_dotenv()

OUTPUT = Path("datasets/synthetic_draft.json")
SAMPLE_SIZE = 15
GENERATOR_MODEL = "gpt-4.1-mini"

chunks = [c.page_content for c in split_documents(load_transcripts())]
sample = random.sample(chunks, min(SAMPLE_SIZE, len(chunks)))

synthesizer = Synthesizer(model=GENERATOR_MODEL)
goldens = synthesizer.generate_goldens_from_contexts(
    contexts=[[chunk] for chunk in sample],
    include_expected_output=True,
    max_goldens_per_context=1,
)

rows = [
    {
        "id": f"g{i:03d}",
        "query": g.input,
        "ideal_answer": g.expected_output,
        "source": "TODO-verify",
    }
    for i, g in enumerate(goldens, start=1)
]

OUTPUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"wrote {len(rows)} draft goldens to {OUTPUT}. Review them before use.")
