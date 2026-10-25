"""
Netflix Careers adapter.
Netflix uses Eightfold AI platform at explore.jobs.netflix.net.
The careers page embeds job data as JSON in the HTML source.
"""

import requests
import logging
import json
import re
from typing import List, Dict, Any
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)

NETFLIX_CAREERS_URL = "https://explore.jobs.netflix.net/careers"
NETFLIX_JOB_BASE = "https://explore.jobs.netflix.net/careers/job"


class NetflixSource(JobSource):
    """Fetch jobs from Netflix's Eightfold AI-powered careers site."""

    def fetch_jobs(self) -> List[RawJob]:
        logger.info(f"[{self.company_name}] Fetching from Netflix Careers (Eightfold)")

        raw_jobs = []

        # Approach 1: Use the search URL with query parameters
        # Netflix's Eightfold platform supports query params for filtering
        params = {
            "query": "Product Manager",
            "domain": "netflix.com",
            "sort_by": "relevance",
        }

        try:
            response = requests.get(
                NETFLIX_CAREERS_URL,
                params=params,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/json",
                }
            )
            response.raise_for_status()

            # The Eightfold platform embeds job data as JSON in script tags
            # or in the page's initial state
            raw_jobs = self._extract_jobs_from_html(response.text)

            if not raw_jobs:
                # Approach 2: Try the Eightfold API endpoint directly
                raw_jobs = self._try_api_endpoint()

        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Request failed: {e}")

        return self.filter_jobs(raw_jobs)

    def _extract_jobs_from_html(self, html: str) -> List[RawJob]:
        """Extract job data from embedded JSON in the HTML page."""
        raw_jobs = []

        # Pattern 1: Look for positions data in script tags
        # Eightfold embeds a JSON blob with positions in __NEXT_DATA__ or similar
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'window\.__NEXT_DATA__\s*=\s*({.*?});',
            r'"positions"\s*:\s*(\[.*?\])\s*[,}]',
            r'data-positions=\'(.*?)\'',
            r'"jobs"\s*:\s*(\[.*?\])\s*[,}]',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    jobs = self._extract_positions(data)
                    if jobs:
                        raw_jobs.extend(jobs)
                        logger.info(f"[{self.company_name}] Found {len(jobs)} positions via pattern")
                        return raw_jobs
                except (json.JSONDecodeError, TypeError):
                    continue

        # Pattern 2: Parse structured job cards from HTML
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        # Eightfold typically renders job cards with specific data attributes
        job_cards = soup.select(
            "[data-test='position-card'], "
            ".position-card, "
            "[class*='job-card'], "
            "[class*='JobCard'], "
            "[class*='position-list'] a, "
            "a[href*='/careers/job/']"
        )

        seen_urls = set()
        for card in job_cards:
            href = card.get("href", "")
            if not href:
                link = card.select_one("a[href]")
                if link:
                    href = link.get("href", "")

            if "/careers/job/" not in href:
                continue

            # Build full URL
            if href.startswith("/"):
                url = f"https://explore.jobs.netflix.net{href}"
            elif not href.startswith("http"):
                url = f"{NETFLIX_JOB_BASE}/{href}"
            else:
                url = href

            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract title - try multiple selectors
            title = ""
            for selector in ["h3", "h2", "[class*='title']", "[class*='name']", "[data-test='position-title']"]:
                el = card.select_one(selector)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title:
                title = card.get_text(strip=True)[:100]

            # Extract location
            location = ""
            for selector in ["[class*='location']", "[data-test='position-location']", "span"]:
                el = card.select_one(selector)
                if el and el != card:
                    loc_text = el.get_text(strip=True)
                    if any(kw in loc_text.lower() for kw in ["usa", "remote", "ca", "ny", "wa", "los", "san"]):
                        location = loc_text
                        break

            if title and len(title) > 3:
                raw_jobs.append(RawJob(
                    company=self.company_name,
                    title=title,
                    url=url,
                    location=location,
                    description=""  # Will be fetched on demand if needed
                ))

        logger.info(f"[{self.company_name}] Extracted {len(raw_jobs)} jobs from HTML")
        return raw_jobs

    def _extract_positions(self, data: Any) -> List[RawJob]:
        """Recursively extract position objects from JSON data."""
        jobs = []

        if isinstance(data, dict):
            # Check if this looks like a positions container
            if "positions" in data:
                return self._extract_positions(data["positions"])
            if "jobs" in data:
                return self._extract_positions(data["jobs"])

            # Check if this looks like a single position
            if "name" in data and ("id" in data or "ats_job_id" in data):
                name = data.get("name", "")
                locations = data.get("locations", [])
                if isinstance(locations, list):
                    location = ", ".join(str(l) for l in locations)
                else:
                    location = str(data.get("location", locations))

                job_id = data.get("id", "")
                url = data.get("canonicalPositionUrl", "")
                if not url and job_id:
                    url = f"{NETFLIX_JOB_BASE}/{job_id}"

                description = data.get("description", data.get("descriptionTeaser", ""))

                # Extract salary/compensation if available
                salary = ""
                for key in ("salary", "compensation", "salaryRange", "pay"):
                    val = data.get(key, "")
                    if val:
                        salary = str(val)
                        break

                jobs.append(RawJob(
                    company=self.company_name,
                    title=name,
                    url=url,
                    location=location,
                    description=str(description)[:3000],
                    salary=salary,
                ))

            # Recurse into values
            for value in data.values():
                if isinstance(value, (dict, list)):
                    jobs.extend(self._extract_positions(value))

        elif isinstance(data, list):
            for item in data:
                jobs.extend(self._extract_positions(item))

        return jobs

    def _try_api_endpoint(self) -> List[RawJob]:
        """Try to hit the Eightfold API endpoint directly."""
        # Some Eightfold deployments expose a /api/apply/v2/jobs endpoint
        api_urls = [
            "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&query=Product%20Manager&num=50",
            "https://explore.jobs.netflix.net/careers/api/jobs?query=Product+Manager&domain=netflix.com",
        ]

        for url in api_urls:
            try:
                response = requests.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Accept": "application/json",
                })
                if response.status_code == 200:
                    data = response.json()
                    jobs = self._extract_positions(data)
                    if jobs:
                        logger.info(f"[{self.company_name}] Got {len(jobs)} jobs from API endpoint")
                        return jobs
            except Exception:
                continue

        return []
