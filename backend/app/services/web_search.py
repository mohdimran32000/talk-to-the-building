"""
Web search tool: searches the web via Tavily API when documents don't have the answer.
"""
import logging

from langsmith import traceable
from tavily import TavilyClient

from app.services.settings import get_tavily_api_key

logger = logging.getLogger(__name__)


@traceable(name="web_search", run_type="tool")
def execute_web_search(query: str) -> str:
    """Search the web using Tavily and return formatted results with source URLs."""
    api_key = get_tavily_api_key()
    if not api_key:
        return "Web search is not configured. Please set a Tavily API key in admin settings."

    client = TavilyClient(api_key=api_key)
    response = client.search(query, max_results=5, include_answer=True)

    parts = []

    # Include Tavily's AI-generated answer if available
    if response.get("answer"):
        parts.append(f"**Summary:** {response['answer']}\n")

    # Format individual results
    results = response.get("results", [])
    if results:
        parts.append("**Sources:**")
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = r.get("content", "")[:200]
            parts.append(f"{i}. [{title}]({url})\n   {snippet}")

    if not parts:
        return "No web results found."

    return "\n\n".join(parts)
