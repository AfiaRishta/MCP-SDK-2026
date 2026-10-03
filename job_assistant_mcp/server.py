import os
import requests
from google import genai
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load Adzuna credentials from this folder's .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Create the MCP server — this name shows up in the host
mcp = FastMCP("job-assistant")

# One Gemini client, reused by the tools below.
# vertexai=True uses your gcloud login and billing-enabled project.
_genai_client = genai.Client(
    vertexai=True,
    project="api-adk-project-2026",
    location="us-central1",
)


@mcp.tool()
def score_match(job_title: str, job_description: str) -> dict:
    """
    Scores how well a job listing matches the user's profile.

    Args:
        job_title: the title of the role
        job_description: the full text of the listing

    Returns a fit score out of 100, the requirements the user clearly
    meets (with evidence from their profile), the gaps, and a
    recommendation. The score is grounded ONLY in the profile — it
    will not invent qualifications the user doesn't have.
    """
    profile = _load_profile()

    prompt = f"""
You are a careful, honest job-fit analyst. Score how well this candidate
fits this role, using ONLY the evidence in their profile. Do not assume
or invent any skill or experience not stated in the profile.

CANDIDATE PROFILE:
{profile}

JOB TITLE: {job_title}
JOB DESCRIPTION:
{job_description}

Respond in strict JSON with these keys:
- "fit_score": integer 0-100
- "meets": list of requirements the candidate clearly meets, each with a
  short quote or reference to the profile as evidence
- "gaps": list of requirements the candidate does not clearly meet
- "recommendation": one of "strong apply", "worth applying",
  "stretch", or "skip"
- "reasoning": 2-3 sentences explaining the score

Return ONLY the JSON, no other text.
"""

    try:
        response = _genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        # Strip any accidental markdown fences before returning
        text = response.text.strip().replace("```json", "").replace("```", "")
        return {"status": "success", "analysis": text}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============================================================
# TOOL: search for jobs via the Adzuna API
# ============================================================

# Path to the profile file, resolved relative to this script
PROFILE_PATH = os.path.join(os.path.dirname(__file__), "profile.md")


def _load_profile() -> str:
    """Helper: read the profile file from disk."""
    try:
        with open(PROFILE_PATH, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "No profile found. Create profile.md in the server folder."


@mcp.resource("profile://me")
def get_profile() -> str:
    """The user's professional profile, used for matching and drafting."""
    return _load_profile()

@mcp.tool()
def search_jobs(what: str, where: str = "Australia", max_results: int = 10) -> list:
    """
    Searches for job listings by keyword and location using Adzuna.

    Args:
        what: the role or keywords to search for, e.g. "AI engineer"
        where: the location, e.g. "Melbourne" or "Australia"
        max_results: how many listings to return (max 50)

    Returns a list of jobs, each with title, company, location,
    salary (if listed), a short description, and the apply link.
    """
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")

    if not app_id or not app_key:
        return [{"error": "Adzuna credentials not found in .env"}]

    # Adzuna scopes searches by country code in the URL path.
    # "au" = Australia.
    url = "https://api.adzuna.com/v1/api/jobs/au/search/1"

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": what,
        "where": where,
        "results_per_page": min(max_results, 50),
        "content-type": "application/json",
    }

    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code != 200:
            return [{"error": f"Adzuna returned status {response.status_code}"}]

        data = response.json()
        jobs = []

        for item in data.get("results", []):
            jobs.append({
                "title": item.get("title", "").replace("<strong>", "").replace("</strong>", ""),
                "company": item.get("company", {}).get("display_name", "Not listed"),
                "location": item.get("location", {}).get("display_name", "Not listed"),
                "salary_min": item.get("salary_min"),
                "salary_max": item.get("salary_max"),
                "description": item.get("description", "")[:300],
                "apply_url": item.get("redirect_url", ""),
                "posted": item.get("created", ""),
            })

        if not jobs:
            return [{"message": f"No jobs found for '{what}' in '{where}'. Try broader terms."}]

        return jobs

    except Exception as e:
        return [{"error": f"Failed to fetch jobs: {str(e)}"}]

@mcp.tool()
def draft_cover_letter(job_title: str, company: str, job_description: str) -> dict:
    """
    Drafts a tailored, ATS-friendly cover letter for a specific role,
    based on the user's profile. Honest — it only draws on real
    experience from the profile. The user reviews and edits before sending.
    """
    profile = _load_profile()

    prompt = f"""
Write a tailored cover letter for this candidate applying to this role.

Rules:
- Use ONLY real experience from the profile. Never invent anything.
- Keep it ATS-friendly: clear plain text, no tables or graphics,
  natural use of keywords from the job description.
- Around 250-320 words. Warm and specific, not generic or robotic.
- Reference the company and 1-2 specific things about the role.
- Sound like a real person, not AI. Vary sentence length.

CANDIDATE PROFILE:
{profile}

ROLE: {job_title} at {company}
JOB DESCRIPTION:
{job_description}

Return only the cover letter text.
"""

    try:
        response = _genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return {"status": "success", "cover_letter": response.text}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
# ============================================================
# Run the server over stdio (how hosts launch it)
# ============================================================

if __name__ == "__main__":
    mcp.run()