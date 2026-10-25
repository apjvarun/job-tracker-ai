"""
Greenhouse API adapter.
Greenhouse provides a public board API for company job boards.
Used by: Anthropic, Databricks, Figma, Airbnb, Coinbase, and many others.
"""

import requests
import logging
import re
from typing import List, Dict, Any
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)

GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseSource(JobSource):
    """Fetch jobs from Greenhouse's public board API."""

    def __init__(self, company_name: str, config: Dict[str, Any],
                 search_keywords: List[str], target_locations: List[str]):
        super().__init__(company_name, config, search_keywords, target_locations)
        self.board_slug = config["board_slug"]

    def fetch_jobs(self) -> List[RawJob]:
        """Fetch jobs from Greenhouse API and filter by keywords/location."""
        url = f"{GREENHOUSE_API_BASE}/{self.board_slug}/jobs?content=true"
        logger.info(f"[{self.company_name}] Fetching from Greenhouse: {url}")

        try:
            response = requests.get(url, timeout=30, headers={
                "User-Agent": "JobTrackerAlert/1.0"
            })
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Greenhouse API error: {e}")
            return []

        jobs_data = data.get("jobs", [])
        raw_jobs = []

        for job in jobs_data:
            title = job.get("title", "")
            location_obj = job.get("location", {})
            location = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)

            # Get the job description (HTML content)
            content = job.get("content", "")
            # Strip HTML
            description = re.sub(r'<[^>]+>', ' ', content)
            description = re.sub(r'\s+', ' ', description).strip()

            job_url = job.get("absolute_url", "")

            # Extract salary from pay_input_ranges if available
            salary = ""
            pay_ranges = job.get("pay_input_ranges", [])
            if pay_ranges:
                parts = []
                for pr in pay_ranges:
                    min_val = pr.get("min_value", "")
                    max_val = pr.get("max_value", "")
                    currency = pr.get("currency_type", "USD")
                    title_r = pr.get("title", "")
                    if min_val and max_val:
                        parts.append(f"{title_r}: {currency} {min_val}-{max_val}".strip(": "))
                    elif max_val:
                        parts.append(f"Up to {currency} {max_val}")
                salary = "; ".join(parts)
            # Also try to find salary in description text
            if not salary:
                import re as _re
                sal_match = _re.search(
                    r'\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*(?:per year|annually|/yr|/year))?',
                    description
                )
                if sal_match:
                    salary = sal_match.group(0)

            raw_jobs.append(RawJob(
                company=self.company_name,
                title=title,
                url=job_url,
                location=location,
                description=description[:3000],
                salary=salary,
            ))

        return self.filter_jobs(raw_jobs)
