import os
import json
from typing import Dict, Any, List
import google.generativeai as genai
from app.config import GEMINI_API_KEY, TAVILY_API_KEY, logger

class ResearchService:
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model_name = "models/gemini-3.6-flash"
        
        # Configure Gemini
        if self.api_key and self.api_key != "your_gemini_api_key_here" and "YOUR_REAL_API_KEY_HERE" not in self.api_key:
            genai.configure(api_key=self.api_key)
            self.client_configured = True
        else:
            self.client_configured = False
            
        # Configure Tavily
        self.tavily_key = TAVILY_API_KEY
        if self.tavily_key and self.tavily_key != "your_tavily_api_key_here":
            try:
                from tavily import TavilyClient
                self.tavily_client = TavilyClient(api_key=self.tavily_key)
                self.tavily_configured = True
                logger.info("Tavily Search API client successfully configured.")
            except Exception as e:
                logger.error("Failed to initialize TavilyClient: %s", e)
                self.tavily_client = None
                self.tavily_configured = False
        else:
            self.tavily_client = None
            self.tavily_configured = False

    def research_company(self, company_input: str) -> Dict[str, Any]:
        """
        Research a company using Tavily Search API and extract structured info with Gemini.
        Returns a structured JSON company profile dictionary.
        """
        company_clean = company_input.strip().lower()
        # If client is not configured, or MOCK_RESEARCH env is true, or query starts with mock_
        if not self.client_configured or not self.tavily_configured or os.environ.get("MOCK_RESEARCH", "false").lower() == "true" or company_clean.startswith("mock_"):
            logger.info("Mock Research Mode active (or query starts with mock_). Generating offline mock company profile.")
            mock_input = company_input[5:] if company_clean.startswith("mock_") else company_input
            return self._generate_mock_profile(mock_input)

        try:
            logger.info("Calling Tavily Search API for research: '%s'...", company_input)
            search_query = company_input.strip()
            search_response = self.tavily_client.search(query=search_query, search_depth="advanced", max_results=5)
            
            results = search_response.get("results", [])
            if not results:
                logger.warning("No search results returned from Tavily for query: '%s'", company_input)
                # Fall back to obscure company behavior
                return self._generate_mock_profile(f"mock_xyznonexistent_{company_input}")
            
            context_items = []
            for idx, r in enumerate(results):
                context_items.append(
                    f"--- Source [{idx+1}]: {r.get('url')} ---\n"
                    f"Title: {r.get('title')}\n"
                    f"Content: {r.get('content')}"
                )
            context_str = "\n\n".join(context_items)

            logger.info("Calling Gemini API for profile extraction on '%s'...", company_input)
            prompt = f"""
You are an expert business development analyst.
We have performed a live web search for "{company_input}".
Based ONLY on the search context provided below, extract and structure a company profile.

SEARCH CONTEXT:
\"\"\"
{context_str}
\"\"\"

Return a structured JSON object. Use the following JSON schema:
{{
  "company_name": "Name of the company",
  "industry": "Industry description or 'not publicly available'",
  "what_they_do": "Detailed summary of what they do or 'not publicly available'",
  "tech_stack": ["List of discoverable tech stack tools, frameworks, and tools. Leave empty if none are discoverable."],
  "size_stage": "Company size/stage or 'not publicly available'",
  "recent_news": ["List of recent news stories, articles, press releases, or 'not publicly available'"],
  "business_needs": ["List of apparent business needs, potential pain points, or BD opportunities that can reasonably be inferred from public information"],
  "citations": {{
    "company_name": "Specific citation source URL or 'reasonable inference' or 'not publicly available'",
    "industry": "Specific citation source URL or 'reasonable inference' or 'not publicly available'",
    "what_they_do": "Specific citation source URL or 'reasonable inference' or 'not publicly available'",
    "tech_stack": "Specific citation source URL or 'reasonable inference' or 'not publicly available'",
    "size_stage": "Specific citation source URL or 'reasonable inference' or 'not publicly available'",
    "recent_news": "Specific citation source URL or 'reasonable inference' or 'not publicly available'",
    "business_needs": "Specific citation source URL or 'reasonable inference' or 'not publicly available'"
  }}
}}

INSTRUCTIONS:
1. Ground your answers ONLY in the provided search context. Do not invent details.
2. If a field's information is not present in the context, set its value to "not publicly available" and its citation to "not publicly available".
3. For "business_needs", make reasonable, professional BD inferences grounded in the context, and note the citation as "reasonable inference".
4. For citations of found fields, specify the Source URL (from the Source [<number>] headers) where the information was located.
5. Ensure the JSON is completely valid and parseable.
"""

            model = genai.GenerativeModel(model_name=self.model_name)
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

            parsed_profile = json.loads(result_json)
            logger.info("Successfully researched and generated profile for: '%s'", company_input)
            return parsed_profile

        except Exception as e:
            logger.error("Failed to perform company research for '%s': %s", company_input, e, exc_info=True)
            return {
                "company_name": company_input,
                "industry": "error during research",
                "what_they_do": f"An error occurred while calling the research service: {str(e)}",
                "tech_stack": [],
                "size_stage": "not publicly available",
                "recent_news": [],
                "business_needs": [],
                "citations": {
                    "company_name": "system fallback",
                    "industry": "system fallback",
                    "what_they_do": "system fallback",
                    "tech_stack": "system fallback",
                    "size_stage": "system fallback",
                    "recent_news": "system fallback",
                    "business_needs": "system fallback"
                }
            }

    def _generate_mock_profile(self, company_input: str) -> Dict[str, Any]:
        """Generate a simulated profile when live API key is absent or mock research mode is enabled."""
        company_clean = company_input.strip().lower()
        
        # Test Obscure Company fallback case
        if "xyznonexistent" in company_clean or "obscure" in company_clean:
            return {
                "company_name": company_input,
                "industry": "not publicly available",
                "what_they_do": "not publicly available",
                "tech_stack": [],
                "size_stage": "not publicly available",
                "recent_news": [],
                "business_needs": [],
                "citations": {
                    "company_name": "search results",
                    "industry": "not publicly available",
                    "what_they_do": "not publicly available",
                    "tech_stack": "not publicly available",
                    "size_stage": "not publicly available",
                    "recent_news": "not publicly available",
                    "business_needs": "not publicly available"
                }
            }

        # Real well-known mock profiles for testing
        if "google" in company_clean:
            name = "Google LLC"
            industry = "Technology & Internet Services"
            what_they_do = "Specializes in search engine technology, online advertising, cloud computing, computer software, quantum computing, e-commerce, consumer electronics, and artificial intelligence."
            tech_stack = ["Python", "C++", "Java", "Go", "Borg", "Spanner", "TensorFlow"]
            size_stage = "Conglomerate / Public"
            recent_news = ["Announced new Gemini updates at developer conferences", "Investing in green energy grid upgrades for data centers"]
            business_needs = ["Optimize cloud subscription retention against AWS/Azure", "Mitigate antitrust regulatory pressure in search and ad-tech markets"]
        elif "microsoft" in company_clean:
            name = "Microsoft Corporation"
            industry = "Software, Consumer Electronics, and Cloud Services"
            what_they_do = "Develops, manufactures, licenses, supports, and sells computer software, consumer electronics, personal computers, and services."
            tech_stack = ["C#", "TypeScript", "C++", "Azure Cloud", "SQL Server", "GitHub Copilot"]
            size_stage = "Conglomerate / Public"
            recent_news = ["Integrating Copilot into Microsoft 365 services globally", "Expanding datacenter regions in Europe and Asia"]
            business_needs = ["Accelerate enterprise adoption of Azure OpenAI services", "Enhance security posture following cloud tenant breaches"]
        else:
            # General fallback mock
            name = company_input
            industry = "Technology"
            what_they_do = f"A general company description for {company_input} representing what the public web search would discover."
            tech_stack = ["React", "Node.js", "AWS"]
            size_stage = "Mid-market"
            recent_news = [f"{company_input} launches new product suite on the market"]
            business_needs = ["Expand marketing outreach", "Upgrade legacy software infrastructure"]

        return {
            "company_name": name,
            "industry": industry,
            "what_they_do": what_they_do,
            "tech_stack": tech_stack,
            "size_stage": size_stage,
            "recent_news": recent_news,
            "business_needs": business_needs,
            "citations": {
                "company_name": "Google Search - official company portal",
                "industry": "Google Search - corporate listings",
                "what_they_do": "Google Search - corporate summary page",
                "tech_stack": "Google Search - developer job postings",
                "size_stage": "Google Search - financial reporting reports",
                "recent_news": "Google Search - major press announcements",
                "business_needs": "reasonable inference based on market dynamics"
            }
        }

research_service = ResearchService()
