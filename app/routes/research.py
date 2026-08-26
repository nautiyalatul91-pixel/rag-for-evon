import time
from typing import List, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.services.auth_service import get_current_user
from app.services.rate_limiter import research_rate_limiter, chat_rate_limiter
from app.services.research_service import research_service
from app.config import logger, audit_logger

router = APIRouter(prefix="/research", tags=["Research"])

class ResearchRequest(BaseModel):
    company: str = Field(..., description="Company name, website URL, or LinkedIn profile link.")

class ResearchResponse(BaseModel):
    research_id: str = Field("", description="Persistent SQLite lookup ID for this research profile")
    company_name: str
    industry: str
    what_they_do: str
    tech_stack: List[str]
    size_stage: str
    recent_news: List[str]
    business_needs: List[str]
    citations: Dict[str, str]

@router.post("", response_model=ResearchResponse)
async def perform_research(
    req: ResearchRequest,
    current_user: dict = Depends(get_current_user),
    _ = Depends(research_rate_limiter.check_limit)
):
    """
    Perform web research on a company using Google Search grounding.
    """
    username = current_user.get("username", "unknown")
    role = current_user.get("role", "unknown")
    company_query = req.company.strip()

    if not company_query:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /research | Success: False | Details: Empty company input query",
            username, role
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company query cannot be empty."
        )

    logger.info("User '%s' requested web research on: '%s'", username, company_query)

    try:
        start_time = time.time()
        # Call research service
        profile = research_service.research_company(company_query)
        duration = time.time() - start_time

        # Save to SQLite
        from app.services.db_service import db_service
        research_id = db_service.save_research_profile(profile)

        # Validate that we got a valid response structure
        response_data = ResearchResponse(
            research_id=research_id,
            company_name=profile.get("company_name", company_query),
            industry=profile.get("industry", "not publicly available"),
            what_they_do=profile.get("what_they_do", "not publicly available"),
            tech_stack=profile.get("tech_stack", []),
            size_stage=profile.get("size_stage", "not publicly available"),
            recent_news=profile.get("recent_news", []),
            business_needs=profile.get("business_needs", []),
            citations=profile.get("citations", {})
        )

        success = "error during research" not in response_data.industry.lower()
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /research | Success: %s | Details: Researched '%s' in %.2fs. Result company: '%s'",
            username, role, str(success), company_query, duration, response_data.company_name
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=response_data.what_they_do
            )

        return response_data

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Unexpected error in research endpoint: %s", e, exc_info=True)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /research | Success: False | Details: Unexpected error: %s",
            username, role, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )

@router.get("/rate-limit-status")
async def get_rate_limit_status(
    current_user: dict = Depends(get_current_user)
):
    """
    Get the rate limit status for the current user.
    """
    username = current_user.get("username", "unknown")
    now = time.time()
    
    # Check research limiter
    research_history = research_rate_limiter.requests.get(username, [])
    research_cutoff = now - research_rate_limiter.window_seconds
    active_research = [t for t in research_history if t > research_cutoff]
    
    # Check chat limiter
    chat_history = chat_rate_limiter.requests.get(username, [])
    chat_cutoff = now - chat_rate_limiter.window_seconds
    active_chat = [t for t in chat_history if t > chat_cutoff]
    
    research_resets_in = 0
    if active_research:
        research_resets_in = max(0, int(active_research[0] + research_rate_limiter.window_seconds - now))
        
    chat_resets_in = 0
    if active_chat:
        chat_resets_in = max(0, int(active_chat[0] + chat_rate_limiter.window_seconds - now))
        
    return {
        "username": username,
        "research": {
            "limit": research_rate_limiter.limit,
            "window_seconds": research_rate_limiter.window_seconds,
            "current_count": len(active_research),
            "resets_in_seconds": research_resets_in,
            "history_timestamps": active_research
        },
        "chat": {
            "limit": chat_rate_limiter.limit,
            "window_seconds": chat_rate_limiter.window_seconds,
            "current_count": len(active_chat),
            "resets_in_seconds": chat_resets_in,
            "history_timestamps": active_chat
        }
    }

@router.post("/reset-limiter")
async def reset_rate_limiter(
    current_user: dict = Depends(get_current_user)
):
    """
    Reset rate limits for the current user.
    """
    username = current_user.get("username", "unknown")
    research_rate_limiter.requests[username] = []
    chat_rate_limiter.requests[username] = []
    logger.info("Rate limit manually reset for user '%s'", username)
    return {"message": f"Rate limits successfully reset for user '{username}'."}
