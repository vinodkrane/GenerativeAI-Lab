import asyncio
import traceback
from dotenv import load_dotenv
load_dotenv()

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from graph import build_graph


async def run_query(query: str, user_id: str = "user_001"):
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"{'='*60}\n")

    async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as saver:
        graph = build_graph(saver)

        initial_state = {
            "messages": [],
            "user_query": query,
            "user_id": user_id,
            "task_queue": [],
            "current_task": None,
            "plan_rationale": "",
            "retrieved_chunks": [],
            "fused_context": "",
            "retrieval_round": 0,
            "rewritten_queries": [],
            "draft_answer": None,
            "reasoning_trace": [],
            "tool_results": [],
            "critic_score": None,
            "iteration_count": 0,
            "token_budget_used": 0,
            "episodic_context": [],
            "final_answer": None,
        }

        config = {"configurable": {"thread_id": f"thread_{user_id}"}}

        try:
            async for event in graph.astream(
                initial_state,
                config=config,
                stream_mode="updates",
            ):
                for node_name, node_output in event.items():
                    print(f"\n[NODE: {node_name}]")
                    if not node_output:
                        continue

                    if "plan_rationale" in node_output and node_output["plan_rationale"]:
                        print(f"  Plan: {node_output['plan_rationale']}")

                    if "fused_context" in node_output and node_output["fused_context"]:
                        preview = node_output["fused_context"][:300].replace("\n", " ")
                        print(f"  Context: {preview}...")

                    if "draft_answer" in node_output and node_output["draft_answer"]:
                        preview = node_output["draft_answer"][:400].replace("\n", " ")
                        print(f"  Draft: {preview}...")

                    if "critic_score" in node_output and node_output["critic_score"]:
                        s = node_output["critic_score"]
                        print(f"  Score: {s.get('composite',0):.2f} | "
                              f"G={s.get('groundedness',0):.2f} "
                              f"R={s.get('relevance',0):.2f} "
                              f"C={s.get('completeness',0):.2f}")
                        reason = s.get("failure_reason")
                        print(f"  Verdict: {'FAIL — ' + reason if reason else 'PASS ✓'}")

                    if "final_answer" in node_output and node_output["final_answer"]:
                        print(f"\n{'='*60}")
                        print("FINAL ANSWER")
                        print(f"{'='*60}")
                        print(node_output["final_answer"])
                        print(f"{'='*60}\n")

        except Exception:
            print("\n[ERROR] Exception during graph execution:")
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_query(
        "What are the key principles of Agentic RAG systems?"
    ))