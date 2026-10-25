"""
Workday ATS adapter.
Many large companies use Workday for their job boards.
Used by: CrowdStrike, Salesforce/Slack, Snowflake, and others.

Workday exposes a public search API at:
  https://{company}.wd{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs
"""

import requests
import logging
import re
from typing import List, Dict, Any
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)


class WorkdaySource(JobSource):
    """Fetch jobs from Workday's public job search API."""

    def __init__(self, company_name: str, config: Dict[str, Any],
                 search_keywords: List[str], target_locations: List[str]):
        super().__init__(company_name, config, search_keywords, target_locations)
        self.company_slug = config["company_slug"]
        self.wd_instance = config.get("wd_instance", "wd5")
        self.site_name = config["site_name"]

    def fetch_jobs(self) -> List[RawJob]:
        """Fetch jobs from Workday API and filter by keywords/location."""
        base_url = f"https://{self.company_slug}.{self.wd_instance}.myworkdayjobs.com"
        api_url = f"{base_url}/wday/cxs/{self.company_slug}/{self.site_name}/jobs"

        logger.info(f"[{self.company_name}] Fetching from Workday: {api_url}")

        raw_jobs = []
        offset = 0
        limit = 20  # Workday typically paginates in sets of 20

        for keyword in self.search_keywords:
            offset = 0
            while True:
                payload = {
                    "appliedFacets": {},
                    "limit": limit,
                    "offset": offset,
                    "searchText": keyword,
                }

                try:
                    response = requests.post(
                        api_url,
                        json=payload,
                        timeout=30,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                            "Accept": "application/json",
                        }
                    )
                    response.raise_for_status()
                    data = response.json()
                except requests.RequestException as e:
                    logger.error(f"[{self.company_name}] Workday API error: {e}")
                    break

                postings = data.get("jobPostings", [])
                if not postings:
                    break

                for posting in postings:
                    title = posting.get("title", "")
                    location = posting.get("locationsText", "")
                    posted_on = posting.get("postedOn", "")

                    # Build job URL from the external path
                    external_path = posting.get("externalPath", "")
                    job_url = f"{base_url}/en-US{external_path}" if external_path else ""

                    # Bullet points / short description
                    bullet_fields = posting.get("bulletFields", [])
                    description = f"Location: {location}. Posted: {posted_on}. " + " ".join(bullet_fields)

                    # Extract salary if available
                    salary = ""
                    pay_range = posting.get("payRange", "")
                    if pay_range:
                        salary = pay_range
                    elif posting.get("startingPay"):
                        salary = str(posting["startingPay"])

                    raw_jobs.append(RawJob(
                        company=self.company_name,
                        title=title,
                        url=job_url,
                        location=location,
                        description=description,
                        salary=salary,
                    ))

                total = data.get("total", 0)
                offset += limit
                if offset >= total:
                    break

        # Deduplicate by URL
        seen_urls = set()
        unique_jobs = []
        for job in raw_jobs:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                unique_jobs.append(job)

        logger.info(f"[{self.company_name}] Workday returned {len(unique_jobs)} unique jobs")

        # Enrich descriptions for keyword-matched jobs
        filtered = [j for j in unique_jobs
                    if self.matches_keywords(j.title) and self.matches_location(j.location)]

        for job in filtered:
            external_path = ""
            if "/en-US" in job.url:
                external_path = job.url.split("/en-US")[-1]
            if external_path:
                detail = self._fetch_job_detail(external_path)
                if detail:
                    job.description = detail

        return self.filter_jobs(unique_jobs)

    def _fetch_job_detail(self, external_path: str) -> str:
        """Fetch full job description from Workday detail endpoint."""
        base_url = f"https://{self.company_slug}.{self.wd_instance}.myworkdayjobs.com"
        detail_url = f"{base_url}/wday/cxs/{self.company_slug}/{self.site_name}{external_path}"

        try:
            response = requests.get(detail_url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
            })
            response.raise_for_status()
            data = response.json()

            posting = data.get("jobPostingInfo", {})
            description_html = posting.get("jobDescription", "")

            if description_html:
                text = re.sub(r'<[^>]+>', ' ', description_html)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:3000]

        except Exception as e:
            logger.debug(f"[{self.company_name}] Could not fetch Workday detail: {e}")

        return ""
