"""
Ashby API adapter.
Ashby provides a public job board API for companies.
Used by: OpenAI, Notion, Ramp, and others.

Ashby's public API endpoint:
  POST https://jobs.ashbyhq.com/api/non-user-graphql
  with a specific query to list job postings.

Alternative: scrape https://jobs.ashbyhq.com/{company_slug}
"""

import requests
import logging
import re
import json
from typing import List, Dict, Any
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)

ASHBY_API_URL = "https://jobs.ashbyhq.com/api/non-user-graphql"


class AshbySource(JobSource):
    """Fetch jobs from Ashby's public job board API."""

    def __init__(self, company_name: str, config: Dict[str, Any],
                 search_keywords: List[str], target_locations: List[str]):
        super().__init__(company_name, config, search_keywords, target_locations)
        self.company_slug = config["company_slug"]

    def fetch_jobs(self) -> List[RawJob]:
        """Fetch jobs from Ashby API and filter by keywords/location."""
        logger.info(f"[{self.company_name}] Fetching from Ashby: {self.company_slug}")

        # Try the GraphQL API first
        raw_jobs = self._fetch_via_api()

        # Fallback to scraping the job board page
        if not raw_jobs:
            raw_jobs = self._fetch_via_scrape()

        return self.filter_jobs(raw_jobs)

    def _fetch_via_api(self) -> List[RawJob]:
        """Fetch via Ashby's non-user GraphQL API."""
        # Ashby uses a specific GraphQL query for public job boards
        payload = {
            "operationName": "ApiJobBoardWithTeams",
            "variables": {
                "organizationHostedJobsPageName": self.company_slug
            },
            "query": """
                query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
                    jobBoard: jobBoardWithTeams(
                        organizationHostedJobsPageName: $organizationHostedJobsPageName
                    ) {
                        teams {
                            id
                            name
                            parentTeamId
                            jobs {
                                id
                                title
                                teamId
                                locationId
                                locationName
                                employmentType
                                secondaryLocations {
                                    locationId
                                    locationName
                                }
                                compensationTierSummary
                            }
                        }
                    }
                }
            """
        }

        try:
            response = requests.post(
                ASHBY_API_URL,
                json=payload,
                timeout=30,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "JobTrackerAlert/1.0"
                }
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Ashby API error: {e}")
            return []

        raw_jobs = []
        job_board = data.get("data", {}).get("jobBoard", {})
        teams = job_board.get("teams", [])

        for team in teams:
            team_name = team.get("name", "")
            for job in team.get("jobs", []):
                title = job.get("title", "")
                location = job.get("locationName", "")

                # Include secondary locations
                secondary = job.get("secondaryLocations", [])
                if secondary:
                    all_locations = [location] + [s.get("locationName", "") for s in secondary]
                    location = " | ".join(filter(None, all_locations))

                job_id = job.get("id", "")
                job_url = f"https://jobs.ashbyhq.com/{self.company_slug}/{job_id}"

                # Build a description from available info
                comp = job.get("compensationTierSummary", "")
                emp_type = job.get("employmentType", "")
                description = f"Team: {team_name}. "
                if emp_type:
                    description += f"Type: {emp_type}. "
                if comp:
                    description += f"Compensation: {comp}. "

                raw_jobs.append(RawJob(
                    company=self.company_name,
                    title=title,
                    url=job_url,
                    location=location,
                    description=description,
                    salary=comp,
                ))

        logger.info(f"[{self.company_name}] Ashby API returned {len(raw_jobs)} jobs")

        # Enrich descriptions by fetching individual job details for filtered jobs
        filtered = [j for j in raw_jobs
                    if self.matches_keywords(j.title) and self.matches_location(j.location)]
        for job in filtered:
            try:
                detail = self._fetch_job_detail(job.url.split("/")[-1])
                if detail:
                    job.description = detail
            except Exception as e:
                logger.debug(f"[{self.company_name}] Could not fetch detail for {job.title}: {e}")

        return raw_jobs

    def _fetch_job_detail(self, job_id: str) -> str:
        """Fetch full job description for a specific posting."""
        payload = {
            "operationName": "ApiJobBoardJobPosting",
            "variables": {
                "organizationHostedJobsPageName": self.company_slug,
                "jobPostingId": job_id
            },
            "query": """
                query ApiJobBoardJobPosting(
                    $organizationHostedJobsPageName: String!,
                    $jobPostingId: String!
                ) {
                    jobPosting(
                        organizationHostedJobsPageName: $organizationHostedJobsPageName,
                        jobPostingId: $jobPostingId
                    ) {
                        title
                        descriptionHtml
                        locationName
                    }
                }
            """
        }

        response = requests.post(
            ASHBY_API_URL,
            json=payload,
            timeout=15,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "JobTrackerAlert/1.0"
            }
        )
        response.raise_for_status()
        data = response.json()

        posting = data.get("data", {}).get("jobPosting", {})
        html = posting.get("descriptionHtml", "")
        if html:
            # Strip HTML tags
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:3000]

        return ""

    def _fetch_via_scrape(self) -> List[RawJob]:
        """Fallback: scrape the Ashby job board HTML page."""
        url = f"https://jobs.ashbyhq.com/{self.company_slug}"
        logger.info(f"[{self.company_name}] Falling back to Ashby HTML scrape: {url}")

        try:
            from bs4 import BeautifulSoup

            response = requests.get(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")
            raw_jobs = []

            # Look for job links
            for link in soup.select("a[href*='/jobs/']"):
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if not title or len(title) < 5:
                    continue

                job_url = href if href.startswith("http") else f"https://jobs.ashbyhq.com{href}"

                raw_jobs.append(RawJob(
                    company=self.company_name,
                    title=title,
                    url=job_url,
                    location="",
                    description=""
                ))

            return raw_jobs

        except Exception as e:
            logger.error(f"[{self.company_name}] Ashby scrape failed: {e}")
            return []
