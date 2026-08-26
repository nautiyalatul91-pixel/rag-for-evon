import time
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.auth_service import get_current_user, require_admin
from app.services.db_service import db_service
from app.services.opportunity_service import opportunity_service
from app.services.draft_service import draft_service
from app.routes.research import ResearchResponse
from app.routes.opportunities import OpportunityItem
from app.config import logger, audit_logger

router = APIRouter(prefix="/drafts", tags=["Drafts"])

class DraftResponse(BaseModel):
    draft_id: str = Field(..., description="Unique persistent SQLite ID for the generated drafts record")
    company_name: str = Field(..., description="Name of the company researched")
    internal_draft: str = Field(..., description="Objective internal sales summary draft")
    outreach_draft: str = Field(..., description="Personalized client outreach message draft")
    status: str = Field(..., description="Review status of the draft (defaults to pending_review)")
    opportunities: Optional[List[OpportunityItem]] = Field(default=None, description="List of opportunities used to generate these drafts")
    profile: Optional[ResearchResponse] = Field(None, description="Researched company profile")
    rejection_reason: Optional[str] = Field(None, description="Reason for rejection, if rejected")
    recipient_emails: Optional[List[str]] = Field(default=[], description="Discovered contact email addresses")
    sent_at: Optional[str] = Field(None, description="Timestamp when draft was sent")
    sent_to: Optional[str] = Field(None, description="Recipient email address if sent")
    send_error: Optional[str] = Field(None, description="Error message if email delivery failed")
    created_at: Optional[str] = Field(None, description="Timestamp when draft was created")

class DraftRequest(BaseModel):
    research_id: Optional[str] = Field(None, description="SQLite ID of a previously researched company profile")
    profile: Optional[ResearchResponse] = Field(None, description="Full researched company profile passed directly")
    opportunities: Optional[List[OpportunityItem]] = Field(None, description="List of opportunities. If omitted, matching is run on the fly.")

class RejectRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Optional reason explaining why the draft is rejected")

