# ─────────────────────────────────────────────
# Vidya Website Automation — Project Variables
# ─────────────────────────────────────────────

# ── 1. Environment & URL ────────────────────
BASE_URL = "https://vidya.online"
ENVIRONMENT = "staging"

# ── 2. Browser Config ───────────────────────
BROWSER = "chromium"
HEADLESS = False
DEFAULT_TIMEOUT = "20s"
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900

# ── 3. Paths ────────────────────────────────
SCREENSHOT_DIR = "screenshots"
REPORT_DIR = "reports"

# ── 4. Contact Form Test Data ───────────────
VALID_NAME = "Test User"
VALID_EMAIL = "testuser@example.com"
VALID_MESSAGE = "This is an automated test message submitted by Robot Framework."
INVALID_EMAIL = "notanemail"
EMPTY_STRING = ""

# ── 5. Expected Page Titles ─────────────────
HOME_PAGE_TITLE = "Vidya Online – AI-Powered Learning Platform | Courses in Hindi & Indian Languages"
PERSONALIZED_ROADMAP_PAGE_TITLE = "Personalized Roadmap | Features | Vidya Online | Vidya Online"
AI_TUTORING_PAGE_TITLE = "AI Tutoring | Features | Vidya Online | Vidya Online"
AI_VIDEO_LESSONS_PAGE_TITLE = "AI Video Lessons | Features | Vidya Online | Vidya Online"
EVALUATION_AND_READINESS_PAGE_TITLE = "Evaluation And Readiness | Features | Vidya Online | Vidya Online"
CAREER_OUTCOMES_PAGE_TITLE = "Career Outcomes | Features | Vidya Online | Vidya Online"
COURSES_PAGE_TITLE = "Vidya Online – Courses | Vidya Online"
STUDENTS_PAGE_TITLE = "Vidya Online – AI-Powered Learning for Students | Skill Building, Projects & Job Readiness | Vidya Online"
BUSINESS_PAGE_TITLE = "Vidya Online – For Businesses | Vidya Online"
UNIVERSITIES_PAGE_TITLE = "Vidya Online – For Universities | Vidya Online"
CONTACT_PAGE_TITLE = "Vidya Online – AI-Powered Learning Platform | Courses in Hindi & Indian Languages"
CAREER_PAGE_TITLE = "Career"

# ── 6. Wait / Retry Config ──────────────────
RETRY_COUNT = 3
POLL_INTERVAL = "0.5s"

# ── 7. Course Names (from TDD) ──────────────
COURSE_REACT = "React.js"
COURSE_NODE = "Node.js"
COURSE_WEBDEV = "Web Development"
COURSE_PYTHON = "Python Django"

# ── 8. File Upload Test Data ─────────────────
RESUME_VALID_PDF = "test_data/resume_valid.pdf"
RESUME_VALID_DOC = "test_data/resume_valid.doc"
RESUME_VALID_DOCX = "test_data/resume_valid.docx"
RESUME_OVERSIZED = "test_data/resume_oversized_11mb.pdf"
RESUME_INVALID_PNG = "test_data/invalid_resume.png"
RESUME_VALID_PNG = "test_data/resume_valid.png"
RESUME_VALID_JPG = "test_data/resume_valid.jpg"
FILE_SIZE_LIMIT_MB = 10
