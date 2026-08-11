from fastapi import APIRouter, HTTPException, status, Depends
from app.config import logger, MOCK_EMBEDDINGS, audit_logger
from app.models.chat import ChatRequest, ChatResponse, SourceSnippet
from app.services.retrieval_service import retrieval_service
from app.services.llm_service import llm_service
from app.services.db_service import db_service
from app.services.auth_service import get_current_user
from app.services.rate_limiter import chat_rate_limiter

router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("", response_model=ChatResponse)
def query_chat(
    request: ChatRequest, 
    current_user: dict = Depends(get_current_user),
    _: None = Depends(chat_rate_limiter.check_limit)
):
    """
    Accepts a user question, retrieves relevant context chunks from ChromaDB, 
    consults the LLM, and returns the answer alongside source citations.
    """
    try:
        question = request.question.strip()
        if not question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Question text cannot be empty."
            )

        conversation_id = request.conversation_id
        if conversation_id:
            conversation_id = conversation_id.strip()

        logger.info("Received chat query. Conversation ID: %s", conversation_id)

        # 1. Retrieve relevant chunks from ChromaDB
        relevant_chunks, all_distances = retrieval_service.retrieve_relevant_chunks(
            question=question, 
            k=request.k
        )

        # 2. No-answer Handling
        if not relevant_chunks:
            fallback_answer = "I don't have information about that in the company knowledge base."
            logger.info(
                "No context chunks satisfied the L2 threshold. Triggered fallback response (No LLM call). "
                "All L2 distances observed: %s", 
                all_distances
            )
            
            # If conversation_id is provided, save this turn as well to maintain history flow
            if conversation_id:
                db_service.save_chat_message(conversation_id, "user", question)
                db_service.save_chat_message(conversation_id, "assistant", fallback_answer)

            # Determine mock flag (matches configuration setting)
            is_mock_flag = MOCK_EMBEDDINGS or not getattr(llm_service, "client_configured", False)
            
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: POST /chat | Success: True | Details: Handled fallback no-answer for question: '%s'",
                current_user["username"], current_user["role"], question
            )
            
            return ChatResponse(
                answer=fallback_answer,
                sources=[],
                conversation_id=conversation_id,
                is_mock=is_mock_flag
            )

        # 3. Generate LLM Answer
        logger.info("Relevant chunks found (%d chunks). Proceeding to LLM generation.", len(relevant_chunks))
        answer, is_mock = llm_service.generate_answer(
            question=question,
            context_chunks=relevant_chunks,
            conversation_id=conversation_id
        )

        # 4. Save history turns to SQLite (if session ID is provided)
        if conversation_id:
            db_service.save_chat_message(conversation_id, "user", question)
            db_service.save_chat_message(conversation_id, "assistant", answer)

        # 5. Format sources response (snippets are truncated for display)
        sources_list = []
        for chunk in relevant_chunks:
            snippet = chunk["text"]
            if len(snippet) > 150:
                snippet = snippet[:150].strip() + "..."
            
            sources_list.append(SourceSnippet(
                filename=chunk["filename"],
                page_number=chunk["page_number"],
                snippet=snippet
            ))

        logger.info(
            "Chat response successfully formulated. Sources cited: %s",
            list({s.filename for s in sources_list})
        )

        cited_sources = list({s.filename for s in sources_list})
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /chat | Success: True | Details: Answered question: '%s' | Cited: %s",
            current_user["username"], current_user["role"], question, str(cited_sources)
        )

        return ChatResponse(
            answer=answer,
            sources=sources_list,
            conversation_id=conversation_id,
            is_mock=is_mock
        )

    except HTTPException as he:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /chat | Success: False | Details: Validation failed: %s",
            current_user["username"], current_user["role"], he.detail
        )
        raise he
    except Exception as e:
        logger.error("Error occurred in chat query pipeline: %s", e, exc_info=True)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /chat | Success: False | Details: Error: %s",
            current_user["username"], current_user["role"], str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while formulating the answer: {str(e)}"
        )
