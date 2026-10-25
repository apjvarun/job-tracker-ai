"""
Stripe Careers adapter.
Stripe uses their own custom career site at stripe.com/jobs/search.
We scrape the page and extract job listings from embedded data or HTML.
"""

import requests
import logging
import json
import re
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from .base import JobSource, RawJob

logger = logging.getLogger(__name__)

STRIPE_JOBS_URL = "https://stripe.com/jobs/search"
STRIPE_JOB_BASE = "https://stripe.com/jobs/listing"


class StripeSource(JobSource):
    """Fetch jobs from Stripe's career site."""

    def fetch_jobs(self) -> List[RawJob]:
        logger.info(f"[{self.company_name}] Fetching from Stripe Careers")

        raw_jobs = []

        try:
            # Try the filtered search URL first (narrows results server-side)
            search_url = f"{STRIPE_JOBS_URL}?query=product+manager"
            response = requests.get(
                search_url,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                }
            )
            response.raise_for_status()

            # Try embedded JSON first, then HTML scraping
            raw_jobs = self._extract_from_json(response.text)

            if not raw_jobs:
                raw_jobs = self._scrape_html(response.text)

            # If filtered search returned nothing, try the full page
            if not raw_jobs:
                logger.info(f"[{self.company_name}] Filtered search empty, trying full jobs page")
                response = requests.get(
                    STRIPE_JOBS_URL,
                    timeout=30,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                    }
                )
                response.raise_for_status()
                raw_jobs = self._extract_from_json(response.text)
                if not raw_jobs:
                    raw_jobs = self._scrape_html(response.text)

        except requests.RequestException as e:
            logger.error(f"[{self.company_name}] Request failed: {e}")

        # Log sample titles BEFORE filtering so we can debug
        if raw_jobs:
            logger.info(f"[{self.company_name}] Sample scraped titles (first 5):")
            for job in raw_jobs[:5]:
                logger.info(f"  → '{job.title}' | loc='{job.location}' | url='{job.url[:80]}'")

        return self.filter_jobs(raw_jobs)

    def _extract_from_json(self, html: str) -> List[RawJob]:
        """Try to extract job data from embedded JSON in the page."""
        raw_jobs = []

        # Look for __NEXT_DATA__ which is common for Next.js sites
        next_data_match = re.search(
            r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        if next_data_match:
            try:
                data = json.loads(next_data_match.group(1))
                jobs = self._parse_job_data(data)
                if jobs:
                    logger.info(f"[{self.company_name}] Found {len(jobs)} jobs from __NEXT_DATA__")
                    return jobs
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"[{self.company_name}] __NEXT_DATA__ parse failed: {e}")

        # Try other common JSON embedding patterns
        patterns = [
            r'window\.__DATA__\s*=\s*({.*?});\s*</script>',
            r'"jobPostings"\s*:\s*(\[.*?\])',
            r'"openPositions"\s*:\s*(\[.*?\])',
            r'"jobs"\s*:\s*(\[.*?\])',
            r'"listings"\s*:\s*(\[.*?\])',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    jobs = self._parse_job_data(data)
                    if jobs:
                        logger.info(f"[{self.company_name}] Found {len(jobs)} jobs from JSON pattern")
                        raw_jobs.extend(jobs)
                        return raw_jobs
                except (json.JSONDecodeError, TypeError):
                    continue

        # Try JSON-LD structured data (schema.org JobPosting)
        ld_matches = re.findall(
            r'<script\s+type="application/ld\+json">(.*?)</script>',
            html, re.DOTALL
        )
        for ld_json in ld_matches:
            try:
                data = json.loads(ld_json)
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "JobPosting":
                            # Extract baseSalary from JSON-LD
                            item_salary = ""
                            item_base_sal = item.get("baseSalary", {})
                            if isinstance(item_base_sal, dict):
                                item_val = item_base_sal.get("value", {})
                                if isinstance(item_val, dict):
                                    s_min = item_val.get("minValue", "")
                                    s_max = item_val.get("maxValue", "")
                                    s_cur = item_base_sal.get("currency", "USD")
                                    if s_min and s_max:
                                        item_salary = f"{s_cur} {s_min}-{s_max}"
                                elif item_val:
                                    item_salary = str(item_val)
                            elif item_base_sal:
                                item_salary = str(item_base_sal)
                            raw_jobs.append(RawJob(
                                company=self.company_name,
                                title=item.get("title", ""),
                                url=item.get("url", ""),
                                location=str(item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")),
                                description=item.get("description", "")[:3000],
                                salary=item_salary,
                            ))
                elif isinstance(data, dict) and data.get("@type") == "JobPosting":
                    # Extract baseSalary from JSON-LD
                    ld_salary = ""
                    base_sal = data.get("baseSalary", {})
                    if isinstance(base_sal, dict):
                        val = base_sal.get("value", {})
                        if isinstance(val, dict):
                            sal_min = val.get("minValue", "")
                            sal_max = val.get("maxValue", "")
                            currency = base_sal.get("currency", "USD")
                            if sal_min and sal_max:
                                ld_salary = f"{currency} {sal_min}-{sal_max}"
                        elif val:
                            ld_salary = str(val)
                    elif base_sal:
                        ld_salary = str(base_sal)
                    raw_jobs.append(RawJob(
                        company=self.company_name,
                        title=data.get("title", ""),
                        url=data.get("url", ""),
                        location=str(data.get("jobLocation", {}).get("address", {}).get("addressLocality", "")),
                        description=data.get("description", "")[:3000],
                        salary=ld_salary,
                    ))
            except (json.JSONDecodeError, TypeError):
                continue

        if raw_jobs:
            logger.info(f"[{self.company_name}] Found {len(raw_jobs)} jobs from JSON-LD")

        return raw_jobs

    def _parse_job_data(self, data: Any, depth: int = 0) -> List[RawJob]:
        """Recursively parse job data from JSON."""
        if depth > 15:
            return []

        jobs = []

        if isinstance(data, dict):
            # Check for job-like structure
            has_title = "title" in data or "name" in data
            has_url = "url" in data or "slug" in data or "id" in data or "href" in data

            if has_title and has_url:
                title = data.get("title", data.get("name", ""))
                location = data.get("location", "")
                if isinstance(location, dict):
                    location = location.get("name", location.get("city", ""))
                elif isinstance(location, list):
                    location = ", ".join(str(l) for l in location)

                slug = data.get("slug", data.get("id", ""))
                url = data.get("url", data.get("href", ""))
                if not url and slug:
                    url = f"{STRIPE_JOB_BASE}/{slug}"
                elif url and not url.startswith("http"):
                    url = f"https://stripe.com{url}"

                description = data.get("description", data.get("content", ""))
                if isinstance(description, dict):
                    description = str(description)

                if title and isinstance(title, str) and len(title) > 3:
                    jobs.append(RawJob(
                        company=self.company_name,
                        title=str(title),
                        url=str(url),
                        location=str(location),
                        description=str(description)[:3000]
                    ))

            # Recurse into values
            for value in data.values():
                if isinstance(value, (dict, list)):
                    jobs.extend(self._parse_job_data(value, depth + 1))

        elif isinstance(data, list):
            for item in data:
                jobs.extend(self._parse_job_data(item, depth + 1))

        return jobs

    def _scrape_html(self, html: str) -> List[RawJob]:
        """Scrape job listings from HTML."""
        raw_jobs = []
        soup = BeautifulSoup(html, "lxml")

        # Strategy 1: Find all links to /jobs/listing/ pages
        job_links = soup.select("a[href*='/jobs/listing/']")

        seen_urls = set()
        for link in job_links:
            href = link.get("href", "")
            url = href if href.startswith("http") else f"https://stripe.com{href}"

            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Try to get a clean title
            # First try direct child heading elements
            title = ""
            for selector in ["h2", "h3", "h4", "[role='heading']"]:
                el = link.select_one(selector)
                if el:
                    title = el.get_text(strip=True)
                    break

            if not title:
                # Get the link text, but try to clean it
                full_text = link.get_text(separator=" | ", strip=True)
                # Often the title is the first segment before a separator
                parts = re.split(r'\s*[|·•—–]\s*', full_text)
                title = parts[0].strip() if parts else full_text

            # Try to find location
            location = ""
            # Check for location in the link text segments
            full_text = link.get_text(separator=" | ", strip=True)
            parts = re.split(r'\s*[|·•—–]\s*', full_text)
            for part in parts[1:]:  # Skip first part (title)
                if any(loc_kw in part.lower() for loc_kw in
                       ["san francisco", "new york", "seattle", "remote",
                        "usa", "united states", "california"]):
                    location = part.strip()
                    break

            # Also check parent/sibling elements for location
            if not location:
                parent = link.parent
                if parent:
                    for selector in ["[class*='location']", "[class*='Location']",
                                     "span:last-child", "p"]:
                        loc_el = parent.select_one(selector)
                        if loc_el and loc_el != link:
                            loc_text = loc_el.get_text(strip=True)
                            if len(loc_text) < 100:  # Sanity check
                                location = loc_text
                                break

            if title and len(title) > 3:
                raw_jobs.append(RawJob(
                    company=self.company_name,
                    title=title,
                    url=url,
                    location=location,
                    description=""
                ))

        # Strategy 2: If no /jobs/listing/ links found, look for other patterns
        if not raw_jobs:
            # Look for any structured job cards
            cards = soup.select(
                "[class*='JobCard'], [class*='job-card'], "
                "[class*='listing'], [data-testid*='job']"
            )
            for card in cards:
                link_el = card.select_one("a[href]")
                title_el = card.select_one("h2, h3, h4, [class*='title']")

                if link_el and title_el:
                    href = link_el.get("href", "")
                    url = href if href.startswith("http") else f"https://stripe.com{href}"
                    title = title_el.get_text(strip=True)

                    if title and url not in seen_urls:
                        seen_urls.add(url)
                        raw_jobs.append(RawJob(
                            company=self.company_name,
                            title=title,
                            url=url,
                            location="",
                            description=""
                        ))

        logger.info(f"[{self.company_name}] Scraped {len(raw_jobs)} jobs from HTML")
        return raw_jobs
