from __future__ import annotations

import pytest

from helpers import ALICE, POLICY_MD, ingest
from lean_rag.container import Container
from lean_rag.reindex import Reindexer

pytestmark = pytest.mark.integration


def _statuses(c: Container) -> dict[str, str]:
    return {v["index_name"]: v["status"] for v in c.repo.list_index_versions()}


def test_build_evaluate_promote_and_rollback(container: Container) -> None:
    ingest(container, "policy.md", POLICY_MD, ALICE, ["staff"], document_id="policy")
    live_ids_before = container.index.chunk_ids("policy")
    reindexer = Reindexer(container)

    report = reindexer.build(2)
    assert report["status"] == "READY" and report["documents"] == 1
    assert report["doc_recall_at_5"] == 1.0 and report["live_doc_recall_at_5"] == 1.0
    assert container.index.live_index() == "chunks_v1"  # building never touches the live alias
    assert container.index.chunk_ids("policy", index="chunks_v1") == live_ids_before

    reindexer.promote("chunks_v2")
    assert container.index.live_index() == "chunks_v2"
    assert _statuses(container) == {"chunks_v1": "RETIRED", "chunks_v2": "LIVE"}
    assert not container.query.answer(ALICE, "What is the nightly hotel limit in London?").abstained

    assert reindexer.rollback() == "chunks_v1"
    assert container.index.live_index() == "chunks_v1"
    assert _statuses(container)["chunks_v1"] == "LIVE"


def test_refuses_to_rebuild_live_or_existing_index(container: Container) -> None:
    ingest(container, "policy.md", POLICY_MD, ALICE, ["staff"])
    reindexer = Reindexer(container)
    with pytest.raises(SystemExit, match="live"):
        reindexer.build(1)
    reindexer.build(2)
    with pytest.raises(SystemExit, match="already exists"):
        reindexer.build(2)


def test_rejected_index_cannot_be_promoted(container: Container) -> None:
    ingest(container, "policy.md", POLICY_MD, ALICE, ["staff"], document_id="policy")
    container.repo.update_fields(
        "policy", filename="policy.exe"
    )  # raw object can no longer be found or parsed
    reindexer = Reindexer(container)
    report = reindexer.build(2)
    assert report["status"] == "REJECTED" and report["failures"]
    with pytest.raises(SystemExit, match="REJECTED"):
        reindexer.promote("chunks_v2")
    assert container.index.live_index() == "chunks_v1"
