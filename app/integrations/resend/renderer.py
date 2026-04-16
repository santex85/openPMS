"""Jinja2 renderer for HTML email templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_templates_dir = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_templates_dir),
    autoescape=select_autoescape(["html", "htm"]),
)


def render_email(template_name: str, context: dict[str, object]) -> str:
    tmpl = _env.get_template(template_name)
    return tmpl.render(**context)
