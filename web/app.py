#!/usr/bin/env python3
"""
Job Tracker Alert — Web Dashboard

Flask app with Google OAuth authentication.
Run: python web/app.py
Visit: http://localhost:8080
"""

import os
import sys
import secrets
import logging
from pathlib import Path
from functools import wraps
from datetime import timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from src.database import JobDatabase
import yaml

app = Flask(__name__)

# Load config and init DB
with open(PROJECT_ROOT / "config.yaml") as f:
    config = yaml.safe_load(f)

db_path = str(PROJECT_ROOT / config["settings"]["database_path"])
db = JobDatabase(db_path)

# ---------------------------------------------------------------------------
# Session + OAuth setup
# ---------------------------------------------------------------------------

app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(hours=24)

ALLOWED_EMAILS = os.getenv("ALLOWED_EMAILS", "").split(",") if os.getenv("ALLOWED_EMAILS") else []

# Only initialize OAuth if credentials are configured
_oauth_enabled = bool(os.getenv("GOOGLE_CLIENT_ID"))
oauth = None
google_oauth = None

if _oauth_enabled:
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    google_oauth = oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def login_required(f):
    """Decorator to require Google login. Passes through if OAuth not configured."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _oauth_enabled:
            return f(*args, **kwargs)
        user = session.get("user")
        if not user:
            return redirect(url_for("login_page"))
        if user.get("email") not in ALLOWED_EMAILS:
            return "Access denied — your email is not authorized.", 403
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login")
def login_page():
    if not _oauth_enabled:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login/google")
def login_google():
    if not _oauth_enabled:
        return redirect(url_for("dashboard"))
    redirect_uri = url_for("authorize", _external=True)
    return google_oauth.authorize_redirect(redirect_uri)


@app.route("/authorize")
def authorize():
    if not _oauth_enabled:
        return redirect(url_for("dashboard"))
    token = google_oauth.authorize_access_token()
    user_info = token.get("userinfo")
    if not user_info or user_info.get("email") not in ALLOWED_EMAILS:
        return "Access denied — your email is not authorized.", 403
    session.permanent = True
    session["user"] = {"email": user_info["email"], "name": user_info.get("name", "")}
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------

def _get_filters():
    """Extract common filter params from request."""
    return {
        "tier": request.args.get("tier", type=int),
        "company": request.args.get("company"),
        "status": request.args.get("status"),
        "starred_only": request.args.get("starred") == "1",
        "sort_by": request.args.get("sort", "date"),
        "search": request.args.get("search"),
    }


@app.route("/")
@login_required
def dashboard():
    """Main dashboard showing fresh jobs (last 3 days)."""
    f = _get_filters()
    jobs = db.get_all_jobs(
        tier=f["tier"], company=f["company"], status=f["status"],
        starred_only=f["starred_only"], max_age_days=3,
        sort_by=f["sort_by"], search=f["search"],
    )
    stats = db.get_stats()
    companies = db.get_companies()
    user = session.get("user")

    return render_template(
        "dashboard.html", jobs=jobs, stats=stats, companies=companies,
        current_tier=f["tier"], current_company=f["company"],
        current_status=f["status"], current_starred=f["starred_only"],
        current_sort=f["sort_by"], current_search=f["search"] or "",
        page="fresh", user=user,
    )


@app.route("/archive")
@login_required
def archive():
    """Archive page showing jobs older than 3 days."""
    f = _get_filters()
    jobs = db.get_all_jobs(
        tier=f["tier"], company=f["company"], status=f["status"],
        starred_only=f["starred_only"], min_age_days=3,
        sort_by=f["sort_by"], search=f["search"],
    )
    stats = db.get_stats()
    companies = db.get_companies()
    user = session.get("user")

    return render_template(
        "dashboard.html", jobs=jobs, stats=stats, companies=companies,
        current_tier=f["tier"], current_company=f["company"],
        current_status=f["status"], current_starred=f["starred_only"],
        current_sort=f["sort_by"], current_search=f["search"] or "",
        page="archive", user=user,
    )


@app.route("/update-status/<int:job_id>", methods=["POST"])
@login_required
def update_status(job_id):
    """Update a job's status (applied, dismissed, new)."""
    new_status = request.form.get("status", "new")
    db.update_status(job_id, new_status)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/toggle-star/<int:job_id>", methods=["POST"])
