"""PHI/PII Redactor — detects and redacts protected health information.

Uses Presidio analyzer/anonymizer when available (optional dependency),
falls back to regex-based detection for common PHI patterns.
"""

import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

# Regex patterns for common PHI/PII types
PHI_PATTERNS = {
    "SSN": r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
    "PHONE": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "EMAIL": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "MRN": r"\b(?:MRN|mrn|Medical Record)[\s:#]*\d{4,12}\b",
    "DOB": r"\b(?:DOB|dob|Date of Birth|born)[\s:]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
    "DATE": r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    "IP_ADDRESS": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    "CREDIT_CARD": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
}

# Name patterns (capitalized word sequences that look like names)
NAME_PATTERN = r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Patient)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b"

ALL_ENTITY_TYPES = list(PHI_PATTERNS.keys()) + ["PERSON"]


def _detect_with_regex(text: str, entity_types: list[str] | None = None) -> list[dict]:
    """Detect PHI entities using regex patterns."""
    entities = []
    types_to_check = entity_types or ALL_ENTITY_TYPES

    for entity_type in types_to_check:
        if entity_type == "PERSON":
            for match in re.finditer(NAME_PATTERN, text):
                entities.append({
                    "type": "PERSON",
                    "value": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                    "score": 0.7,
                })
            continue

        pattern = PHI_PATTERNS.get(entity_type)
        if not pattern:
            continue

        for match in re.finditer(pattern, text):
            entities.append({
                "type": entity_type,
                "value": match.group(),
                "start": match.start(),
                "end": match.end(),
                "score": 0.85,
            })

    # Sort by position (start offset)
    entities.sort(key=lambda e: e["start"])
    return entities


def _redact_text(text: str, entities: list[dict]) -> str:
    """Replace detected entities with redaction markers."""
    # Process from end to start to preserve offsets
    result = text
    for entity in sorted(entities, key=lambda e: e["start"], reverse=True):
        placeholder = f"[{entity['type']}]"
        result = result[: entity["start"]] + placeholder + result[entity["end"] :]
    return result


def _detect_with_presidio(text: str, entity_types: list[str] | None = None) -> list[dict]:
    """Detect PHI entities using Presidio analyzer."""
    from presidio_analyzer import AnalyzerEngine

    analyzer = AnalyzerEngine()
    presidio_types = entity_types or [
        "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN",
        "CREDIT_CARD", "IP_ADDRESS", "DATE_TIME", "MEDICAL_LICENSE",
    ]

    results = analyzer.analyze(text=text, entities=presidio_types, language="en")

    entities = []
    for result in results:
        entities.append({
            "type": result.entity_type,
            "value": text[result.start : result.end],
            "start": result.start,
            "end": result.end,
            "score": round(result.score, 2),
        })

    entities.sort(key=lambda e: e["start"])
    return entities


class PhiRedactorTool(DynamicTool):
    """Detects and optionally redacts PHI/PII from text."""

    @property
    def tool_name(self) -> str:
        return "phi_redactor"

    @property
    def tool_description(self) -> str:
        return (
            "Detects protected health information (PHI) and personally identifiable "
            "information (PII) in text. Supports SSN, phone, email, MRN, DOB, dates, "
            "and person names. Can optionally redact detected entities. Uses Presidio "
            "when available, falls back to regex patterns."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "text": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The text to scan for PHI/PII",
                ),
                "entity_types": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    items=adk_types.Schema(type=adk_types.Type.STRING),
                    description=(
                        "Specific entity types to detect. Options: SSN, PHONE, EMAIL, "
                        "MRN, DOB, DATE, IP_ADDRESS, CREDIT_CARD, PERSON. Default: all."
                    ),
                    nullable=True,
                ),
                "return_redacted": adk_types.Schema(
                    type=adk_types.Type.BOOLEAN,
                    description="If true, return redacted text with entities replaced by [TYPE] markers",
                    nullable=True,
                ),
            },
            required=["text"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        text = args.get("text", "")
        entity_types = args.get("entity_types")
        return_redacted = args.get("return_redacted", False)

        if not text:
            return {"entities": [], "entity_count": 0}

        # Try Presidio first, fall back to regex
        import logging as _log
        try:
            entities = _detect_with_presidio(text, entity_types)
            detection_method = "presidio"
        except (ImportError, ModuleNotFoundError):
            entities = _detect_with_regex(text, entity_types)
            detection_method = "regex"
        except Exception as exc:
            _log.getLogger(__name__).warning("Presidio error, falling back to regex: %s", exc)
            entities = _detect_with_regex(text, entity_types)
            detection_method = "regex"

        result = {
            "entities": entities,
            "entity_count": len(entities),
            "detection_method": detection_method,
        }

        if return_redacted and entities:
            result["redacted_text"] = _redact_text(text, entities)

        return result
