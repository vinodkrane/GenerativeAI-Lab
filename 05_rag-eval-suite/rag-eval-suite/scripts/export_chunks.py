"""Dump every chunk in the vector store to chunks_dump.json.

Handy for scanning chunks when hand-picking ideal_context for a dataset.

    python scripts/export_chunks.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.vector_store import load_store

store = load_store()

# Chroma omits the chunk text unless asked for it
data = store._collection.get(include=["documents", "metadatas"])

chunks = [
    {"id": cid, "text": text, "meta": meta}
    for cid, text, meta in zip(data["ids"], data["documents"], data["metadatas"])
]
chunks.sort(key=lambda c: (c["meta"].get("session", 0), c["id"]))

Path("chunks_dump.json").write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"wrote {len(chunks)} chunks to chunks_dump.json")
for session, count in sorted(Counter(c["meta"].get("session") for c in chunks).items()):
    print(f"  session {session}: {count} chunks")
