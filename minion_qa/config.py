"""
MinionQA Configuration Module.

Coordinates settings for OpenMinion agent runtime, Cortensor provider,
and Playwright browser execution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Automatically load environment variables from .env file
load_dotenv()


@dataclass
class MinionQAConfig:
    """Runtime configuration for MinionQA autonomous agent."""

    # Browser Execution
    headless: bool = True
    timeout_ms: int = 15000
    viewport_width: int = 1280
    viewport_height: int = 800
    screenshot_dir: Path = field(default_factory=lambda: Path("./screenshots"))
    reports_dir: Path = field(default_factory=lambda: Path("./reports"))

    # Provider & Model Settings (Cortensor / OpenRouter / OpenAI)
    provider: str = field(
        default_factory=lambda: os.getenv("MINIONQA_PROVIDER", "openrouter")
    )
    model: str = field(
        default_factory=lambda: os.getenv("MINIONQA_MODEL", "openai/gpt-4o-mini")
    )
    openrouter_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY")
    )
    openai_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY")
    )
    cortensor_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("CORTENSOR_API_KEY")
    )
    cortensor_base_url: str = field(
        default_factory=lambda: os.getenv(
            "CORTENSOR_BASE_URL", "http://127.0.0.1:8080/api/v2/completions"
        )
    )
    cortensor_portal_url: str = field(
        default_factory=lambda: os.getenv(
            "CORTENSOR_PORTAL_URL", "https://api.cortensor.app/v1"
        )
    )

    # OpenMinion Agent Runtime
    session_id: Optional[str] = None
    enable_policy_gating: bool = True
    max_steps_per_run: int = 50
    verbose: bool = True

    def __post_init__(self) -> None:
        self.screenshot_dir = Path(self.screenshot_dir).resolve()
        self.reports_dir = Path(self.reports_dir).resolve()
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


default_config = MinionQAConfig()