@login_required
def toggle_star(job_id):
    """Toggle the starred state of a job."""
    db.toggle_star(job_id)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/jobs")
@login_required
def api_jobs():
    """JSON API for jobs (for future integrations)."""
    tier = request.args.get("tier", type=int)
    company = request.args.get("company")
    jobs = db.get_all_jobs(tier=tier, company=company)
    return jsonify(jobs)


@app.route("/api/stats")
@login_required
def api_stats():
    """JSON API for dashboard stats."""
    return jsonify(db.get_stats())


# ---------------------------------------------------------------------------
# Cover Letter Generator
# ---------------------------------------------------------------------------

COVER_LETTER_PROMPT = """You are an expert career coach writing a cover letter for a senior Product Manager candidate.

RULES:
- Write a compelling, professional cover letter (300-400 words)
- Address it to "Dear Hiring Manager,"
- Opening paragraph: Hook the reader — connect the candidate's passion/experience to the company's mission and this specific role
- Middle paragraph(s): Highlight 2-3 specific achievements from the resume that directly map to the job requirements. Use concrete metrics and outcomes where available. Orient the candidate's skills toward what THIS role needs.
- Closing paragraph: Express enthusiasm, suggest next steps, and end confidently
- Sign off as the candidate (use the name from the resume)
- Tone: Confident but not arrogant, professional but warm, specific not generic
- Do NOT use clichés like "I am writing to express my interest" or "I believe I would be a great fit"
- Start with something that shows genuine understanding of the company/role
- Make the recruiter WANT to schedule an interview

Return ONLY the cover letter text — no extra commentary, no subject line, no JSON wrapping."""

COVER_LETTER_USER_TEMPLATE = """CANDIDATE RESUME:
{resume}

---

TARGET JOB:
Company: {company}
Title: {title}
Location: {location}
Description:
{description}

---

Write a tailored cover letter for this role."""


def _load_resume():
    """Load the candidate resume text."""
    resume_path = PROJECT_ROOT / config.get("candidate", {}).get("resume_path", "resume.txt")
    if resume_path.exists():
        return resume_path.read_text()
    return ""


def _extract_candidate_name():
    """Extract the candidate's name from the first line of resume.txt."""
    resume = _load_resume()
    if resume:
        first_line = resume.strip().split("\n")[0].strip()
        if first_line and len(first_line) < 60:
            return first_line
    return "Candidate"


def _generate_cover_letter(resume: str, job: dict) -> str:
    """Generate a cover letter using Gemini (primary) or Anthropic (fallback)."""
    logger = logging.getLogger(__name__)

    user_prompt = COVER_LETTER_USER_TEMPLATE.format(
        resume=resume,
        company=job.get("company", ""),
        title=job.get("title", ""),
        location=job.get("location", ""),
        description=(job.get("description", "") or "")[:3000],
    )

    # Try Gemini first (free for single requests, no rate limit concern)
    google_key = os.getenv("GOOGLE_API_KEY")
    if google_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=google_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=COVER_LETTER_PROMPT,
                    max_output_tokens=1000,
                    temperature=0.5,
                ),
            )
            return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini cover letter generation failed: {e}")

    # Fallback to Anthropic
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=COVER_LETTER_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.5,
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.warning(f"Anthropic cover letter generation failed: {e}")

    return "Error: Could not generate cover letter — no API keys available."


# ---------------------------------------------------------------------------
# Resume Upload
# ---------------------------------------------------------------------------

