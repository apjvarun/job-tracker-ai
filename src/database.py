"""
Database layer for the Job Tracker Alert system.
Uses SQLite to persist jobs, match results, and notification history.
"""

import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict


@dataclass
class Job:
    """Represents a job posting."""
    company: str
    title: str
    url: str
    location: str
    description: str
    description_hash: str = ""
    salary: str = ""
    id: Optional[int] = None
    match_tier: Optional[int] = None
    match_score: Optional[float] = None
    match_reason: Optional[str] = None
    first_seen: Optional[str] = None
    status: str = "new"  # new, applied, dismissed

    def __post_init__(self):
        if not self.description_hash:
            self.description_hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Create a unique hash for deduplication."""
        combined = f"{self.company}|{self.title}|{self.url}"
        return hashlib.md5(combined.encode()).hexdigest()


class JobDatabase:
    """SQLite database manager for job tracking."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    location TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    description_hash TEXT UNIQUE NOT NULL,
                    match_tier INTEGER,
                    match_score REAL,
                    match_reason TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'new',
                    starred INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_hash ON jobs(description_hash);
                CREATE INDEX IF NOT EXISTS idx_jobs_tier ON jobs(match_tier);
                CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_notifications_job ON notifications(job_id);
            """)
            # Migration: add starred column to existing databases
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN starred INTEGER DEFAULT 0")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Migration: add salary column
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN salary TEXT DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists
        finally:
            conn.close()

    def job_exists(self, description_hash: str) -> bool:
        """Check if a job with this hash already exists."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE description_hash = ?",
                (description_hash,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def insert_job(self, job: Job) -> int:
        """Insert a new job, returns the job ID."""
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO jobs
                (company, title, url, location, description, description_hash, salary, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (job.company, job.title, job.url, job.location,
                  job.description, job.description_hash, job.salary, job.status))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_match(self, job_id: int, tier: int, score: float, reason: str):
        """Update a job's match results."""
        conn = self._get_conn()
        try:
            conn.execute("""
                UPDATE jobs
                SET match_tier = ?, match_score = ?, match_reason = ?
                WHERE id = ?
            """, (tier, score, reason, job_id))
            conn.commit()
        finally:
            conn.close()

    def update_status(self, job_id: int, status: str):
        """Update a job's status (new/applied/dismissed)."""
        conn = self._get_conn()
        try:
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
            conn.commit()
        finally:
            conn.close()

    def toggle_star(self, job_id: int):
        """Toggle the starred state of a job."""
        conn = self._get_conn()
        try:
            conn.execute("UPDATE jobs SET starred = CASE WHEN starred = 1 THEN 0 ELSE 1 END WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()

    def record_notification(self, job_id: int, channel: str):
        """Record that a notification was sent for a job."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO notifications (job_id, channel) VALUES (?, ?)",
                (job_id, channel)
            )
            conn.commit()
        finally:
            conn.close()

    def was_notified(self, job_id: int, channel: str) -> bool:
        """Check if a notification was already sent for this job on this channel."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE job_id = ? AND channel = ?",
                (job_id, channel)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_unnotified_jobs(self, tier: int, channel: str) -> List[Dict]:
        """Get jobs of a specific tier that haven't been notified on the given channel."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT j.* FROM jobs j
                WHERE j.match_tier = ?
                AND j.status != 'dismissed'
                AND j.id NOT IN (
                    SELECT job_id FROM notifications WHERE channel = ?
                )
                ORDER BY j.first_seen DESC
            """, (tier, channel)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_digest_jobs(self, hours: int = 24) -> List[Dict]:
        """Get Tier 2 (and optionally Tier 3) jobs from the last N hours for the daily digest."""
        conn = self._get_conn()
        try:
            cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
            rows = conn.execute("""
                SELECT j.* FROM jobs j
                WHERE j.match_tier IN (2, 3)
                AND j.first_seen >= ?
                AND j.status != 'dismissed'
                AND j.id NOT IN (
                    SELECT job_id FROM notifications WHERE channel = 'digest'
                )
                ORDER BY j.match_tier ASC, j.match_score DESC
            """, (cutoff,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_all_jobs(self, tier: Optional[int] = None,
                     company: Optional[str] = None,
                     status: Optional[str] = None,
                     starred_only: bool = False,
                     max_age_days: Optional[int] = None,
                     min_age_days: Optional[int] = None,
                     sort_by: str = "date",
                     search: Optional[str] = None,
                     limit: int = 200) -> List[Dict]:
        """Get all jobs with optional filters (for the web dashboard).

        Args:
            max_age_days: If set, only return jobs newer than this many days.
            min_age_days: If set, only return jobs older than this many days.
            sort_by: Sort order — 'date', 'score', 'company', 'title'.
            search: Search term to filter by title or company.
        """
        conn = self._get_conn()
        try:
            query = "SELECT * FROM jobs WHERE 1=1"
            params = []

            if tier is not None:
                query += " AND match_tier = ?"
                params.append(tier)
            if company:
                query += " AND company = ?"
                params.append(company)
            if status:
                query += " AND status = ?"
                params.append(status)
            if starred_only:
                query += " AND starred = 1"
            if max_age_days is not None:
                cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
                query += " AND first_seen >= ?"
                params.append(cutoff)
            if min_age_days is not None:
                cutoff = (datetime.utcnow() - timedelta(days=min_age_days)).isoformat()
                query += " AND first_seen < ?"
                params.append(cutoff)
            if search:
                query += " AND (title LIKE ? OR company LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])

            sort_map = {
                "date": "starred DESC, first_seen DESC",
                "score": "match_score DESC",
                "company": "company ASC, first_seen DESC",
                "title": "title ASC",
            }
            order = sort_map.get(sort_by, sort_map["date"])
            query += f" ORDER BY {order} LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_companies(self) -> List[str]:
        """Get list of distinct companies in the database."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT company FROM jobs ORDER BY company"
            ).fetchall()
            return [row["company"] for row in rows]
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Get summary statistics for the dashboard."""
        conn = self._get_conn()
        try:
            stats = {}
            for tier in [1, 2, 3]:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM jobs WHERE match_tier = ?",
                    (tier,)
                ).fetchone()
                stats[f"tier_{tier}"] = row["cnt"]

            row = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
            stats["total"] = row["cnt"]

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE status = 'applied'"
            ).fetchone()
            stats["applied"] = row["cnt"]

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE starred = 1"
            ).fetchone()
            stats["starred"] = row["cnt"]

            return stats
        finally:
            conn.close()

    def get_job_by_id(self, job_id: int) -> Optional[Dict]:
        """Get a single job by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
