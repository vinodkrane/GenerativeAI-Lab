import os
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

# -------------------------------
# LangSmith Configuration
# -------------------------------
# Set the LangChain project name for LangSmith observability.
os.environ['LANGCHAIN_PROJECT'] = 'langsmith-demo'

prompt = PromptTemplate(
    input_variables=["topic", "audience"],
    template="Create a {audience}-friendly summary of {topic}. "
             "Include key points and actionable insights."
)

llm = ChatOpenAI(
    model="gpt-4",
    temperature=0.7,
    max_tokens=500
)

chain = prompt | llm

# -------------------------------
# LangSmith Run Configuration
# -------------------------------
# This dictionary allows you to provide additional metadata to LangSmith for observability.
# - run_name: a friendly name for this specific run
# - tags: labels to categorize this run
# - metadata: detailed info like model settings, prompt version, experiment id
config = {
    "run_name": "Audience-specific summarization",
    "tags": ["summarization", "content-generation", "gpt-4"],
    "metadata": {"user_id": "user_123", "session_id": "sess_456"}
}

result = chain.invoke(
    {
        "topic": "Artificial Intelligence trends in 2024",
        "audience": "business executives"
    },
    config=config  # Pass run config for LangSmith observability
)

print(result.content)