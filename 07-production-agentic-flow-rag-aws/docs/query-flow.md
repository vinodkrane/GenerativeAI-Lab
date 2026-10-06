# Query flow

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API
    participant Q as Query supervisor
    participant R as Retrieval Agent
    participant S as OpenSearch
    participant G as Generator
    participant V as Citation validator
    C->>A: POST /query (Bearer ID token)
    A->>A: verify JWT -> User(sub, tenant, groups)
    A->>Q: answer(user, question)
    Q->>Q: AccessFilter.for_user(user)  (fixed before any model call)
    Q->>R: plan(question)
    R-->>Q: route + <=3 queries (original question always kept)
    alt out of scope
        Q-->>C: abstain (out_of_scope)
    end
    loop at most max_search_rounds
        Q->>S: BM25 + kNN per query, each with tenant + ACL filter
        S-->>Q: candidates
        Q->>Q: RRF fusion, ACL re-check, rerank
        Q->>R: assess(question, top passages)
        R-->>Q: sufficient? selected labels, optional follow-up
    end
    alt insufficient
        Q-->>C: abstain (insufficient_evidence)
    end
    loop at most generation_attempts (2)
        Q->>G: question + selected evidence only
        G-->>Q: sentences with citations | insufficient
        Q->>V: validate every sentence
        V-->>Q: ok | problems (fed back once)
    end
    Q-->>C: answer + citations, or abstain (citation_validation_failed)
```

## Validation rules (deterministic, `agents/citations.py`)

Every sentence must:

- cite at least one evidence id, and only ids that were supplied;
- have ≥ 50 % of its content terms present in the cited passages;
- contain no number that is absent from the cited passages;
- not contain the system-prompt canary.

One failed attempt produces feedback for one regeneration. A second failure abstains.

## Abstention reasons

| Reason | When |
|---|---|
| `out_of_scope` | greetings, requests to perform actions, questions not answerable from documents |
| `insufficient_evidence` | nothing the user may read supports an answer (also returned when no index exists yet) |
| `citation_validation_failed` | two answers failed validation |
| `budget_exceeded` | per-query LLM call or token budget hit |

Abstention messages are identical whether a document does not exist or exists but is not
visible to the caller, so the API does not leak existence across ACLs.
