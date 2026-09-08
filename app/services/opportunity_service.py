import os
import json
from typing import Dict, Any, List
import google.generativeai as genai
from app.config import GEMINI_API_KEY, GEMINI_MODEL, logger
from app.services.embedding_service import embedding_service
from app.services.db_service import db_service

class OpportunityService:
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model_name = f"models/{GEMINI_MODEL}" if not GEMINI_MODEL.startswith("models/") else GEMINI_MODEL
        
        # Configure Gemini
        if self.api_key and self.api_key != "your_gemini_api_key_here" and "YOUR_REAL_API_KEY_HERE" not in self.api_key:
            genai.configure(api_key=self.api_key)
            self.client_configured = True
        else:
            self.client_configured = False

    def identify_opportunities(self, company_profile: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cross-references a target company profile against the evon_capabilities collection.
        Queries ChromaDB directly to fetch the top matches and lets Gemini evaluate the fit.
        """
        company_name = company_profile.get("company_name", "Unknown Company")
        business_needs = company_profile.get("business_needs", [])
        
        logger.info("Identifying opportunities for company: '%s'...", company_name)
        
        # 1. Gather Needs for Retrieval
        search_terms = []
        for need in business_needs:
            if need and need.strip() != "not publicly available":
                search_terms.append(need.strip())
                
        if not search_terms:
            fallback = company_profile.get("what_they_do", "")
            if fallback and fallback.strip() != "not publicly available":
                search_terms.append(fallback.strip())
        
        if not search_terms:
            logger.info("No search terms available in the company profile for '%s'.", company_name)
            return {
                "opportunities": [],
                "explanation": f"The company profile for {company_name} is empty, making it impossible to establish any business opportunities."
            }

        # 2. Retrieve Top EVON Capability Chunks from ChromaDB directly (no L2 strict threshold filtering)
        retrieved_chunks = []
        seen_chunks = set()
        
        for term in search_terms:
            try:
                embeddings = embedding_service.get_embeddings([term])
                query_embedding = embeddings[0]
                
                target_collection = db_service.collections.get("evon_capabilities")
                if not target_collection:
                    logger.error("evon_capabilities collection not found in db_service.")
                    continue
                    
                results = target_collection.query(
                    query_embeddings=[query_embedding],
                    n_results=3,
                    include=["documents", "metadatas", "distances"]
                )
                
                if results and results.get("documents") and len(results["documents"][0]) > 0:
                    documents = results["documents"][0]
                    metadatas = results["metadatas"][0]
                    distances = results["distances"][0]
                    for idx in range(len(documents)):
                        text = documents[idx]
                        meta = metadatas[idx]
                        dist = distances[idx]
                        
                        chunk_key = (meta.get("source_filename", "unknown"), meta.get("page_number", 1), text[:100])
                        if chunk_key not in seen_chunks:
                            seen_chunks.add(chunk_key)
                            logger.info(
                                "Retrieved candidate chunk from '%s' (page %d) | Distance Score: %.4f for term: '%s'",
                                meta.get("source_filename", "unknown"), meta.get("page_number", 1), dist, term[:40]
                            )
                            retrieved_chunks.append({
                                "text": text,
                                "filename": meta.get("source_filename", "unknown"),
                                "page_number": meta.get("page_number", 1)
                            })
            except Exception as e:
                logger.error("Failed to retrieve capabilities for term '%s': %s", term, e)

        if not retrieved_chunks:
            logger.info("No capabilities retrieved from vector database for '%s'.", company_name)
            return {
                "opportunities": [],
                "explanation": f"No matching EVON capabilities were found in the database for the needs of {company_name}."
            }

        # 3. Format context for the LLM
        context_items = []
        for idx, chunk in enumerate(retrieved_chunks):
            context_items.append(
                f"--- EVON Capability [{idx+1}] (File: {chunk['filename']}, Page: {chunk['page_number']}) ---\n"
                f"{chunk['text']}"
            )
        capabilities_context = "\n\n".join(context_items)

        # 4. Invoke Gemini for Reasoning & Matching
        if not self.client_configured:
            logger.warning("Gemini client not configured. Returning empty opportunities.")
            return {
                "opportunities": [],
                "explanation": "Gemini API client is not configured on the server, so reasoning about opportunities could not be performed."
            }

        prompt = f"""
You are an expert sales engineer and business development specialist at EVON Technologies.
Your task is to analyze a target company's profile and cross-reference it with retrieved chunks of EVON Technologies' service offerings and case studies.
Identify genuine, realistic business opportunities where EVON's capabilities can solve the target company's pain points/needs.

TARGET COMPANY PROFILE:
Name: {company_name}
Industry: {company_profile.get("industry", "not publicly available")}
What they do: {company_profile.get("what_they_do", "not publicly available")}
Tech Stack: {company_profile.get("tech_stack", [])}
Business Needs/Pain Points: {business_needs}
Recent News: {company_profile.get("recent_news", [])}

RETRIEVED EVON CAPABILITIES & CASE STUDIES:
{capabilities_context}

INSTRUCTIONS:
1. For each retrieved EVON capability, determine if there is a GENUINE business opportunity for EVON to help this target company.
2. To be a genuine opportunity:
     - The target company must have a clear need, pain point, tech transition, or goal (listed in their profile) that the EVON capability directly solves.
     - You must provide concrete evidence from the target company's profile (citing the need/recent news/tech stack) and from EVON's capabilities (citing the service/case study).
3. Avoid forcing matches that are completely unrelated to technical software or workflow automation capability (e.g., EVON cannot help with cooking recipes, food taste, or raw physical delivery vehicle routing). However, you should identify custom software, process automation, communication integrations, or digital workflows (such as custom kitchen display dashboards to verify order packing accuracy, or automated WhatsApp messaging for handling delivery complaints) that can meaningfully solve or alleviate the business's operational and customer service pain points.
4. If no genuine opportunities are found, return an empty list.
5. Recognize that software solutions routinely address operational, customer service, and human-error pain points through automated workflow verification systems, logging tools, API communication integrations (e.g., WhatsApp Business API, SMS, automated order-tracking notifications), and custom dashboards or kitchen display system (KDS) portals. If an operational or communication pain point in the company profile can be meaningfully improved through custom software development or system integration, this constitutes a valid opportunity for EVON's Custom Software Development or IT Consulting & Digital Transformation.
6. Do not automatically dismiss opportunities for local retail, restaurants, gyms, or small businesses on the basis of company size or scale alone, and do not refuse to match them on the assumption that they should use off-the-shelf software. Instead, if a pain point can be addressed by a custom tool or API integration (such as an order verification/KDS dashboard or automated WhatsApp messaging), you must identify it as a valid Custom Software Development or IT Consulting & Digital Transformation opportunity, leaving the commercial/financial viability decision to the human reviewer.

Return a structured JSON object using this schema:
{{
  "opportunities": [
    {{
      "evon_service": "Name of the specific EVON service/capability or Case Study that is a match",
      "evidence_from_profile": "The literal, word-for-word text of the specific business need, news item, or profile fact as listed in the TARGET COMPANY PROFILE that justifies this opportunity. Do NOT paraphrase, edit, or add external details.",
      "fit_explanation": "A detailed explanation of why this EVON service fits the company's needs and how EVON can help"
    }}
  ],
  "explanation": "A summary explanation of the matching results (especially why opportunities were found, or why no opportunities matched)."
}}

Ensure the output is valid JSON and matches the schema exactly. Do not wrap it in markdown block characters.
"""

        try:
            logger.info("Calling Gemini API for opportunity reasoning on '%s'...", company_name)
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            
            result_json = response.text.strip()
            # Clean possible markdown wrap
            if result_json.startswith("```"):
                lines = result_json.split("\n")
                if lines[0].startswith("```json"):
                    lines = lines[1:-1]
                elif lines[0].startswith("```"):
                    lines = lines[1:-1]
                result_json = "\n".join(lines).strip()

            parsed_data = json.loads(result_json)
            opps = parsed_data.get("opportunities", [])
            logger.info("Successfully identified %d opportunities for '%s'", len(opps), company_name)
            return parsed_data

        except Exception as e:
            logger.error("Failed to perform opportunity identification for '%s': %s", company_name, e, exc_info=True)
            return {
                "opportunities": [],
                "explanation": f"An error occurred while calling the opportunity matching service: {str(e)}"
            }

opportunity_service = OpportunityService()