@router.post("", response_model=DraftResponse)
async def generate_drafts(
    req: DraftRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Generate and persist both an internal summary draft and a personalized client outreach draft
    for a given company profile and its matched opportunities.
    """
    username = current_user.get("username", "unknown")
    role = current_user.get("role", "unknown")

    # 1. Resolve Profile
    profile_dict = None
    research_id = req.research_id

    if research_id:
        logger.info("User '%s' requested draft generation for research_id: '%s'", username, research_id)
        saved_profile = db_service.get_research_profile(research_id)
        if not saved_profile:
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: POST /drafts | Success: False | Details: Research ID '%s' not found",
                username, role, research_id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research profile with ID '{research_id}' not found."
            )
        profile_dict = saved_profile
    elif req.profile:
        logger.info("User '%s' requested draft generation for direct profile payload: '%s'", username, req.profile.company_name)
        profile_dict = req.profile.dict()
    else:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /drafts | Success: False | Details: Missing both research_id and profile",
            username, role
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'research_id' or 'profile' must be provided in the request body."
        )

    # 2. Resolve Opportunities
    opportunities_list = []
    if req.opportunities is not None:
        opportunities_list = [opp.dict() for opp in req.opportunities]
    else:
        logger.info("Opportunities not provided. Running opportunity matching on-the-fly for '%s'...", profile_dict.get("company_name"))
        match_result = opportunity_service.identify_opportunities(profile_dict)
        opportunities_list = match_result.get("opportunities", [])

    # 3. Generate Drafts
    try:
        if profile_dict.get("company_name") == "FORCE_QUOTA_EXCEEDED":
            import google.api_core.exceptions
            raise google.api_core.exceptions.ResourceExhausted("Simulated 429 quota exhaustion limit reached")
            
        start_time = time.time()
        internal_draft, outreach_draft = draft_service.generate_drafts(profile_dict, opportunities_list)
        duration = time.time() - start_time
        
        # 4. Save to Database
        draft_id = db_service.save_draft(
            research_id=research_id,
            company_name=profile_dict.get("company_name"),
            internal_draft=internal_draft,
            outreach_draft=outreach_draft,
            profile=profile_dict,
            opportunities=opportunities_list
        )
        
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /drafts | Success: True | Details: Generated drafts for '%s' with ID %s in %.2fs",
            username, role, profile_dict.get("company_name"), draft_id, duration
        )
        
        return DraftResponse(
            draft_id=draft_id,
            company_name=profile_dict.get("company_name"),
            internal_draft=internal_draft,
            outreach_draft=outreach_draft,
            status="pending_review",
            opportunities=opportunities_list,
            profile=profile_dict,
            recipient_emails=extract_emails_from_any(profile_dict),
            sent_at=None,
            sent_to=None,
            send_error=None
        )
        
    except Exception as e:
        error_msg = str(e)
        is_quota = (
            "429" in error_msg or 
            "quota" in error_msg.lower() or 
            "resource_exhausted" in error_msg.lower() or 
            "resourceexhausted" in error_msg.lower() or
            "exhausted" in error_msg.lower() or
            "limit" in error_msg.lower()
        )
        
        if is_quota:
            logger.warning("Gemini quota exceeded during draft generation for '%s'", profile_dict.get("company_name"))
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: POST /drafts | Success: False | Details: Draft generation failed due to quota limit. No draft created.",
                username, role
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "quota_exceeded",
                    "message": "Unable to generate drafts right now — the Gemini API daily quota has been reached. Please try again later once the quota resets, or switch to a different model/API key with available quota."
                }
            )
        else:
            logger.error("Error generating drafts: %s", e, exc_info=True)
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: POST /drafts | Success: False | Details: Generation error: %s",
                username, role, error_msg
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate drafts: {error_msg}"
            )

@router.get("", response_model=List[DraftResponse])
async def list_drafts(
    status_filter: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    List all generated drafts, optionally filtered by review status.
    """
    username = current_user.get("username", "unknown")
    
    logger.info("User '%s' listed drafts with filter status='%s'", username, status_filter)
    try:
        drafts = db_service.list_drafts(status=status_filter)
        return [
            DraftResponse(
                draft_id=d["id"],
                company_name=d["company_name"],
                internal_draft=d["internal_draft"],
                outreach_draft=d["outreach_draft"],
                status=d["status"],
                opportunities=d["opportunities"],
                profile=d["profile"],
                rejection_reason=d["rejection_reason"],
                recipient_emails=extract_emails_from_any(d["profile"]),
                sent_at=d.get("sent_at"),
                sent_to=d.get("sent_to"),
                send_error=d.get("send_error"),
                created_at=d["created_at"]
            )
            for d in drafts
        ]
    except Exception as e:
        logger.error("Failed to list drafts: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve drafts queue: {str(e)}"
        )

@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft_details(
    draft_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    View a specific draft's full details (company profile, opportunities, drafts, status).
    """
    username = current_user.get("username", "unknown")
    
    logger.info("User '%s' requested details for draft ID '%s'", username, draft_id)
    draft = db_service.get_draft(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft with ID '{draft_id}' not found."
        )
    return DraftResponse(
        draft_id=draft["id"],
        company_name=draft["company_name"],
        internal_draft=draft["internal_draft"],
        outreach_draft=draft["outreach_draft"],
        status=draft["status"],
        opportunities=draft["opportunities"],
        profile=draft["profile"],
        rejection_reason=draft["rejection_reason"],
        recipient_emails=extract_emails_from_any(draft["profile"]),
        sent_at=draft.get("sent_at"),
        sent_to=draft.get("sent_to"),
        send_error=draft.get("send_error"),
        created_at=draft["created_at"]
    )

@router.post("/{draft_id}/approve", response_model=Dict[str, Any])
async def approve_draft(
    draft_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Marks a draft as approved (admin role required).
    """
    username = current_user.get("username", "unknown")
    role = current_user.get("role", "unknown")
    
    draft = db_service.get_draft(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft with ID '{draft_id}' not found."
        )
        
    success = db_service.update_draft_status(draft_id, "approved")
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update draft status."
        )
        
    audit_logger.info(
        "User: %s | Role: %s | Endpoint: POST /drafts/%s/approve | Success: True | Details: Approved draft for '%s'",
        username, role, draft_id, draft.get("company_name")
    )
    
    return {
        "success": True,
        "message": f"Draft for '{draft.get('company_name')}' approved successfully.",
        "draft_id": draft_id,
        "status": "approved"
    }

@router.post("/{draft_id}/reject", response_model=Dict[str, Any])
async def reject_draft(
    draft_id: str,
    req: RejectRequest,
    current_user: dict = Depends(require_admin)
):
    """
    Marks a draft as rejected, with an optional reason (admin role required).
    """
    username = current_user.get("username", "unknown")
    role = current_user.get("role", "unknown")
    reason = req.reason.strip() if req.reason else None
    
    draft = db_service.get_draft(draft_id)
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft with ID '{draft_id}' not found."
        )
        
    success = db_service.update_draft_status(draft_id, "rejected", reason)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update draft status."
        )
        
    audit_logger.info(
        "User: %s | Role: %s | Endpoint: POST /drafts/%s/reject | Success: True | Details: Rejected draft for '%s'. Reason: %s",
        username, role, draft_id, draft.get("company_name"), reason or "No reason provided"
    )
    
    return {
        "success": True,
        "message": f"Draft for '{draft.get('company_name')}' rejected successfully.",
        "draft_id": draft_id,
        "status": "rejected",
        "reason": reason
    }

