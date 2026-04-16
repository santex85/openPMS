"""Resend email integration (API client + HTML templates)."""

from app.integrations.resend.client import ResendNotConfiguredError, send_email
from app.integrations.resend.renderer import render_email

__all__ = [
    "ResendNotConfiguredError",
    "render_email",
    "send_email",
]
