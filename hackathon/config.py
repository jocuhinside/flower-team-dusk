"""Model endpoint configuration without secret disclosure.

Reads the sponsor variables first and falls back to the Flower runtime's own
variable names, so the same app works locally (.env) and on a configured SuperNode:

* On SuperGrid the runtime injects FLWR_RUNTIME_BASE_URL and FLWR_RUNTIME_API_KEY
  into the AgentApp process; those are used as-is and win when present (Flower's
  docs say to pass them to the OpenAI client unmodified).
* Otherwise: key SPONSOR_API_KEY or FLWR_MODEL_API_KEY; endpoint SPONSOR_BASE_URL
  or FLWR_MODEL_API_ENDPOINT.
* model: AGENT_MODEL or SPONSOR_MODEL (required). The event post has no separate
  model variable; use the Flower-published model string, e.g. the Endeavor 1.0
  identifier once Flower confirms it, or a Nebius ID for local runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

RUNTIME_KEY_VAR = "FLWR_RUNTIME_API_KEY"
RUNTIME_URL_VAR = "FLWR_RUNTIME_BASE_URL"
KEY_VARS = ("SPONSOR_API_KEY", "FLWR_MODEL_API_KEY")
URL_VARS = ("SPONSOR_BASE_URL", "FLWR_MODEL_API_ENDPOINT")
MODEL_VARS = ("AGENT_MODEL", "SPONSOR_MODEL")
# Used when no model variable is set, e.g. inside a SuperGrid run where .env does not exist.
# Override with AGENT_MODEL. Fallbacks if this stalls on SuperGrid: openai/gpt-5.6-terra, then
# openai/gpt-5.6-sol. Not flwrlabs/endeavor-1.0: its providers returned 502 on SuperGrid.
DEFAULT_MODEL = "openai/gpt-5.6-sol"


class ConfigurationError(RuntimeError):
    pass


def _first(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


@dataclass(frozen=True)
class SponsorConfig:
    base_url: str
    model: str
    api_key: str
    runtime: bool = False

    @classmethod
    def from_environment(cls) -> SponsorConfig:
        model = _first(MODEL_VARS) or DEFAULT_MODEL
        runtime_key = os.environ.get(RUNTIME_KEY_VAR, "").strip()
        runtime_url = os.environ.get(RUNTIME_URL_VAR, "").strip()
        if runtime_key and runtime_url:
            if model is None:
                raise ConfigurationError("missing required variables: " + " or ".join(MODEL_VARS))
            # Runtime values are passed through unmodified, as Flower's docs require.
            return cls(base_url=runtime_url, model=model, api_key=runtime_key, runtime=True)
        api_key, base_url = _first(KEY_VARS), _first(URL_VARS)
        missing = [
            " or ".join(names)
            for names, value in ((KEY_VARS, api_key), (URL_VARS, base_url), (MODEL_VARS, model))
            if value is None
        ]
        if missing:
            raise ConfigurationError("missing required variables: " + ", ".join(missing))
        base_url = base_url.rstrip("/")
        if base_url.endswith("/responses"):  # the OpenAI SDK appends /responses itself
            base_url = base_url[: -len("/responses")]
        return cls(base_url=base_url, model=model, api_key=api_key)


def _runtime_present() -> bool:
    return bool(
        os.environ.get(RUNTIME_KEY_VAR, "").strip() and os.environ.get(RUNTIME_URL_VAR, "").strip()
    )


def status() -> dict[str, bool]:
    """Return presence only (key, endpoint, model), never credential values.

    Key and endpoint count as present when the SuperGrid runtime variables are set.
    """
    runtime = _runtime_present()
    return {
        "SPONSOR_API_KEY": runtime or _first(KEY_VARS) is not None,
        "SPONSOR_BASE_URL": runtime or _first(URL_VARS) is not None,
        "SPONSOR_MODEL": _first(MODEL_VARS) is not None,
    }
