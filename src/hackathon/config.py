"""Sponsor API configuration without secret disclosure."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SponsorConfig:
    base_url: str
    model: str
    api_key: str

    @classmethod
    def from_environment(cls) -> SponsorConfig:
        missing = [
            name
            for name in ("SPONSOR_API_KEY", "SPONSOR_BASE_URL", "SPONSOR_MODEL")
            if not os.environ.get(name, "").strip()
        ]
        if missing:
            raise ConfigurationError("missing required variables: " + ", ".join(missing))
        return cls(
            base_url=os.environ["SPONSOR_BASE_URL"].rstrip("/"),
            model=os.environ["SPONSOR_MODEL"],
            api_key=os.environ["SPONSOR_API_KEY"],
        )


def status() -> dict[str, bool]:
    """Return presence only, never credential values."""
    return {
        name: bool(os.environ.get(name, "").strip())
        for name in ("SPONSOR_API_KEY", "SPONSOR_BASE_URL", "SPONSOR_MODEL")
    }
