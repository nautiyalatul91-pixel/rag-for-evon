from typing import List, Dict, Any, Tuple
from app.config import RETRIEVAL_THRESHOLD, logger
from app.services.db_service import db_service
from app.services.embedding_service import embedding_service

class RetrievalService:
    def retrieve_relevant_chunks(self, question: str, k: int = 5) -> Tuple[List[Dict[str, Any]], List[float]]:
        """
        Embeds the question, searches the ChromaDB vector database,
        logs L2 distance scores, and filters chunks by threshold.
        
        Returns:
            Tuple[List[Dict], List[float]]: (filtered_relevant_chunks, all_distance_scores)
        """
        logger.info("Starting retrieval for question: '%s' (k=%d)", question, k)

        # 1. Embed the query
        try:
            embeddings = embedding_service.get_embeddings([question])
            query_embedding = embeddings[0]
        except Exception as e:
            logger.error("Failed to generate embedding for query: %s", e)
            raise e

        # 2. Query ChromaDB
        # We query for up to k results
        try:
            results = db_service.collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                include=["documents", "metadatas", "distances"]
            )
        except Exception as e:
            logger.error("Failed to query ChromaDB: %s", e)
            raise e

        # Check if results are empty
        if not results or not results.get("ids") or len(results["ids"][0]) == 0:
            logger.info("ChromaDB query returned 0 matches.")
            return [], []

        # 3. Process, log, and filter
        filtered_chunks = []
        all_scores = []

        # ChromaDB queries return lists nested inside lists (batch size of 1)
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        for idx in range(len(documents)):
            text = documents[idx]
            meta = metadatas[idx]
            dist = distances[idx]
            all_scores.append(dist)

            filename = meta.get("source_filename", "unknown")
            page_num = meta.get("page_number", 1)

            # Log the actual L2 distance score (Privacy-compliant: no plaintext chunk logged)
            logger.info(
                "Retrieved chunk candidate from file: '%s' (page %d) | L2 Distance Score: %.4f (Threshold: %.2f)",
                filename, page_num, dist, RETRIEVAL_THRESHOLD
            )

            # Filter by L2 distance threshold
            if dist <= RETRIEVAL_THRESHOLD:
                filtered_chunks.append({
                    "text": text,
                    "filename": filename,
                    "page_number": page_num,
                    "distance": dist
                })
            else:
                logger.info(
                    "Chunk from file '%s' (page %d) discarded: L2 distance (%.4f) exceeds threshold (%.2f)",
                    filename, page_num, dist, RETRIEVAL_THRESHOLD
                )

        logger.info(
            "Retrieval summary: %d candidates found | %d passed L2 threshold constraint.",
            len(all_scores), len(filtered_chunks)
        )
        return filtered_chunks, all_scores

# Global retrieval service instance
retrieval_service = RetrievalService()
