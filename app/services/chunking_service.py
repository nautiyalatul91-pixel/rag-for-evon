import re
import tiktoken
from datetime import datetime
from typing import List, Dict, Any
from app.config import logger

class ChunkingService:
    def __init__(self):
        # text-embedding-3-small uses cl100k_base
        try:
            self.encoder = tiktoken.encoding_for_model("text-embedding-3-small")
        except Exception:
            self.encoder = tiktoken.get_encoding("cl100k_base")
            
        self.min_chunk_size = 500
        self.max_chunk_size = 800
        self.target_overlap = 80  # target overlap in tokens (50-100 range)

    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in a string."""
        return len(self.encoder.encode(text))

    def split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences, preserving spacing."""
        # Split on sentence terminals followed by space
        sentence_ends = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentence_ends if s.strip()]

    def chunk_document(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Chunks list of pages.
        Returns a list of chunk dicts:
        {
            "text": str,
            "page_number": int,
            "chunk_index": int,
            "timestamp": str,
            "token_count": int
        }
        """
        all_chunks = []
        chunk_index = 0
        timestamp = datetime.utcnow().isoformat() + "Z"

        for page in pages:
            page_num = page["page_number"]
            page_text = page["text"]
            
            if not page_text.strip():
                continue
                
            sentences = self.split_into_sentences(page_text)
            if not sentences:
                continue

            # Calculate token counts for all sentences
            sentence_tokens = []
            for s in sentences:
                tokens = self.encoder.encode(s)
                token_count = len(tokens)
                
                # Handle edge case where a single sentence is larger than max_chunk_size
                if token_count > self.max_chunk_size:
                    # Split the sentence into smaller word-based sub-sentences
                    words = s.split(" ")
                    temp_s = ""
                    for word in words:
                        test_s = (temp_s + " " + word).strip()
                        if len(self.encoder.encode(test_s)) > self.max_chunk_size:
                            sentence_tokens.append((temp_s, len(self.encoder.encode(temp_s))))
                            temp_s = word
                        else:
                            temp_s = test_s
                    if temp_s:
                        sentence_tokens.append((temp_s, len(self.encoder.encode(temp_s))))
                else:
                    sentence_tokens.append((s, token_count))

            # Run greedy chunking with overlap backtracking
            idx = 0
            n_sentences = len(sentence_tokens)
            
            while idx < n_sentences:
                current_chunk_text = []
                current_tokens = 0
                start_idx = idx
                
                # Add sentences to current chunk
                while idx < n_sentences:
                    s_text, s_token_count = sentence_tokens[idx]
                    
                    # If adding this sentence exceeds the maximum chunk size, stop
                    if current_tokens + s_token_count > self.max_chunk_size and current_tokens >= self.min_chunk_size:
                        break
                        
                    current_chunk_text.append(s_text)
                    current_tokens += s_token_count
                    idx += 1
                
                # Construct chunk
                chunk_str = " ".join(current_chunk_text)
                
                all_chunks.append({
                    "text": chunk_str,
                    "page_number": page_num,
                    "chunk_index": chunk_index,
                    "timestamp": timestamp,
                    "token_count": current_tokens
                })
                chunk_index += 1
                
                # If we've processed all sentences on the page, we're done
                if idx >= n_sentences:
                    break
                    
                # Backtrack to create overlap for the next chunk
                # Find how many sentences to backtrack to get close to target_overlap tokens
                overlap_tokens = 0
                backtrack_count = 0
                
                for back_idx in range(idx - 1, start_idx, -1):
                    _, s_token_count = sentence_tokens[back_idx]
                    if overlap_tokens + s_token_count > self.target_overlap + 20: # Allow a small buffer
                        break
                    overlap_tokens += s_token_count
                    backtrack_count += 1
                
                # Reset the main index to the start of the overlap
                if backtrack_count > 0:
                    idx = idx - backtrack_count

        logger.info("Generated %d chunks from document content.", len(all_chunks))
        return all_chunks

# Global chunking service instance
chunking_service = ChunkingService()
