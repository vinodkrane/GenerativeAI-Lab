"""Transcript loading, chunking and the persisted Chroma store."""

import re

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app import config

load_dotenv()


def load_transcripts() -> list[Document]:
    """Read every .vtt file and drop the cue timestamps, keeping only spoken text."""
    docs = []
    for path in sorted(config.TRANSCRIPT_DIR.glob("*.vtt")):
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and line != "WEBVTT" and "-->" not in line:
                lines.append(line)

        session = int(re.search(r"session_(\d+)", path.name).group(1))
        docs.append(Document(page_content=" ".join(lines), metadata={"session": session}))
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
    )
    return splitter.split_documents(docs)


def load_store() -> Chroma:
    """Open the persisted store, building it on first use so we embed only once."""
    embeddings = OpenAIEmbeddings(model=config.EMBEDDING_MODEL)

    if config.VECTOR_DB_DIR.exists():
        return Chroma(persist_directory=str(config.VECTOR_DB_DIR), embedding_function=embeddings)

    chunks = split_documents(load_transcripts())
    return Chroma.from_documents(chunks, embeddings, persist_directory=str(config.VECTOR_DB_DIR))


if __name__ == "__main__":
    store = load_store()
    for doc in store.similarity_search("what is regression testing?", k=3):
        print(f"[session {doc.metadata['session']}] {doc.page_content[:150]}...\n")
