"""
SmartRecruiters API adapter.
SmartRecruiters provides a public API for company job boards.
Used by: Palo Alto Networks, Block (Square), and others.

Public API: https://api.smartrecruiters.com/v1/companies/{companyId}/postings
"""

import requests
import logging
import re
from typing import List, Dict, Any
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)

SMARTRECRUITERS_API_BASE = "https://api.smartrecruiters.com/v1/companies"


class SmartRecruitersSource(JobSource):
    """Fetch jobs from SmartRecruiters public API."""

    def __init__(self, company_name: str, config: Dict[str, Any],
                 search_keywords: List[str], target_locations: List[str]):
        super().__init__(company_name, config, search_keywords, target_locations)
        self.company_id = config["company_id"]

    def fetch_jobs(self) -> List[RawJob]:
        """Fetch jobs from SmartRecruiters API and filter by keywords/location."""
        logger.info(f"[{self.company_name}] Fetching from SmartRecruiters: {self.company_id}")

        raw_jobs = []
        offset = 0
        limit = 100

        while True:
            url = f"{SMARTRECRUITERS_API_BASE}/{self.company_id}/postings"
            params = {
                "offset": offset,
                "limit": limit,
            }

            try:
                response = requests.get(url, params=params, timeout=30, headers={
                    "User-Agent": "JobTrackerAlert/1.0",
                    "Accept": "application/json"
                })
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                logger.error(f"[{self.company_name}] SmartRecruiters API error: {e}")
                break

            postings = data.get("content", [])
            if not postings:
                break

            for posting in postings:
                title = posting.get("name", "")

                # Location
                location_obj = posting.get("location", {})
                city = location_obj.get("city", "")
                region = location_obj.get("region", "")
                country = location_obj.get("country", "")
                remote = location_obj.get("remote", False)

                location_parts = [p for p in [city, region] if p]
                location = ", ".join(location_parts)
                if remote:
                    location = f"{location} (Remote)" if location else "Remote"

                # URL
                company_slug = posting.get("company", {}).get("identifier", self.company_id)
                posting_id = posting.get("id", "")
                ref_url = posting.get("ref", "")
                job_url = f"https://jobs.smartrecruiters.com/{company_slug}/{posting_id}"

                # Description from the posting list is usually brief;
                # we'll use the job detail endpoint for matched jobs later
                department = posting.get("department", {}).get("label", "")
                experience = posting.get("experienceLevel", {}).get("name", "")
                desc_parts = []
                if department:
                    desc_parts.append(f"Department: {department}")
                if experience:
                    desc_parts.append(f"Experience: {experience}")
                description = ". ".join(desc_parts)

                # Extract compensation if available
                salary = ""
                comp_obj = posting.get("compensation", {})
                if comp_obj:
                    min_val = comp_obj.get("min", "")
                    max_val = comp_obj.get("max", "")
                    currency = comp_obj.get("currency", "USD")
                    if min_val and max_val:
                        salary = f"{currency} {min_val}-{max_val}"
                    elif max_val:
                        salary = f"Up to {currency} {max_val}"

                raw_jobs.append(RawJob(
                    company=self.company_name,
                    title=title,
                    url=job_url,
                    location=location,
                    description=description,
                    salary=salary,
                ))

            # Check if there are more pages
            total = data.get("totalFound", 0)
            offset += limit
            if offset >= total:
                break

        logger.info(f"[{self.company_name}] SmartRecruiters returned {len(raw_jobs)} total jobs")

        # Enrich descriptions for keyword-matched jobs
        filtered = [j for j in raw_jobs
                    if self.matches_keywords(j.title) and self.matches_location(j.location)]

        for job in filtered:
            posting_id = job.url.split("/")[-1]
            detail = self._fetch_job_detail(posting_id)
            if detail:
                job.description = detail

        return self.filter_jobs(raw_jobs)

    def _fetch_job_detail(self, posting_id: str) -> str:
        """Fetch full job description for a specific posting."""
        url = f"{SMARTRECRUITERS_API_BASE}/{self.company_id}/postings/{posting_id}"

        try:
            response = requests.get(url, timeout=15, headers={
                "User-Agent": "JobTrackerAlert/1.0",
                "Accept": "application/json"
            })
            response.raise_for_status()
            data = response.json()

            # Extract description sections
            sections = data.get("jobAd", {}).get("sections", {})
            parts = []

            for section_key in ["companyDescription", "jobDescription", "qualifications", "additionalInformation"]:
                section = sections.get(section_key, {})
                title = section.get("title", "")
                text = section.get("text", "")
                if text:
                    # Strip HTML
                    clean = re.sub(r'<[^>]+>', ' ', text)
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    if title:
                        parts.append(f"{title}: {clean}")
                    else:
                        parts.append(clean)

            return "\n".join(parts)[:3000]

        except Exception as e:
            logger.debug(f"[{self.company_name}] Could not fetch detail for posting {posting_id}: {e}")
            return ""
