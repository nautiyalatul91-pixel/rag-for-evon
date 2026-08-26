import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.services.auth_service import get_current_user
from app.services.db_service import db_service
from app.services.opportunity_service import opportunity_service
from app.routes.research import ResearchResponse
from app.config import logger, audit_logger

router = APIRouter(prefix="/opportunities", tags=["Opportunities"])

class OpportunityItem(BaseModel):
    evon_service: str = Field(..., description="The name of the matching EVON capability/service")
    evidence_from_profile: str = Field(..., description="Evidence from the target company profile justifying the match")
    fit_explanation: str = Field(..., description="Short narrative explaining why they align and how EVON helps")

class OpportunityResponse(BaseModel):
    opportunities: List[OpportunityItem] = Field(..., description="List of matched high-confidence business opportunities")
    explanation: str = Field(..., description="Summary explanation of the matching process and outcomes")

class OpportunityRequest(BaseModel):
    research_id: Optional[str] = Field(None, description="SQLite ID of a previously researched company profile")
    profile: Optional[ResearchResponse] = Field(None, description="Full researched company profile passed directly")

@router.post("", response_model=OpportunityResponse)
async def identify_opportunities(
    req: OpportunityRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Identify business opportunities for EVON by matching a target company's profile
    against EVON's core capabilities in ChromaDB.
    """
    username = current_user.get("username", "unknown")
    role = current_user.get("role", "unknown")

    # 1. Resolve Profile Data
    profile_dict = None

    if req.research_id:
        logger.info("User '%s' requested opportunity matching for research_id: '%s'", username, req.research_id)
        saved_profile = db_service.get_research_profile(req.research_id)
        if not saved_profile:
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: POST /opportunities | Success: False | Details: Research ID '%s' not found in SQLite",
                username, role, req.research_id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research profile with ID '{req.research_id}' not found."
            )
        profile_dict = saved_profile
    elif req.profile:
        logger.info("User '%s' requested opportunity matching for direct profile payload: '%s'", username, req.profile.company_name)
        # Convert Pydantic response back to standard dict
        profile_dict = req.profile.dict()
    else:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /opportunities | Success: False | Details: Missing both research_id and profile in request",
            username, role
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'research_id' or 'profile' must be provided in the request body."
        )

    # 2. Run Matching Logic
    try:
        start_time = time.time()
        matching_result = opportunity_service.identify_opportunities(profile_dict)
        duration = time.time() - start_time
        
        opp_count = len(matching_result.get("opportunities", []))
        
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /opportunities | Success: True | Details: Identified %d opportunities for '%s' in %.2fs",
            username, role, opp_count, profile_dict.get("company_name"), duration
        )
        
        return OpportunityResponse(
            opportunities=matching_result.get("opportunities", []),
            explanation=matching_result.get("explanation", "")
        )
        
    except Exception as e:
        logger.error("Error in opportunities endpoint matching: %s", e, exc_info=True)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /opportunities | Success: False | Details: Matching error: %s",
            username, role, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to match opportunities: {str(e)}"
        )
