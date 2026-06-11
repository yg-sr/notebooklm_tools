"""Self-built uploader for personal Google NotebookLM (CLI + library + MCP)."""

from .api import NotebookLM, NotebookLMSync
from .client import NblmClient, Notebook, NotebookLMError, Source

__version__ = "0.1.0"

__all__ = [
    "NotebookLM",
    "NotebookLMSync",
    "NblmClient",
    "Notebook",
    "Source",
    "NotebookLMError",
]
