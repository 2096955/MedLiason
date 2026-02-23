"""Query sanitization and injection prevention utilities.

Provides input validation for MCP servers that interact with external
APIs using query languages (SoQL for CDC SODA, SQL for provider_intel).
"""

import re


def sanitize_query(query: str, max_len: int = 500) -> str:
    """Strip potentially dangerous patterns from a search query.

    Removes SQL/SoQL injection patterns while preserving legitimate
    medical search terms. Truncates to max_len.
    """
    if not query:
        return ""
    # Remove common injection patterns
    dangerous_patterns = [
        r";\s*DROP\b",
        r";\s*DELETE\b",
        r";\s*UPDATE\b",
        r";\s*INSERT\b",
        r";\s*ALTER\b",
        r";\s*CREATE\b",
        r";\s*EXEC\b",
        r"--\s",
        r"/\*.*?\*/",
        r"UNION\s+SELECT",
        r"OR\s+1\s*=\s*1",
        r"'\s*OR\s+'",
    ]
    sanitized = query
    for pattern in dangerous_patterns:
        sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)

    # Remove unbalanced quotes
    sanitized = sanitized.replace("'", "").replace('"', "")

    # Truncate
    return sanitized[:max_len].strip()


def escape_soql(value: str) -> str:
    """Escape a value for use in CDC SODA SoQL $where clauses.

    Prevents SoQL injection by escaping single quotes and backslashes.
    """
    if not value:
        return ""
    # Escape backslashes first, then single quotes
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("'", "\\'")
    return escaped


def escape_sql_string(value: str) -> str:
    """Escape a value for use in SQL string literals.

    Prevents SQL injection in provider_intel queries. Validates that
    the value contains only safe characters for names/states/companies.
    """
    if not value:
        return ""
    # Remove any characters that aren't alphanumeric, spaces, hyphens, periods, or commas
    safe = re.sub(r"[^\w\s\-.,]", "", value)
    # Escape single quotes
    safe = safe.replace("'", "''")
    return safe


def validate_numeric(value: str) -> str | None:
    """Validate and return a numeric string, or None if invalid."""
    try:
        float(value)
        return value
    except (ValueError, TypeError):
        return None
