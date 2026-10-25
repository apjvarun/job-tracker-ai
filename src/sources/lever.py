"""
Lever API adapter.
Lever provides a public JSON API for company job boards.
Used by: Stripe, Netflix, and many others.
"""

import requests
import logging
from typing import List, Dict, Any
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)

LEVER_API_BASE = "https://api.lever.co/v0/postings"


class LeverSource(JobSource):
    """Fetch jobs from Lever's public API."""

    def __init__(self, company_name: str, config: Dict[str, Any],
                 search_keywords: List[str], target_locations: List[str]):
        super().__init__(company_name, config, search_keywords, target_locations)
        self.company_slug = config["company_slug"]

    def fetch_jobs(self) -> List[RawJob]:
        """Fetch jobs from Lever API and filter by keywords/location."""
        url = f"{LEVER_API_BASE}/{self.company_slug}?mode=json"
        logger.info(f"[{self.company_name}] Fetching from Lever: {url}")

        try:
            response = requests.get(url, timeout=30, headers={
                "User-Agent": "JobTrackerAlert/1.0"
            })
            response.raise_for_status()
            postings = response.json()
        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Lever API error: {e}")
            return []

        raw_jobs = []
        for posting in postings:
            title = posting.get("text", "")
            location = posting.get("categories", {}).get("location", "")
            if isinstance(location, list):
                location = ", ".join(location)

            # Build the description from available fields
            description_parts = []
            if posting.get("descriptionPlain"):
                description_parts.append(posting["descriptionPlain"])
            for lst in posting.get("lists", []):
                if lst.get("text"):
                    description_parts.append(lst["text"])
                if lst.get("content"):
                    # Strip HTML tags from content
                    import re
                    clean = re.sub(r'<[^>]+>', ' ', lst["content"])
                    description_parts.append(clean)

            description = "\n".join(description_parts)

            # Also check additional metadata
            additional_plain = posting.get("additionalPlain", "")
            if additional_plain:
                description += "\n" + additional_plain

            # Extract salary/compensation
            salary = ""
            salary_range = posting.get("salaryRange", {})
            if salary_range:
                sr_min = salary_range.get("min", "")
                sr_max = salary_range.get("max", "")
                sr_currency = salary_range.get("currency", "USD")
                if sr_min and sr_max:
                    salary = f"{sr_currency} {sr_min}-{sr_max}"
                elif sr_max:
                    salary = f"Up to {sr_currency} {sr_max}"
            if not salary:
                comp = posting.get("categories", {}).get("compensation", "")
                if comp:
                    salary = comp if isinstance(comp, str) else str(comp)

            raw_jobs.append(RawJob(
                company=self.company_name,
                title=title,
                url=posting.get("hostedUrl", posting.get("applyUrl", "")),
                location=location,
                description=description[:3000],
                salary=salary,
            ))

        return self.filter_jobs(raw_jobs)
