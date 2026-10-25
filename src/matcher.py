"""
Job matching engine using Google Gemini API with Anthropic Claude fallback.
Compares job descriptions against the user's resume and assigns match tiers.
"""

import json
import re
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Result of matching a job against the resume."""
    tier: int       # 1 = close match, 2 = related, 3 = low/no match
    score: float    # 0.0 to 1.0 confidence
    reason: str     # 2-3 sentence actionable explanation


SYSTEM_PROMPT = """You are an expert tech recruiter evaluating job-resume fit for a senior Product Manager candidate.
You will receive the candidate's resume and a job posting. Analyze the match and respond with ONLY valid JSON.

Response format (no other text):
{"tier": 1, "score": 0.82, "reason": "Senior PM role focused on platform strategy — your 9 years leading cross-functional product launches transfer directly. Your API monetization and merchant-facing experience is a strong differentiator. Emphasize: scaling products from 0→1 and data-driven roadmap ownership."}

CANDIDATE CONTEXT:
- Senior Product Manager with 9+ years of experience
- The candidate has hand-picked these target companies and is actively job searching
- Open to ANY customer-facing PM role — domain match is a bonus, not a requirement
- What matters: seniority fit, transferable PM skills, product leadership responsibilities
- Target compensation: $300k+ total comp (base + equity + bonus)
- Salary is ONE signal among many — not a hard cutoff
- Unknown salary: neutral, do not penalize
- Disclosed salary below $200k: negative signal, likely Tier 2 unless exceptional fit
- Disclosed salary $300k+: positive signal, strengthens Tier 1 case

TIER DEFINITIONS (be selective with Tier 1):

Tier 1 (Apply Now) — Score >= 0.75. Reserve for the BEST matches only (~25-30% of PM roles). ALL of these must be true:
  • Title is clearly a Product Manager role (PM, Senior PM, Staff PM, Principal PM, Group PM, Technical PM, Growth PM, Platform PM)
  • Seniority aligns with 7-12 years experience (Senior or Staff level is ideal; not entry-level, not VP/Director requiring 15+ years)
  • Role involves product leadership: owning roadmaps, shipping features, customer-facing strategy, cross-functional leadership
  • Location is compatible (US-based, remote, or CA/WA)
  If ANY of these fail, it's Tier 2 at most.

Tier 2 (Worth Tracking) — Score 0.40-0.74. Solid roles worth monitoring but with a gap:
  • PM title but seniority is a stretch (too junior <5y, or senior-leaning 12-15y)
  • Adjacent roles: Technical Program Manager, Product Operations, Product Analyst, Product Marketing Manager
  • PM role but heavily specialized in a niche requiring domain credentials the candidate lacks
  • Interesting roles at target companies that aren't pure PM but could be a lateral move

Tier 3 (Skip) — Score < 0.40. Clearly not relevant:
  • Not a product role (engineering, design, sales, legal, HR, finance)
  • Requires credentials the candidate doesn't have (MD, JD, PhD, 20+ years niche expertise)
  • Intern or new-grad level roles

