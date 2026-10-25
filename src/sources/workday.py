"""
Generic career page scraper for companies using custom career pages.
Handles Google Careers, Meta Careers, and other custom career sites.

For v1, these use API endpoints where possible, falling back to HTML scraping.
"""

import requests
import logging
import re
import json
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)


class GoogleCareersSource(JobSource):
    """Fetch jobs from Google Careers.

    Google Careers has an internal API that returns JSON.
    We query it with appropriate parameters for Product Manager roles.
    """

    def fetch_jobs(self) -> List[RawJob]:
        logger.info(f"[{self.company_name}] Fetching from Google Careers")

        raw_jobs = []

        # Google Careers API endpoint
        # This searches for Product Manager roles in the US
        api_url = "https://careers.google.com/api/v3/search/"
        params = {
            "q": "Product Manager",
            "location": "California, United States",
            "page_size": 50,
        }

        try:
            response = requests.get(api_url, params=params, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })

            if response.status_code == 200:
                try:
                    data = response.json()
                    for job in data.get("jobs", []):
                        title = job.get("title", "")
                        locations = job.get("locations", [])
                        location = ", ".join(locations) if locations else ""
                        description = job.get("description", "")
                        # Strip HTML
                        description = re.sub(r'<[^>]+>', ' ', description)
                        job_url = f"https://careers.google.com/jobs/results/{job.get('id', '')}/"

                        raw_jobs.append(RawJob(
                            company=self.company_name,
                            title=title,
                            url=job_url,
                            location=location,
                            description=description[:3000]
                        ))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"[{self.company_name}] JSON parse error, trying HTML fallback: {e}")
                    raw_jobs = self._scrape_html_fallback()
            else:
                logger.warning(f"[{self.company_name}] API returned {response.status_code}, trying HTML fallback")
                raw_jobs = self._scrape_html_fallback()

        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Request failed: {e}")
            raw_jobs = self._scrape_html_fallback()

        return self.filter_jobs(raw_jobs)

    def _scrape_html_fallback(self) -> List[RawJob]:
        """Fallback HTML scraping for Google Careers."""
        try:
            url = "https://www.google.com/about/careers/applications/jobs/results/?q=Product%20Manager&location=California"
            response = requests.get(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            soup = BeautifulSoup(response.text, "lxml")

            raw_jobs = []
            # Look for job cards - Google's structure may vary
            job_cards = soup.select("[data-job-id], .gc-card, .lLd2De")
            for card in job_cards:
                title_el = card.select_one("h3, .QJPWVe, [data-title]")
                location_el = card.select_one(".r0wTof, [data-location]")
                link_el = card.select_one("a[href]")

                if title_el:
                    raw_jobs.append(RawJob(
                        company=self.company_name,
                        title=title_el.get_text(strip=True),
                        url=link_el["href"] if link_el else "",
                        location=location_el.get_text(strip=True) if location_el else "",
                        description=""
                    ))

            return raw_jobs
        except Exception as e:
            logger.error(f"[{self.company_name}] HTML fallback failed: {e}")
            return []


class MetaCareersSource(JobSource):
    """Fetch jobs from Meta Careers.

    Meta Careers has a GraphQL-like API behind the scenes.
    We'll try the API first, then fall back to HTML scraping.
    """

    def fetch_jobs(self) -> List[RawJob]:
        logger.info(f"[{self.company_name}] Fetching from Meta Careers")

        raw_jobs = []

        # Meta's career API endpoint
        api_url = "https://www.metacareers.com/graphql"

        # Try the search page directly
        search_url = "https://www.metacareers.com/jobs?q=product%20manager&location[0]=United%20States"

        try:
            response = requests.get(search_url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml"
            })

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")

                # Try to extract embedded JSON data from script tags
                for script in soup.find_all("script", type="application/json"):
                    try:
                        data = json.loads(script.string)
                        raw_jobs.extend(self._extract_from_json(data))
                    except (json.JSONDecodeError, TypeError):
                        continue

                # If no JSON data found, try HTML parsing
                if not raw_jobs:
                    raw_jobs = self._parse_html(soup)

        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Request failed: {e}")

        return self.filter_jobs(raw_jobs)

    def _extract_from_json(self, data: Any, depth: int = 0) -> List[RawJob]:
        """Recursively search JSON for job data."""
        if depth > 10:
            return []

        jobs = []
        if isinstance(data, dict):
            # Check if this looks like a job posting
            if "title" in data and ("location" in data or "locations" in data):
                title = data.get("title", "")
                location = data.get("location", data.get("locations", ""))
                if isinstance(location, list):
                    location = ", ".join(str(l) for l in location)
                url = data.get("url", data.get("canonical_url", ""))
                if not url and data.get("id"):
                    url = f"https://www.metacareers.com/jobs/{data['id']}/"
                description = data.get("description", "")

                if title:
                    jobs.append(RawJob(
                        company=self.company_name,
                        title=str(title),
                        url=str(url),
                        location=str(location),
                        description=str(description)[:3000]
                    ))

            for value in data.values():
                jobs.extend(self._extract_from_json(value, depth + 1))

        elif isinstance(data, list):
            for item in data:
                jobs.extend(self._extract_from_json(item, depth + 1))

        return jobs

    def _parse_html(self, soup: BeautifulSoup) -> List[RawJob]:
        """Fallback HTML parsing for Meta Careers."""
        raw_jobs = []
        # Look for job listing elements
        job_links = soup.select("a[href*='/jobs/']")
        seen_urls = set()

        for link in job_links:
            href = link.get("href", "")
            if "/jobs/" not in href or href in seen_urls:
                continue
            seen_urls.add(href)

            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            url = href if href.startswith("http") else f"https://www.metacareers.com{href}"

            raw_jobs.append(RawJob(
                company=self.company_name,
                title=title,
                url=url,
                location="",
                description=""
            ))

        return raw_jobs
