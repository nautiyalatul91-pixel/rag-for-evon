from typing import List, Dict, Any, Tuple, Optional
from app.config import RETRIEVAL_THRESHOLD, logger
from app.services.db_service import db_service
from app.services.embedding_service import embedding_service

class RetrievalService:
    def retrieve_relevant_chunks(
        self,
        question: str,
        k: int = 5,
        collection_name: str = "company_knowledge_base_gemini_3072",
        user_role: Optional[str] = None,
        username: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[float]]:
        """
        Embeds the question, searches the ChromaDB vector database using role-based
        access filtering at the vector index level (where clause), logs L2 distance scores,
        and filters chunks by threshold.
        
        Returns:
            Tuple[List[Dict], List[float]]: (filtered_relevant_chunks, all_distance_scores)
        """
        logger.info(
            "Starting retrieval for question: '%s' in collection '%s' (user: %s, role: %s, k=%d)",
            question, collection_name, username or "anonymous", user_role or "unfiltered", k
        )

        # 1. Embed the query
        try:
            embeddings = embedding_service.get_embeddings([question])
            query_embedding = embeddings[0]
        except Exception as e:
            logger.error("Failed to generate embedding for query: %s", e)
            raise e

        # 2. Build Role Hierarchy Access Filter (Phase 15)
        canonical_role_id = None
        where_clause = None
        if user_role:
            role_record = db_service.get_role(user_role)
            if not role_record:
                for r in db_service.list_roles():
                    if user_role.lower() in (r["role_id"].lower(), r["role_name"].lower()):
                        role_record = r
                        break
            canonical_role_id = role_record["role_id"] if role_record else user_role.lower()
            where_clause = {f"access_{canonical_role_id}": True}

        # 3. Query ChromaDB with native metadata where clause
        try:
            target_collection = db_service.collections.get(collection_name)
            if not target_collection:
                raise ValueError(f"ChromaDB collection '{collection_name}' not configured.")

            total_chunks = target_collection.count()
            accessible_chunks = total_chunks
            if where_clause:
                try:
                    accessible_chunks = len(target_collection.get(where=where_clause, include=[])["ids"])
                except Exception as cnt_err:
                    logger.warning("Failed to count accessible chunks for role '%s': %s", canonical_role_id, cnt_err)
                    accessible_chunks = -1

            logger.info(
                "Retrieval access filter applied for user '%s' (role: '%s') in collection '%s': "
                "%d of %d total chunks are within access scope.",
                username or "anonymous", canonical_role_id or "unfiltered", collection_name,
                accessible_chunks, total_chunks
            )

            query_kwargs = {
                "query_embeddings": [query_embedding],
                "n_results": k,
                "include": ["documents", "metadatas", "distances"]
            }
            if where_clause:
                query_kwargs["where"] = where_clause

            results = target_collection.query(**query_kwargs)
        except Exception as e:
            logger.error("Failed to query ChromaDB: %s", e)
            raise e

        # Check if results are empty
        if not results or not results.get("ids") or len(results["ids"][0]) == 0:
            logger.info("ChromaDB query returned 0 matches within role access scope.")
            return [], []

        # 4. Process, log, and filter
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
            "Retrieval summary for user '%s' (role: '%s'): %d candidates found | %d passed L2 threshold constraint.",
            username or "anonymous", canonical_role_id or "unfiltered", len(all_scores), len(filtered_chunks)
        )
        return filtered_chunks, all_scores

# Global retrieval service instance
retrieval_service = RetrievalService()
