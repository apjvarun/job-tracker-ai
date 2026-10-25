"""
Base class for all job source adapters.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class RawJob:
    """A raw job posting fetched from a source, before matching."""
    company: str
    title: str
    url: str
    location: str
    description: str
    salary: str = ""


class JobSource(ABC):
    """Abstract base class for all job source adapters."""

    def __init__(self, company_name: str, config: Dict[str, Any],
                 search_keywords: List[str], target_locations: List[str]):
        self.company_name = company_name
        self.config = config
        self.search_keywords = [kw.lower() for kw in search_keywords]
        self.target_locations = [loc.lower() for loc in target_locations]

    @abstractmethod
    def fetch_jobs(self) -> List[RawJob]:
        """Fetch all relevant job postings from this source."""
        pass

    def matches_keywords(self, title: str) -> bool:
        """Check if a job title matches any of the search keywords."""
        title_lower = title.lower()
        return any(kw in title_lower for kw in self.search_keywords)

    def matches_location(self, location: str) -> bool:
        """Check if a job location matches any target locations."""
        if not location:
            return True  # If no location specified, include it
        location_lower = location.lower()
        # Also match if location contains "remote" or "united states"
        if "remote" in location_lower or "united states" in location_lower:
            return True
        return any(loc in location_lower for loc in self.target_locations)

    def filter_jobs(self, jobs: List[RawJob]) -> List[RawJob]:
        """Filter jobs by keywords and location."""
        filtered = []
        for job in jobs:
            if self.matches_keywords(job.title) and self.matches_location(job.location):
                filtered.append(job)
        logger.info(f"[{self.company_name}] {len(jobs)} total jobs → {len(filtered)} after filtering")
        return filtered
