from pydantic import BaseModel, Field
from typing import List, Optional

class ChatRequest(BaseModel):
    question: str = Field(..., description="The user's question in natural language.")
    conversation_id: Optional[str] = Field(None, description="Optional conversation session ID for multi-turn history.")
    k: int = Field(5, description="Number of context chunks to retrieve.")

class SourceSnippet(BaseModel):
    filename: str = Field(..., description="The name of the source document.")
    page_number: int = Field(..., description="The page number where the chunk originated.")
    snippet: str = Field(..., description="A short text snippet of the source chunk.")

class ChatResponse(BaseModel):
    answer: str = Field(..., description="The generated response from the LLM.")
    sources: List[SourceSnippet] = Field(..., description="The list of source chunks used to formulate the answer.")
    conversation_id: Optional[str] = Field(None, description="The conversation session ID.")
    is_mock: bool = Field(False, description="Flag indicating if the response was generated locally via Mock Mode.")
