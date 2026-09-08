import tiktoken
from typing import List, Dict, Any, Tuple, Optional
import google.generativeai as genai
from app.config import GEMINI_API_KEY, GEMINI_MODEL, MOCK_EMBEDDINGS, logger
from app.services.db_service import db_service

class LLMService:
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model_name = GEMINI_MODEL
        self.fallback_models = [
            GEMINI_MODEL,
            "gemini-3.5-flash",
            "gemini-3.7-flash",
            "gemini-flash-latest",
            "gemini-3.8-flash",
            "gemini-3.6-flash"
        ]
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for m in self.fallback_models:
            clean = m.replace("models/", "")
            if clean not in seen:
                seen.add(clean)
                deduped.append(clean)
        self.fallback_models = deduped
        self.temperature = 0.2
        self.max_prompt_tokens = 6000  # Safety cap for prompt size
        
        # Keep tiktoken for local token count estimates (fast and free)
        try:
            self.encoder = tiktoken.encoding_for_model("gpt-4o-mini")
        except Exception:
            self.encoder = tiktoken.get_encoding("cl100k_base")

        if self.api_key and self.api_key != "your_gemini_api_key_here" and "YOUR_REAL_API_KEY_HERE" not in self.api_key:
            genai.configure(api_key=self.api_key)
            self.client_configured = True
        else:
            self.client_configured = False

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string."""
        return len(self.encoder.encode(text))

    def _format_context(self, context_chunks: List[Dict[str, Any]]) -> str:
        """Format retrieved chunks with metadata for injection into the prompt."""
        formatted_parts = []
        for idx, chunk in enumerate(context_chunks):
            part = (
                f"Context Chunk #{idx + 1}:\n"
                f"Source File: {chunk['filename']} (Page {chunk['page_number']})\n"
                f"Content:\n{chunk['text']}\n"
                f"----------------------------------------"
            )
            formatted_parts.append(part)
        return "\n".join(formatted_parts)

    def generate_answer(
        self, 
        question: str, 
        context_chunks: List[Dict[str, Any]], 
        conversation_id: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Builds the system prompt, handles safety token limits, calls the Gemini LLM, 
        and returns (answer_text, is_mock).
        """
        # 1. Base System Instructions
        system_prompt = (
            "You are a helpful and factual private company assistant.\n"
            "You are provided with a context of document chunks retrieved from the company database.\n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the User Question ONLY using the provided Context Chunks.\n"
            "2. Cite which document(s) and page number(s) the answer came from.\n"
            "3. If the answer is NOT present in the provided context chunks, state clearly that you do "
            "not have information about that in the company knowledge base, instead of guessing or using outside knowledge.\n"
            "4. Keep your answer professional, factual, and strictly grounded in the context. Do not hallucinate."
        )

        # 2. Retrieve history if conversation_id is provided
        history = []
        if conversation_id:
            # Retrieve last 6 messages (3 turns)
            history = db_service.get_chat_history(conversation_id, limit=6)

        # 3. Format context chunks
        context_str = self._format_context(context_chunks)
        user_message_content = (
            f"Context Chunks:\n{context_str}\n\n"
            f"User Question: {question}"
        )

        # 4. Token Capping Logic
        # Assemble message structure and calculate tokens
        while True:
            # Calculate tokens
            total_tokens = self.count_tokens(system_prompt) + self.count_tokens(user_message_content)
            for msg in history:
                total_tokens += self.count_tokens(msg["content"])

            if total_tokens <= self.max_prompt_tokens:
                break
                
            # If we are over the token limit:
            # Step A: Try pruning older conversation history messages first
            if history:
                logger.warning(
                    "Prompt token count (%d) exceeds limit (%d). Pruning oldest chat history message.",
                    total_tokens, self.max_prompt_tokens
                )
                history.pop(0)  # Remove the oldest message in history
            # Step B: If history is empty and still over, prune lower-similarity context chunks
            elif len(context_chunks) > 1:
                logger.warning(
                    "Prompt token count (%d) exceeds limit (%d) with empty history. Pruning lowest-similarity chunk.",
                    total_tokens, self.max_prompt_tokens
                )
                context_chunks.pop()  # ChromaDB returns chunks sorted by distance ascending, so pop the last (least similar)
                context_str = self._format_context(context_chunks)
                user_message_content = (
                    f"Context Chunks:\n{context_str}\n\n"
                    f"User Question: {question}"
                )
            else:
                # If only one chunk remains and it's still too large, truncate it directly
                logger.warning("Single chunk too large for token limit. Truncating context.")
                truncated_text = context_chunks[0]["text"][:2000]
                context_chunks[0]["text"] = truncated_text + "... [TRUNCATED]"
                context_str = self._format_context(context_chunks)
                user_message_content = (
                    f"Context Chunks:\n{context_str}\n\n"
                    f"User Question: {question}"
                )
                break

        # 5. Check if Mock Mode is active
        # Requires MOCK_EMBEDDINGS=true OR missing client_configured
        if MOCK_EMBEDDINGS or not self.client_configured:
            logger.info("Mock LLM Mode active. Generating simulated answer locally.")
            is_mock = True
            
            filenames = ", ".join(list({c["filename"] for c in context_chunks}))
            context_snippet = context_chunks[0]["text"]
            if len(context_snippet) > 200:
                context_snippet = context_snippet[:200] + "..."

            answer = (
                f"[Mock LLM Response]\n"
                f"Based on the context retrieved from '{filenames}':\n"
                f"The document states: \"{context_snippet}\"\n\n"
                f"Citations: {filenames} (Page {context_chunks[0]['page_number']})"
            )
            return answer, is_mock

        # 6. Execute live call to Google Gemini API (gemini-2.5-flash)
        is_mock = False
        
        # Format messages for Gemini API
        # Gemini roles must be 'user' or 'model' (translated from SQLite 'user' / 'assistant')
        contents = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [msg["content"]]})
        
        # Add current turn
        contents.append({"role": "user", "parts": [user_message_content]})

        last_error = None
        for candidate_model in self.fallback_models:
            max_retries = 3
            delay = 1.0
            quota_exhausted = False
            
            for attempt in range(max_retries):
                try:
                    logger.info("Calling Gemini API (%s)...", candidate_model)
                    model = genai.GenerativeModel(
                        model_name=candidate_model,
                        system_instruction=system_prompt,
                        generation_config={"temperature": self.temperature}
                    )
                    response = model.generate_content(contents, request_options={"timeout": 30.0})
                    answer = response.text
                    return answer, is_mock
                    
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    # If daily quota is exceeded (429), fall back immediately to next candidate model in pool
                    if "429" in err_str and ("quota" in err_str.lower() or "free_tier" in err_str.lower()):
                        logger.warning(
                            "Gemini model '%s' quota exceeded (429). Falling back to next available model in pool...",
                            candidate_model
                        )
                        quota_exhausted = True
                        break
                    
                    if attempt == max_retries - 1:
                        logger.warning("Model '%s' failed after %d retries: %s", candidate_model, max_retries, e)
                        break
                    
                    import random
                    import time
                    sleep_time = delay * (2 ** attempt) + random.uniform(0.0, 1.0)
                    logger.warning(
                        "Gemini API issue on '%s'. Retrying in %.2fs (Attempt %d/%d)... Error: %s",
                        candidate_model, sleep_time, attempt + 1, max_retries, e
                    )
                    time.sleep(sleep_time)

            if not quota_exhausted and last_error and not ("429" in str(last_error)):
                # If error is not quota related (e.g. malformed prompt), don't unnecessarily cycle all models
                break

        logger.error("All candidate Gemini models failed or exceeded quota.")
        raise last_error

# Global LLM service instance
llm_service = LLMService()