@app.route("/upload-resume", methods=["POST"])
@login_required
def upload_resume():
    """Upload a new resume file (.txt or .pdf, max 5MB)."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".txt", ".pdf"):
        return jsonify({"error": "Only .txt and .pdf files are supported"}), 400

    # Check file size (5MB max)
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({"error": "File too large (max 5MB)"}), 400

    try:
        if ext == ".txt":
            text = f.read().decode("utf-8")
        else:
            import pdfplumber
            import io
            pdf_bytes = f.read()
            text_parts = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            text = "\n\n".join(text_parts)

        if not text.strip():
            return jsonify({"error": "Could not extract text from file"}), 400

        resume_path = PROJECT_ROOT / config.get("settings", {}).get("resume_path", "resume.txt")
        resume_path.write_text(text, encoding="utf-8")

        return jsonify({"success": True, "filename": f.filename, "size": len(text)})
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {str(e)}"}), 500


@app.route("/api/resume-info")
@login_required
def resume_info():
    """Return info about the current resume file."""
    resume_path = PROJECT_ROOT / config.get("settings", {}).get("resume_path", "resume.txt")
    if not resume_path.exists():
        return jsonify({"exists": False})

    text = resume_path.read_text(encoding="utf-8")
    return jsonify({
        "exists": True,
        "filename": resume_path.name,
        "size": len(text),
        "preview": text[:500],
    })


@app.route("/api/cover-letter/<int:job_id>", methods=["POST"])
@login_required
def generate_cover_letter(job_id):
    """Generate a cover letter on demand for a specific job."""
    job = db.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    resume = _load_resume()
    if not resume:
        return jsonify({"error": "Resume not found"}), 500

    cover_letter = _generate_cover_letter(resume, job)
    return jsonify({"cover_letter": cover_letter, "job_title": job.get("title", ""), "company": job.get("company", "")})


@app.route("/api/cover-letter-download/<int:job_id>", methods=["POST"])
@login_required
def download_cover_letter(job_id):
    """Download a cover letter as PDF or DOCX."""
    from flask import send_file
    import io
    import tempfile

    fmt = request.args.get("format", "pdf")
    content = request.form.get("content", "")
    company = request.form.get("company", "Company")
    title = request.form.get("title", "Role")

    if not content:
        return jsonify({"error": "No cover letter content"}), 400

    safe_company = "".join(c for c in company if c.isalnum() or c in " -_")
    filename = f"Cover_Letter_{safe_company}"

    if fmt == "docx":
        return _generate_docx(content, company, title, filename)
    else:
        return _generate_pdf(content, company, title, filename)


def _generate_pdf(content: str, company: str, title: str, filename: str):
    """Generate a PDF cover letter using reportlab."""
    from flask import send_file
    import io

    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=72, bottomMargin=72,
                            leftMargin=72, rightMargin=72)
    styles = getSampleStyleSheet()

    # Custom styles
    name_style = ParagraphStyle("Name", parent=styles["Normal"],
                                fontSize=16, spaceAfter=4,
                                fontName="Helvetica-Bold")
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"],
                                    fontSize=10, textColor="#666666",
                                    spaceAfter=20)
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
                                fontSize=11, leading=18,
                                spaceAfter=12, alignment=TA_LEFT)

    story = []
    candidate_name = _extract_candidate_name()
    story.append(Paragraph(candidate_name, name_style))
    story.append(Paragraph(f"Cover Letter for {title} at {company}", subtitle_style))
    story.append(Spacer(1, 12))

    # Split content into paragraphs and add each
    for para in content.split("\n\n"):
        para = para.strip()
        if para:
            # Escape HTML special chars for reportlab
            para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(para, body_style))

    doc.build(story)
    buf.seek(0)

    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"{filename}.pdf")


def _generate_docx(content: str, company: str, title: str, filename: str):
    """Generate a DOCX cover letter using python-docx."""
    from flask import send_file
    import io
    import subprocess
    import tempfile

    # Create docx using docx-js via Node.js
    js_code = _build_docx_js(content, company, title)

    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(js_code)
        js_path = f.name

    out_path = js_path.replace(".js", ".docx")
    try:
        result = subprocess.run(["node", js_path], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            # Fallback: use simple text file approach
            return _generate_docx_simple(content, company, title, filename)

        with open(out_path, "rb") as f:
            buf = io.BytesIO(f.read())

        return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         as_attachment=True,
                         download_name=f"{filename}.docx")
    except Exception:
        return _generate_docx_simple(content, company, title, filename)
    finally:
        import os as _os
        for p in [js_path, out_path]:
            try:
                _os.unlink(p)
            except OSError:
                pass


def _generate_docx_simple(content: str, company: str, title: str, filename: str):
    """Fallback DOCX generation using python-docx if available, or plain text."""
    from flask import send_file
    import io

    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Inches

        doc = DocxDocument()

        # Set margins
        for section in doc.sections:
            section.top_margin = Inches(1)
            section.bottom_margin = Inches(1)
            section.left_margin = Inches(1)
            section.right_margin = Inches(1)

        # Name
        name_para = doc.add_paragraph()
        candidate_name = _extract_candidate_name()
        name_run = name_para.add_run(candidate_name)
        name_run.bold = True
        name_run.font.size = Pt(16)

        # Subtitle
        sub_para = doc.add_paragraph()
        sub_run = sub_para.add_run(f"Cover Letter for {title} at {company}")
        sub_run.font.size = Pt(10)
        sub_run.font.color.rgb = None  # default

        doc.add_paragraph()  # spacer

        # Body paragraphs
        for para in content.split("\n\n"):
            para = para.strip()
            if para:
                p = doc.add_paragraph(para)
                for run in p.runs:
                    run.font.size = Pt(11)

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)

        return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         as_attachment=True,
                         download_name=f"{filename}.docx")
    except ImportError:
        # Last resort: send as plain text
        buf = io.BytesIO(content.encode("utf-8"))
        return send_file(buf, mimetype="text/plain",
                         as_attachment=True,
                         download_name=f"{filename}.txt")


def _build_docx_js(content: str, company: str, title: str) -> str:
    """Build a Node.js script to generate the DOCX."""
    import json
    import tempfile

    out_path = tempfile.mktemp(suffix=".docx")
    escaped_content = json.dumps(content)
    escaped_company = json.dumps(company)
    escaped_title = json.dumps(title)
    escaped_name = json.dumps(_extract_candidate_name())

    return f'''
const {{ Document, Packer, Paragraph, TextRun, AlignmentType }} = require("docx");
const fs = require("fs");

const content = {escaped_content};
const company = {escaped_company};
const title = {escaped_title};
const candidateName = {escaped_name};

const paragraphs = content.split("\\n\\n").filter(p => p.trim()).map(p =>
    new Paragraph({{
        children: [new TextRun({{ text: p.trim(), font: "Arial", size: 22 }})],
        spacing: {{ after: 240 }},
    }})
);

const doc = new Document({{
    styles: {{
        default: {{ document: {{ run: {{ font: "Arial", size: 22 }} }} }},
    }},
    sections: [{{
        properties: {{
            page: {{
                size: {{ width: 12240, height: 15840 }},
                margin: {{ top: 1440, right: 1440, bottom: 1440, left: 1440 }},
            }},
        }},
        children: [
            new Paragraph({{
                children: [new TextRun({{ text: candidateName, bold: true, font: "Arial", size: 32 }})],
                spacing: {{ after: 80 }},
            }}),
            new Paragraph({{
                children: [new TextRun({{ text: "Cover Letter for " + title + " at " + company, font: "Arial", size: 20, color: "666666" }})],
                spacing: {{ after: 400 }},
            }}),
            ...paragraphs,
        ],
    }}],
}});

Packer.toBuffer(doc).then(buffer => {{
    fs.writeFileSync("{out_path}", buffer);
    process.exit(0);
}}).catch(err => {{
    console.error(err);
    process.exit(1);
}});
'''


if __name__ == "__main__":
    print(f"\n  Job Tracker Dashboard running at http://localhost:8080\n")
    app.run(debug=True, port=8080, host="0.0.0.0")
