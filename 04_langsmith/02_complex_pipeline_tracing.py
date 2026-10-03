import os
from dotenv import load_dotenv
from langsmith import traceable
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4",
    temperature=0.7,
    max_tokens=500
)

# -------------------------------
# LangSmith Configuration
# -------------------------------
# Set the LangChain project name for LangSmith observability.
os.environ['LANGCHAIN_PROJECT'] = '02_ComplexPipelineTracing'
load_dotenv()

# -------------------------------
# Define Traced Functions
# -------------------------------

# The @traceable decorator marks this function to be monitored in LangSmith.
# LangSmith will log inputs, outputs, and intermediate steps for observability.
@traceable(name="research_and_summarize_pipeline")
def research_pipeline(query: str):
    # Step 1: Research
    research_prompt = f"Research the following topic: {query}"
    research_result = llm.invoke(research_prompt)
    
    # Step 2: Analyze
    analysis_prompt = f"Analyze this research: {research_result}"
    analysis = llm.invoke(analysis_prompt)
    
    # Step 3: Summarize
    summary_prompt = f"Create an executive summary: {analysis}"
    summary = llm.invoke(summary_prompt)
    
    return {
        'research': research_result,
        'analysis': analysis,
        'summary': summary
    }

# Another function traced for observability in LangSmith.
@traceable(name="quality_check")
def quality_check(content: dict):
    summary_text = content['summary'].content
    quality_score = len(summary_text) / 1000  # Simple metric
    return {'content': content, 'quality_score': quality_score}

# Run the research -> analyze -> summarize pipeline with tracing enabled
result = research_pipeline("Impact of AI on healthcare")

# Perform a quality check, also traced in LangSmith
final_result = quality_check(result)
print(final_result)