REASON FORMAT (2-3 sentences, actionable for the candidate):
Sentence 1: Why this role fits (or doesn't) — connect the role's focus to the candidate's background.
Sentence 2: What specific strength from their experience applies to this role.
Sentence 3: What they should emphasize in their application (for Tier 1/2) or why it's not a fit (for Tier 3).

KEY EVALUATION ORDER:
1. Is this a Product Manager role? (title check)
2. Does the seniority level fit 9 years of experience?
3. Does it involve product leadership responsibilities?
4. Location compatibility
5. Domain overlap (bonus only, never a dealbreaker for Tier 1 vs 2)
6. Compensation fit (if disclosed — bonus signal, not a hard requirement)

Do NOT inflate tiers. Be honest and selective. Tier 1 should feel like "she should definitely apply to this one"."""


USER_PROMPT_TEMPLATE = """CANDIDATE RESUME:
{resume}

---

JOB POSTING:
Company: {company}
Title: {title}
Location: {location}
Salary: {salary}
Description:
{description}

---

Evaluate the fit. Return ONLY JSON: {{"tier": <1|2|3>, "score": <0.0-1.0>, "reason": "<2-3 sentence actionable explanation>"}}"""


class JobMatcher:
    """Matches jobs against a resume using Gemini (primary) with Anthropic fallback."""

    def __init__(self, api_key: str, resume_text: str, model: str = "gemini-2.5-flash-lite",
                 anthropic_api_key: str = None):
        self.resume = resume_text
        self.model = model
        self.max_retries = 3
        self.anthropic_api_key = anthropic_api_key

        # Primary: Gemini
        self.gemini_client = None
        try:
            from google import genai
            self.gemini_client = genai.Client(api_key=api_key)
            logger.info("Gemini client initialized (primary)")
        except Exception as e:
            logger.warning(f"Could not init Gemini client: {e}")

        # Fallback: Anthropic
        self.anthropic_client = None
        if anthropic_api_key:
            try:
                import anthropic
                self.anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
                logger.info("Anthropic client initialized (fallback)")
            except Exception as e:
                logger.warning(f"Could not init Anthropic client: {e}")

    def match(self, company: str, title: str, location: str,
              description: str, salary: str = "") -> MatchResult:
        """Match a job posting against the resume.

        Tries Anthropic first (faster), falls back to Gemini if Anthropic fails.
        """
        user_prompt = USER_PROMPT_TEMPLATE.format(
            resume=self.resume,
            company=company,
            title=title,
            location=location,
            salary=salary or "Not disclosed",
            description=description[:2500]
        )

        # Try Anthropic first (faster, no harsh rate limits)
        if self.anthropic_client:
            result = self._try_anthropic(user_prompt)
            if result:
                return result
            logger.warning("Anthropic failed — falling back to Gemini")

        # Fallback to Gemini
        if self.gemini_client:
            result = self._try_gemini(user_prompt)
            if result:
                return result
            logger.warning("Gemini fallback also failed")

        return self._fallback_result(title)

    def _try_gemini(self, user_prompt: str) -> Optional[MatchResult]:
        """Try matching with Gemini API."""
        from google.genai import types

        for attempt in range(self.max_retries):
            try:
                # Free tier: 10 RPM — pace requests to stay under limit
                time.sleep(7)

                response = self.gemini_client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        max_output_tokens=500,
                        temperature=0.3,
                    )
                )

                response_text = response.text.strip()
                return self._parse_response(response_text)

            except Exception as e:
                error_str = str(e).lower()
                if "resource_exhausted" in error_str or "429" in error_str or "rate" in error_str:
                    wait_time = 15 * (attempt + 1)
                    logger.warning(f"Gemini rate limited (attempt {attempt + 1}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                elif attempt < self.max_retries - 1:
                    logger.error(f"Gemini API error (attempt {attempt + 1}): {e}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"Gemini API failed after {self.max_retries} attempts: {e}")
                    return None  # Signal to try fallback

        return None  # All retries exhausted

    def _try_anthropic(self, user_prompt: str) -> Optional[MatchResult]:
        """Try matching with Anthropic Claude API as fallback."""
        for attempt in range(self.max_retries):
            try:
                response = self.anthropic_client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=500,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=0.3,
                )

                response_text = response.content[0].text.strip()
                return self._parse_response(response_text)

            except Exception as e:
                error_str = str(e).lower()
                if "rate" in error_str or "429" in error_str:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"Anthropic rate limited, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                elif attempt < self.max_retries - 1:
                    logger.error(f"Anthropic API error (attempt {attempt + 1}): {e}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"Anthropic API failed after {self.max_retries} attempts: {e}")
                    return None

        return None

    def _parse_response(self, response_text: str) -> MatchResult:
        """Parse LLM JSON response into a MatchResult."""
        try:
            # Strip markdown code blocks that Gemini sometimes wraps around JSON
            cleaned = re.sub(r'^```(?:json)?\s*', '', response_text, flags=re.MULTILINE)
            cleaned = re.sub(r'```\s*$', '', cleaned, flags=re.MULTILINE).strip()

            # Extract JSON object
            start = cleaned.index("{")
            end = cleaned.rindex("}") + 1
            json_str = cleaned[start:end]

            data = json.loads(json_str)

            tier = int(data.get("tier", 3))
            score = float(data.get("score", 0.0))
            reason = str(data.get("reason", "No reason provided"))

            # Validate tier
            if tier not in (1, 2, 3):
                tier = 3

            # Clamp score
            score = max(0.0, min(1.0, score))

            return MatchResult(tier=tier, score=score, reason=reason)

        except (json.JSONDecodeError, ValueError, IndexError) as e:
            logger.warning(f"Failed to parse match response: {e}. Raw: {response_text[:200]}")
            return MatchResult(tier=3, score=0.0, reason="Parse error — could not evaluate match")

    def _fallback_result(self, title: str) -> MatchResult:
        """Return a safe fallback when both APIs fail."""
        if "product manager" in title.lower():
            return MatchResult(
                tier=2,
                score=0.5,
                reason="Both APIs unavailable — title matches PM role, defaulting to Tier 2."
            )
        return MatchResult(
            tier=3,
            score=0.0,
            reason="Both APIs unavailable — unable to evaluate"
        )
