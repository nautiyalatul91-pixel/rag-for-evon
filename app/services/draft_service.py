import os
from typing import Dict, Any, List, Tuple
import google.generativeai as genai
from app.config import GEMINI_API_KEY, GEMINI_MODEL, logger

class DraftService:
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model_name = f"models/{GEMINI_MODEL}" if not GEMINI_MODEL.startswith("models/") else GEMINI_MODEL
        
        # Configure Gemini
        if self.api_key and self.api_key != "your_gemini_api_key_here" and "YOUR_REAL_API_KEY_HERE" not in self.api_key:
            genai.configure(api_key=self.api_key)
            self.client_configured = True
        else:
            self.client_configured = False

    def generate_drafts(self, company_profile: Dict[str, Any], opportunities: List[Dict[str, Any]]) -> Tuple[str, str]:
        """
        Generates two distinct drafts for a set of opportunities:
        1. An honest, objective internal summary.
        2. A highly personalized, low-pressure client outreach message.
        
        Returns: (internal_draft, outreach_draft)
        """
        company_name = company_profile.get("company_name", "Unknown Company")
        logger.info("Generating drafts for company: '%s'...", company_name)
        
        # 1. Refuse generation if opportunities list is empty
        if not opportunities:
            logger.info("No opportunities identified for '%s'. Skipping LLM generation and returning refusal drafts.", company_name)
            refuse_internal = f"""# Target Company Overview: {company_name}
We performed research on {company_name} (Industry: {company_profile.get("industry", "N/A")}, Size/Stage: {company_profile.get("size_stage", "N/A")}).

# Likely Technology & Operational Needs
No technology or operational needs matching EVON's service portfolio were identified in the target company's profile.

# Core EVON Value Fit
No genuine value fit has been identified between EVON's capabilities and {company_name}'s current operations.

# Actionable Sales Recommendation
We recommend not pursuing active sales outreach for {company_name} at this time, as there are no matching service opportunities. All identified business needs are outside of EVON's custom engineering focus area."""
            
            refuse_outreach = "No genuine business opportunity was identified for this company — recommend not pursuing outreach"
            return (refuse_internal, refuse_outreach)
            
        if not self.client_configured:
            logger.warning("Gemini client not configured. Returning fallback placeholder drafts.")
            return (
                f"# Internal Summary: {company_name}\n\nClient not configured.",
                f"Subject: Connecting with {company_name}\n\nHi team, let's connect."
            )
            
        # Format opportunities list for context
        opp_items = []
        for idx, o in enumerate(opportunities):
            opp_items.append(
                f"Opportunity [{idx+1}]:\n"
                f"- EVON Service Match: {o.get('evon_service')}\n"
                f"- Evidence from Profile: {o.get('evidence_from_profile')}\n"
                f"- Potential Fit: {o.get('fit_explanation')}"
            )
        opportunities_str = "\n\n".join(opp_items) if opp_items else "No explicit opportunities identified."

        # Prompt 1: Internal Analysis Summary
        internal_prompt = f"""
You are an expert internal business intelligence analyst at EVON Technologies.
Write an honest, objective, and matter-of-fact internal summary about the target company and the identified sales opportunities.
This document is for our internal team ONLY. Avoid fluff, sales jargon, or marketing copy. Be direct.

CRITICAL GROUNDING INSTRUCTION: Do NOT invent, assume, or extrapolate any facts, locations, tech stacks, or customer reviews not explicitly provided in the TARGET COMPANY PROFILE or IDENTIFIED OPPORTUNITIES context. Do NOT use any external or pre-trained knowledge about this company. If a detail is not in the provided context, treat it as entirely unavailable.

TARGET COMPANY PROFILE:
Name: {company_name}
Industry: {company_profile.get("industry", "not publicly available")}
What they do: {company_profile.get("what_they_do", "not publicly available")}
Tech Stack: {company_profile.get("tech_stack", [])}
Business Needs/Pain Points: {company_profile.get("business_needs", [])}
Recent News: {company_profile.get("recent_news", [])}

IDENTIFIED OPPORTUNITIES:
{opportunities_str}

YOUR TASK:
Write an internal summary with the following sections (use clear Markdown headings):
1. **Target Company Overview**: Objective summary of their business model, current scale/stage, and tech stack.
2. **Likely Technology & Operational Needs**: Critical analysis of what they are struggling with, potential scalability bottlenecks, or operational objectives.
3. **Core EVON Value Fit**: Specifically which EVON service(s) or case study capabilities fit their needs, and the reasoning why. Be realistic.
4. **Actionable Sales Recommendation**: A tactical recommendation for our BD/sales team on how to approach them, what angle to focus on, and potential objections to anticipate (e.g. internal development preference, tight budget).
"""

        # Prompt 2: Outreach Draft
        outreach_prompt = f"""
You are an expert business development representative at EVON Technologies.
Write a highly personalized, professional, and warm business outreach message to the target company.
The goal is to initiate a relationship. Avoid generic templates, buzzwords, or sounding like spam. Keep the message concise and easy to read.

CRITICAL GROUNDING INSTRUCTION: Do NOT invent, assume, or extrapolate any facts, locations, tech stacks, or customer reviews not explicitly provided in the TARGET COMPANY PROFILE or IDENTIFIED OPPORTUNITIES context. Do NOT use any external or pre-trained knowledge about this company. If a detail is not in the provided context, treat it as entirely unavailable.

TARGET COMPANY PROFILE:
Name: {company_name}
Industry: {company_profile.get("industry", "not publicly available")}
What they do: {company_profile.get("what_they_do", "not publicly available")}
Tech Stack: {company_profile.get("tech_stack", [])}
Business Needs/Pain Points: {company_profile.get("business_needs", [])}
Recent News: {company_profile.get("recent_news", [])}

IDENTIFIED OPPORTUNITIES:
{opportunities_str}

YOUR TASK:
Write a personalized outreach email message. Ensure you:
1. **Establish Context**: Reference something specific and real about their company (such as a recent news event, their specific product ecosystem, or their tech stack) to show we actually did our research.
2. **Introduce EVON Relevance**: Briefly and naturally introduce what EVON does that is relevant to their situation (e.g. cloud migrations, AI recommendations, custom systems, or QA).
3. **Highlight concrete benefit**: Articulate the specific benefit/value proposition we can deliver to solve one of their actual pain points.
4. **Soft Call-to-Action**: Propose a low-pressure next step (e.g. a brief 10-minute introduction call, or offering to send over a relevant case study).
5. **No-Pressure Exit**: Include a polite line inviting future contact even if they are not interested in doing business right now (e.g., "Even if you don't have active needs right now, I'd love to connect on LinkedIn to keep in touch for the future").

Note: Keep the tone highly consultative and helpful. Do not sound pushy or aggressive.
"""

        model = genai.GenerativeModel(self.model_name)
        
        logger.info("Calling Gemini API for internal summary on '%s'...", company_name)
        internal_resp = model.generate_content(internal_prompt)
        internal_draft = internal_resp.text.strip()

        logger.info("Calling Gemini API for outreach draft on '%s'...", company_name)
        outreach_resp = model.generate_content(outreach_prompt)
        outreach_draft = outreach_resp.text.strip()

        logger.info("Successfully generated internal and outreach drafts for '%s'", company_name)
        return (internal_draft, outreach_draft)

draft_service = DraftService()
