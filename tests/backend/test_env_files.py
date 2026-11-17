"""Regression (found by the Docker end-to-end test).

Docker Compose and python-dotenv disagree on `KEY=   # comment`: Python reads an empty value,
Compose reads the comment text as the value. Inside Docker the backend then believed its
WhatsApp phone number id was "# from Meta App ..." and ignored every incoming message.
Rule: comments go on their own line, never after a value.
"""
import re

from app.config import PROJECT_ROOT

ASSIGNMENT_WITH_INLINE_COMMENT = re.compile(r"^\s*[A-Z0-9_]+=[^\"']*\s#")


def test_env_example_has_no_inline_comments():
    bad = [line for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
           if ASSIGNMENT_WITH_INLINE_COMMENT.match(line)]
    assert bad == [], f"Move these comments to their own line: {bad}"


def test_env_example_lists_every_setting_docker_needs():
    names = {line.split("=", 1)[0] for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
             if re.match(r"^[A-Z0-9_]+=", line)}
    required = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "JWT_SECRET", "ADMIN_EMAIL", "ADMIN_PASSWORD",
                "WA_MOCK_MODE", "WA_VERIFY_TOKEN", "BACKEND_INTERNAL_API_KEY", "N8N_ENCRYPTION_KEY"}
    assert required <= names, f"missing: {required - names}"
