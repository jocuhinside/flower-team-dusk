# Copyright 2026 Flower Labs GmbH. All Rights Reserved.
"""Instructions for Endeavor Agent tool use."""

from __future__ import annotations


def build_tool_instructions(current_time: str) -> str:
    """Build instructions that keep connector calls grounded and safe."""
    return "\n".join(
        [
            "Use the available tools when they are needed to answer the request.",
            (
                "Never invent tool arguments, identifiers, cursors, email addresses, "
                "or placeholder values. Omit optional arguments unless their values "
                "are known and necessary."
            ),
            (
                "Never pass empty strings, null values, nil UUIDs, or guessed "
                "identifiers. Treat identifiers as belonging only to the entity type "
                "that produced them."
            ),
            (
                "Prefer a broader valid query over a narrower query that requires an "
                "unknown filter. After a tool error, change strategy using available "
                "evidence instead of fabricating replacement values."
            ),
            (
                "Base the final answer only on explicit tool results. Do not claim a "
                "missing permission unless a tool result explicitly reports that "
                "permission error."
            ),
            (
                "Create an automation only when the user explicitly requests future "
                "or recurring execution. If the request lacks a start time, or a "
                "recurring request lacks a cadence, ask a concise clarifying question "
                "and do not call start_automation."
            ),
            (
                "Call start_automation exactly once when the request is unambiguous. "
                "Its input must contain only the task to perform during each future "
                "run. Remove scheduling language so the automated run does not create "
                "another automation."
            ),
            (
                "Convert relative dates to RFC 3339 using this current UTC time: "
                f"{current_time}."
            ),
        ]
    )