class SendOutreachRequest(BaseModel):
    recipient_email: str = Field(..., description="Email address of the recipient")
    subject: str = Field(..., description="Subject of the email outreach message")
    body: str = Field(..., description="Message body of the email outreach")

def extract_emails_from_any(obj: Any) -> List[str]:
    """Extract all email addresses from any dict, list or nested string structure."""
    if not obj:
        return []
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    found = set()
    
    def walk(val):
        if isinstance(val, str):
            for match in re.findall(email_pattern, val):
                clean = match.strip('.,()[]{}<> ')
                if clean:
                    found.add(clean)
        elif isinstance(val, list):
            for item in val:
                walk(item)
        elif isinstance(val, dict):
            for k, v in val.items():
                walk(k)
                walk(v)
                
    walk(obj)
    return sorted(list(found))

@router.post("/{draft_id}/send", response_model=Dict[str, Any])
async def send_draft_outreach(
    draft_id: str,
    req: SendOutreachRequest,
    current_user: dict = Depends(require_admin)
):
    """
    Delivers the approved outreach message via the configured email provider.
    Marks status as 'sent' and logs audit events.
    """
    username = current_user.get("username", "unknown")
    role = current_user.get("role", "unknown")
    
    # 1. Fetch Draft
    draft = db_service.get_draft(draft_id)
    if not draft:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /drafts/%s/send | Success: False | Details: Draft ID '%s' not found",
            username, role, draft_id, draft_id
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft with ID '{draft_id}' not found."
        )
        
    # 2. Check status is 'approved'
    if draft["status"] != "approved":
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /drafts/%s/send | Success: False | Details: Cannot send draft in status '%s'",
            username, role, draft_id, draft["status"]
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only approved drafts can be sent. Current status is '{draft['status']}'."
        )
        
    # 3. Call Outreach Service to deliver email
    from app.services.outreach_service import outreach_service
    sent_at = datetime.utcnow().isoformat() + "Z"
    
    try:
        result = outreach_service.send_email(
            recipient=req.recipient_email,
            subject=req.subject,
            body=req.body
        )
        
        # 4. Save to Database
        db_status = "sent_dryrun" if result.get("dry_run") else "sent"
        db_service.update_sent_status(
            draft_id=draft_id,
            status=db_status,
            sent_to=req.recipient_email,
            sent_at=sent_at,
            send_error=None
        )
        
        # 5. Log audit
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /drafts/%s/send | Success: True | Details: Sent outreach to '%s' via %s (Dry-run: %s)",
            username, role, draft_id, req.recipient_email, result.get("provider", "unknown"), result.get("dry_run", False)
        )
        
        return {
            "success": True,
            "message": "Outreach email sent successfully." if not result.get("dry_run") else "Outreach email simulated successfully (Dry-run).",
            "draft_id": draft_id,
            "status": db_status,
            "sent_to": req.recipient_email,
            "sent_at": sent_at,
            "provider": result.get("provider", "unknown"),
            "dry_run": result.get("dry_run", False)
        }
    except Exception as e:
        error_msg = str(e)
        # Save failure details to db
        db_service.update_sent_status(
            draft_id=draft_id,
            status="approved", # Keep as approved so they can retry
            sent_to=req.recipient_email,
            sent_at=None,
            send_error=error_msg
        )
        
        # Log failure
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /drafts/%s/send | Success: False | Details: Send failed: %s",
            username, role, draft_id, error_msg
        )
        
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Email transmission failed: {error_msg}"
        )
