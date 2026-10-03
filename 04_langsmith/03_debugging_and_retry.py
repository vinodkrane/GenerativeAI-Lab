import os
from dotenv import load_dotenv
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.llms.base import LLM
from langsmith import traceable

# -------------------------------
# LangSmith + Environment Config
# -------------------------------
os.environ['LANGCHAIN_PROJECT'] = '03_DebugLLMcalls'
load_dotenv()

# -------------------------------
# Prompt Template
# -------------------------------
prompt = PromptTemplate(
    input_variables=["topic", "audience"],
    template="Create a {audience}-friendly summary of {topic}. "
             "Include key points and actionable insights."
)

# -------------------------------
# Custom Failing LLM
# -------------------------------
class FailingLLM(LLM):
    """
    LLM that always raises an error to simulate a failure for LangSmith.
    """
    def _call(self, prompt, stop=None):
        raise RuntimeError("Simulated LLM failure for LangSmith tracing")

    @property
    def _identifying_params(self):
        return {}

    @property
    def _llm_type(self):
        return "failing_llm"

# -------------------------------
# LLM Setup
# -------------------------------
failing_llm = FailingLLM()
llm = ChatOpenAI(
    model="gpt-4",
    temperature=0.7,
    max_tokens=500
)

# -------------------------------
# Chains
# -------------------------------
primary_chain = prompt | failing_llm   # will fail
fallback_chain = prompt | llm           # normal GPT-4 chain
generation_chain = prompt | llm         # for quality-assured runs

# -------------------------------
# Robust Execution (Fallback Logic)
# -------------------------------
@traceable(name="robust_chain_execution")
def execute_with_fallback(input_data: dict):
    config = {
        'run_name': 'Robust Execution with Fallback',
        'tags': ['error-handling', 'fallback'],
        'metadata': {'attempt': 1}
    }

    try:
        # Primary chain will fail here
        return primary_chain.invoke(input_data, config=config)
    except Exception as e:
        # Log error and run fallback chain
        config['metadata'].update({
            'error': str(e),
            'fallback_used': True,
            'attempt': 2
        })
        return fallback_chain.invoke(input_data, config=config)

# -------------------------------
# Quality Assurance (Retry Logic)
# -------------------------------
def assess_quality(result) -> float:
    return 0.9  # Placeholder for demo purposes

@traceable(name="quality_assured_generation")
def generate_with_quality_check(input_data: dict):
    max_attempts = 3
    best_result = None
    best_score = 0.0

    for attempt in range(max_attempts):
        config = {
            'run_name': f'Quality Check Attempt {attempt + 1}',
            'tags': ['quality-assurance', 'retry-logic'],
            'metadata': {
                'attempt': attempt + 1,
                'max_attempts': max_attempts
            }
        }

        result = generation_chain.invoke(input_data, config=config)
        quality_score = assess_quality(result)
        config['metadata']['quality_score'] = quality_score

        if quality_score > best_score:
            best_result = result
            best_score = quality_score

        if quality_score > 0.8:
            return result

    return best_result

# -------------------------------
# Example Usage
# -------------------------------
input_data = {
    "topic": "Artificial Intelligence trends in 2024",
    "audience": "business executives"
}

# Run with fallback-enabled execution
result = execute_with_fallback(input_data)
print("Robust Execution Result:\n", result.content)

# Run with retry + quality-assurance logic
quality_result = generate_with_quality_check(input_data)
print("\nQuality-Assured Generation Result:\n", quality_result.content)