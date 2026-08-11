import time
import random
from typing import List, Dict, Any
import google.generativeai as genai
from app.config import GEMINI_API_KEY, MOCK_EMBEDDINGS, logger

class EmbeddingService:
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model = "models/gemini-embedding-001"
        self.max_batch_count = 100
        self.max_batch_tokens = 250000

        # Note: We initialize the Gemini configuration if API key is provided
        if self.api_key and self.api_key != "your_gemini_api_key_here" and "YOUR_REAL_API_KEY_HERE" not in self.api_key:
            genai.configure(api_key=self.api_key)
            self.client_configured = True
        else:
            self.client_configured = False

    def get_embeddings(self, texts: List[str], max_retries: int = 5, initial_delay: float = 1.0) -> List[List[float]]:
        """
        Calls the Gemini embeddings API for a list of texts, with exponential backoff on retryable errors.
        Bypasses API if MOCK_EMBEDDINGS is enabled or client is not configured.
        """
        if MOCK_EMBEDDINGS or not self.client_configured:
            logger.info("Mock Embeddings Mode enabled (or key missing). Generating %d mock vectors offline (3072-d).", len(texts))
            # gemini-embedding-001 is 3072-dimensional by default
            return [[0.01 * (idx % 100)] * 3072 for idx in range(len(texts))]

        delay = initial_delay
        for attempt in range(max_retries):
            try:
                logger.info("Calling Gemini Embeddings API for %d texts...", len(texts))
                result = genai.embed_content(
                    model=self.model,
                    content=texts,
                    request_options={"timeout": 30.0}
                )
                # Google Generative AI returns a list of float lists under 'embedding'
                return result["embedding"]
                
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error("Gemini API embedding rate limit or error exceeded max retries. Failing.")
                    raise e
                
                # Exponential backoff with jitter
                sleep_time = delay * (2 ** attempt) + random.uniform(0.0, 1.0)
                logger.warning(
                    "Gemini API embedding issue. Retrying in %.2fs (Attempt %d/%d)... Error: %s",
                    sleep_time, attempt + 1, max_retries, e
                )
                time.sleep(sleep_time)

    def batch_chunks(self, chunks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Splits a list of chunks into batches.
        Capped by both chunk count (100) and total token count (250,000), whichever is hit first.
        """
        batches = []
        current_batch = []
        current_tokens = 0

        for chunk in chunks:
            token_count = chunk.get("token_count", 0)
            
            # Check if this single chunk itself exceeds the token batch limit (highly unlikely)
            if token_count > self.max_batch_tokens:
                logger.warning(
                    "Chunk token count (%d) exceeds max batch tokens limit (%d). Batching it individually.",
                    token_count, self.max_batch_tokens
                )

            # Check if adding this chunk violates either limit
            if (len(current_batch) >= self.max_batch_count or 
                    current_tokens + token_count > self.max_batch_tokens):
                # Save current batch and start a new one
                if current_batch:
                    batches.append(current_batch)
                current_batch = [chunk]
                current_tokens = token_count
            else:
                current_batch.append(chunk)
                current_tokens += token_count

        # Append final batch if it has items
        if current_batch:
            batches.append(current_batch)

        logger.info(
            "Batched %d chunks into %d batches based on size limits (Max: %d chunks, %d tokens per batch).",
            len(chunks), len(batches), self.max_batch_count, self.max_batch_tokens
        )
        return batches

# Global embedding service instance
embedding_service = EmbeddingService()
