"""Prompt injection detection for MedExpert user inputs.

Heuristic regex-based detector that flags common prompt injection
patterns.  Returns a structured result with safety verdict and flags.

This is a *detector*, not a sanitizer — callers decide what to do
with flagged input (block, redact, or proceed with caution).
"""

import logging
import re
from typing import Optional

log = logging.getLogger("medexpert.input_guard")

# ---------------------------------------------------------------------------
# Injection patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_instructions", re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above|your)[\s\w]{0,30}"
        r"(instructions?|rules?|prompts?|context)",
        re.IGNORECASE,
    )),
    ("override_system", re.compile(
        r"(you\s+are\s+now|act\s+as|pretend\s+(to\s+be|you\s+are)"
        r"|your\s+new\s+(role|identity|instructions?))",
        re.IGNORECASE,
    )),
    ("system_prompt_leak", re.compile(
        r"(show|reveal|print|output|repeat|display)\s+(me\s+)?"
        r"(your|the)\s+(system\s+prompt|instructions?|rules?|hidden\s+prompt)",
        re.IGNORECASE,
    )),
    ("jailbreak_delimiter", re.compile(
        r"(```\s*system|<\|im_start\|>|<\|system\|>|\[INST\]|\[/INST\]|<<SYS>>)",
        re.IGNORECASE,
    )),
    ("role_hijack", re.compile(
        r"(from\s+now\s+on|henceforth|starting\s+now)"
        r".{0,30}(ignore|forget|disregard|override)",
        re.IGNORECASE,
    )),
    ("data_exfil", re.compile(
        r"(send|post|fetch|curl|wget|http)\s+.{0,50}"
        r"(api|endpoint|webhook|server)",
        re.IGNORECASE,
    )),
    ("encoding_evasion", re.compile(
        r"(base64|rot13|hex)\s*(encode|decode|convert)",
        re.IGNORECASE,
    )),
]

MAX_INPUT_LENGTH = 50_000


def check_input(
    text: str,
    extra_patterns: Optional[list[tuple[str, re.Pattern]]] = None,
) -> dict:
    """Scan user input for prompt injection patterns.

    Returns
    -------
    dict
        ``safe`` (bool), ``flags`` (list[str]), ``matched_snippets``,
        ``truncated`` (bool), ``original_length`` (int).
    """
    if not text:
        return {
            "safe": True,
            "flags": [],
            "matched_snippets": [],
            "truncated": False,
            "original_length": 0,
        }

    original_length = len(text)
    truncated = original_length > MAX_INPUT_LENGTH
    scan_text = text[:MAX_INPUT_LENGTH] if truncated else text

    flags: list[str] = []
    snippets: list[str] = []

    patterns = list(_INJECTION_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)

    for name, pattern in patterns:
        match = pattern.search(scan_text)
        if match:
            flags.append(name)
            snippets.append(match.group()[:100])

    safe = len(flags) == 0

    if not safe:
        log.warning(
            "Prompt injection detected: flags=%s length=%d",
            flags,
            original_length,
        )

    return {
        "safe": safe,
        "flags": flags,
        "matched_snippets": snippets,
        "truncated": truncated,
        "original_length": original_length,
    }
