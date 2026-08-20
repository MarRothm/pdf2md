"""Offline Docling PDF-to-Markdown web service."""

# The single source of truth for the version. `pyproject.toml` reads it from here via
# hatchling, and `/api/health` and the startup log report it. It used to be a second
# literal that nobody bumped: the 1.2.0 image reported itself as 1.0.0.
__version__ = "1.7.0"
