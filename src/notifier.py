"""
Notification system: Telegram + Email.
Handles instant alerts (Tier 1) and daily digests (Tier 2).
"""

import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send notifications via Telegram bot."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_tier1_alert(self, job: Dict[str, Any]) -> bool:
        """Send an instant alert for a Tier 1 (close match) job."""
        score_pct = int((job.get("match_score", 0) or 0) * 100)

        message = (
            f"🎯 *INSTANT JOB ALERT*\n\n"
            f"*{self._escape_md(job['title'])}*\n"
            f"🏢 {self._escape_md(job['company'])}\n"
            f"📍 {self._escape_md(job.get('location', 'N/A'))}\n\n"
            f"Match: {score_pct}% confidence\n"
            f"💡 {self._escape_md(job.get('match_reason', ''))}\n\n"
            f"[🔗 Apply Now]({job['url']})"
        )

        return self._send(message)

    def send_test_message(self) -> bool:
        """Send a test message to verify the bot is working."""
        message = (
            "✅ *Job Tracker Alert — Connected\\!*\n\n"
            "Your Telegram notifications are set up correctly\\.\n"
            "You'll receive instant alerts here for Tier 1 \\(close match\\) jobs\\."
        )
        return self._send(message)

    def _send(self, message: str) -> bool:
        """Send a message via Telegram API."""
        try:
            response = requests.post(self.api_url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": False
            }, timeout=10)

            if response.status_code == 200:
                logger.info("Telegram message sent successfully")
                return True
            else:
                # Fallback: try without markdown if formatting fails
                logger.warning(f"Telegram MarkdownV2 failed ({response.status_code}), trying plain text")
                response2 = requests.post(self.api_url, json={
                    "chat_id": self.chat_id,
                    "text": message.replace("*", "").replace("\\", ""),
                    "disable_web_page_preview": False
                }, timeout=10)
                if response2.status_code == 200:
                    return True
                logger.error(f"Telegram send failed: {response2.text}")
                return False

        except requests.RequestException as e:
            logger.error(f"Telegram request error: {e}")
            return False

    @staticmethod
    def _escape_md(text: str) -> str:
        """Escape special characters for Telegram MarkdownV2."""
        if not text:
            return ""
        special_chars = ['_', '[', ']', '(', ')', '~', '`', '>', '#',
                         '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = text.replace(char, f"\\{char}")
        return text


class EmailNotifier:
    """Send notifications via Gmail SMTP."""

    def __init__(self, gmail_address: str, app_password: str,
                 recipient: str, smtp_server: str = "smtp.gmail.com",
                 smtp_port: int = 587):
        self.gmail_address = gmail_address
        self.app_password = app_password
        self.recipient = recipient
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port

    def send_tier1_alert(self, job: Dict[str, Any]) -> bool:
        """Send an instant email alert for a Tier 1 job."""
        score_pct = int((job.get("match_score", 0) or 0) * 100)
        subject = f"🎯 INSTANT: {job['title']} at {job['company']}"

        html_body = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px 10px 0 0;">
                <h2 style="margin: 0;">🎯 Close Match Found!</h2>
                <p style="margin: 5px 0 0 0; opacity: 0.9;">This job closely matches your profile</p>
            </div>

            <div style="border: 1px solid #e0e0e0; border-top: none; padding: 20px; border-radius: 0 0 10px 10px;">
                <h3 style="color: #333; margin-top: 0;">{job['title']}</h3>
                <p style="color: #666; margin: 5px 0;">
                    🏢 <strong>{job['company']}</strong> &nbsp;|&nbsp;
                    📍 {job.get('location', 'N/A')} &nbsp;|&nbsp;
                    📊 {score_pct}% match
                </p>

                <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 15px 0;">
                    <strong>Why this matches:</strong><br>
                    {job.get('match_reason', 'High relevance to your profile')}
                </div>

                <a href="{job['url']}" style="display: inline-block; background: #667eea; color: white; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 10px;">
                    Apply Now →
                </a>

                <p style="color: #999; font-size: 12px; margin-top: 20px;">
                    Detected: {datetime.now().strftime('%B %d, %Y at %I:%M %p')} PT<br>
                    Sent by Job Tracker Alert
                </p>
            </div>
        </div>
        """

        return self._send_email(subject, html_body)

    def send_digest(self, jobs: List[Dict[str, Any]]) -> bool:
        """Send a daily digest email with Tier 2 (and optionally Tier 3) jobs."""
        if not jobs:
            logger.info("No jobs for digest, skipping email")
            return True

        subject = f"📋 Job Digest — {datetime.now().strftime('%B %d, %Y')} ({len(jobs)} new roles)"

        tier2_jobs = [j for j in jobs if j.get("match_tier") == 2]
        tier3_jobs = [j for j in jobs if j.get("match_tier") == 3]

        job_rows = ""
        for job in tier2_jobs + tier3_jobs:
            tier_label = "Related" if job.get("match_tier") == 2 else "Low Match"
            tier_color = "#f0ad4e" if job.get("match_tier") == 2 else "#999"
            score_pct = int((job.get("match_score", 0) or 0) * 100)

            job_rows += f"""
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #eee;">
                    <strong><a href="{job['url']}" style="color: #333; text-decoration: none;">{job['title']}</a></strong><br>
                    <span style="color: #666; font-size: 13px;">{job['company']} • {job.get('location', 'N/A')}</span>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">
                    <span style="background: {tier_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px;">
                        {tier_label}
                    </span>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">{score_pct}%</td>
                <td style="padding: 12px; border-bottom: 1px solid #eee;">
                    <a href="{job['url']}" style="color: #667eea;">Apply →</a>
                </td>
            </tr>
            """

        html_body = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
            <div style="background: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
                <h2 style="margin: 0; color: #333;">📋 Daily Job Digest</h2>
                <p style="margin: 5px 0 0 0; color: #666;">
                    {len(tier2_jobs)} related matches, {len(tier3_jobs)} other roles — {datetime.now().strftime('%B %d, %Y')}
                </p>
            </div>

            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: #f0f0f0;">
                        <th style="padding: 10px; text-align: left;">Role</th>
                        <th style="padding: 10px; text-align: center;">Match</th>
                        <th style="padding: 10px; text-align: center;">Score</th>
                        <th style="padding: 10px; text-align: center;">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {job_rows}
                </tbody>
            </table>

            <p style="color: #999; font-size: 12px; margin-top: 20px; text-align: center;">
                Sent by Job Tracker Alert • <a href="http://localhost:5000" style="color: #667eea;">View Dashboard</a>
            </p>
        </div>
        """

        return self._send_email(subject, html_body)

    def send_test_email(self) -> bool:
        """Send a test email to verify SMTP is working."""
        subject = "✅ Job Tracker Alert — Email Connected!"
        html_body = """
        <div style="font-family: -apple-system, sans-serif; padding: 20px;">
            <h2>✅ Email notifications are set up!</h2>
            <p>You'll receive:</p>
            <ul>
                <li><strong>Instant alerts</strong> for Tier 1 (close match) jobs</li>
                <li><strong>Daily digest</strong> at 9 AM with Tier 2 (related) jobs</li>
            </ul>
            <p style="color: #666;">Sent by Job Tracker Alert</p>
        </div>
        """
        return self._send_email(subject, html_body)

    def _send_email(self, subject: str, html_body: str) -> bool:
        """Send an HTML email via Gmail SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.gmail_address
            msg["To"] = self.recipient
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)

            logger.info(f"Email sent: {subject}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("Gmail SMTP auth failed. Check your app password and ensure 2FA is enabled.")
            return False
        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False
