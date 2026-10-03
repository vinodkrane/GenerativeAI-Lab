PY ?= python

.PHONY: app retrieval generation triad answer safety ops suite baseline compare

app:
	streamlit run app/chat_ui.py

# component and pipeline evals
retrieval:
	$(PY) -m evals.quality.retrieval
generation:
	$(PY) -m evals.quality.generation
triad:
	$(PY) -m evals.quality.rag_triad
answer:
	$(PY) -m evals.quality.answer_quality

# safety and operations
safety:
	$(PY) -m evals.safety
ops:
	$(PY) -m evals.operational

# regression workflow
baseline:
	$(PY) -m evals.regression.run_suite --baseline --label "baseline"
suite:
	$(PY) -m evals.regression.run_suite
compare:
	$(PY) -m evals.regression.compare
