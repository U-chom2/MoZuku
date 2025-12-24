"""Comment extraction module using tree-sitter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from tree_sitter_languages import get_language, get_parser
except ImportError:
    get_language = None
    get_parser = None


@dataclass
class CommentSegment:
    """Represents a comment segment in source code."""

    start_byte: int
    end_byte: int
    sanitized: str  # Comment text with leading markers removed


@dataclass
class ByteRange:
    """Byte range for content highlighting."""

    start_byte: int
    end_byte: int


# Language ID to tree-sitter language name mapping
LANGUAGE_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "typescriptreact": "tsx",
    "javascriptreact": "javascript",
    "c": "c",
    "cpp": "cpp",
    "rust": "rust",
    "go": "go",
    "java": "java",
    "html": "html",
    "css": "css",
    "latex": None,  # LaTeX uses custom parsing
}

# Comment node types for each language
COMMENT_NODE_TYPES = {
    "python": {"comment"},
    "javascript": {"comment", "line_comment", "block_comment"},
    "typescript": {"comment", "line_comment", "block_comment"},
    "tsx": {"comment", "line_comment", "block_comment"},
    "c": {"comment", "line_comment", "block_comment"},
    "cpp": {"comment", "line_comment", "block_comment"},
    "rust": {"line_comment", "block_comment"},
    "go": {"comment"},
    "java": {"line_comment", "block_comment"},
    "html": {"comment"},
    "css": {"comment"},
}


def is_language_supported(language_id: str) -> bool:
    """Check if a language is supported for comment extraction."""
    return language_id in LANGUAGE_MAP


def extract_comments(language_id: str, text: str) -> list[CommentSegment]:
    """Extract comments from source code.

    Args:
        language_id: Language identifier (e.g., "python", "javascript")
        text: Source code text

    Returns:
        List of comment segments
    """
    import sys

    if get_parser is None:
        print("[DEBUG] extract_comments: get_parser is None", file=sys.stderr)
        return []

    ts_language = LANGUAGE_MAP.get(language_id)
    if ts_language is None:
        if language_id == "latex":
            return _extract_latex_comments(text)
        print(f"[DEBUG] extract_comments: language_id {language_id} not in LANGUAGE_MAP", file=sys.stderr)
        return []

    try:
        parser = get_parser(ts_language)
    except Exception as e:
        print(f"[DEBUG] extract_comments: get_parser failed: {e}", file=sys.stderr)
        return []

    text_bytes = text.encode("utf-8")
    tree = parser.parse(text_bytes)
    if tree is None:
        print("[DEBUG] extract_comments: tree is None", file=sys.stderr)
        return []

    comment_types = COMMENT_NODE_TYPES.get(ts_language, set())
    print(f"[DEBUG] extract_comments: ts_language={ts_language}, comment_types={comment_types}", file=sys.stderr)
    segments: list[CommentSegment] = []

    def visit_node(node):
        if node.type in comment_types:
            start_byte = node.start_byte
            end_byte = node.end_byte
            # Use byte slice, then decode to get correct text
            comment_text = text_bytes[start_byte:end_byte].decode("utf-8", errors="replace")
            sanitized = _sanitize_comment(comment_text, language_id)
            segments.append(
                CommentSegment(
                    start_byte=start_byte,
                    end_byte=end_byte,
                    sanitized=sanitized,
                )
            )
        else:
            for child in node.children:
                visit_node(child)

    visit_node(tree.root_node)

    return segments


def extract_html_content_ranges(text: str) -> list[ByteRange]:
    """Extract content (text) ranges from HTML.

    Args:
        text: HTML source code

    Returns:
        List of byte ranges containing text content
    """
    if get_parser is None:
        return []

    try:
        parser = get_parser("html")
    except Exception:
        return []

    tree = parser.parse(text.encode("utf-8"))
    if tree is None:
        return []

    ranges: list[ByteRange] = []

    def visit_node(node):
        if node.type == "text":
            start = node.start_byte
            end = node.end_byte
            if start >= end or end > len(text.encode("utf-8")):
                return

            # Trim whitespace
            content = text.encode("utf-8")[start:end].decode("utf-8", errors="replace")
            trimmed_start = start
            trimmed_end = end

            # Trim leading whitespace
            for i, c in enumerate(content):
                if not c.isspace():
                    break
                trimmed_start += len(c.encode("utf-8"))

            # Trim trailing whitespace
            for i, c in enumerate(reversed(content)):
                if not c.isspace():
                    break
                trimmed_end -= len(c.encode("utf-8"))

            if trimmed_end > trimmed_start:
                ranges.append(ByteRange(start_byte=trimmed_start, end_byte=trimmed_end))
        else:
            for child in node.children:
                visit_node(child)

    visit_node(tree.root_node)

    return ranges


def extract_latex_content_ranges(text: str) -> list[ByteRange]:
    """Extract content ranges from LaTeX (excluding commands, math, comments).

    Args:
        text: LaTeX source code

    Returns:
        List of byte ranges containing text content
    """
    ranges: list[ByteRange] = []
    text_bytes = text.encode("utf-8")
    i = 0

    while i < len(text):
        c = text[i]

        # Skip comments
        if c == "%" and not _is_escaped(text, i):
            line_end = text.find("\n", i)
            if line_end == -1:
                break
            i = line_end + 1
            continue

        # Skip inline math $...$
        if c == "$" and not _is_escaped(text, i):
            if i + 1 < len(text) and text[i + 1] == "$":
                # Display math $$...$$
                closing = _find_closing_double_dollar(text, i + 2)
                if closing == -1:
                    break
                i = closing + 2
            else:
                closing = _find_closing_dollar(text, i + 1)
                if closing == -1:
                    break
                i = closing + 1
            continue

        # Skip LaTeX commands
        if c == "\\":
            i += 1
            while i < len(text) and (text[i].isalpha() or text[i] == "@"):
                i += 1
            if i < len(text) and text[i] == "*":
                i += 1
            continue

        # Skip braces
        if c in "{}":
            i += 1
            continue

        # Skip whitespace
        if c.isspace():
            i += 1
            continue

        # Found content start
        start = i
        advanced = False

        while i < len(text):
            d = text[i]
            if d in "\\${}":
                break
            if d == "%" and not _is_escaped(text, i):
                break
            if d.isspace() or (ord(d) < 128 and not d.isalnum()):
                break

            i += 1
            advanced = True

        if advanced:
            start_byte = len(text[:start].encode("utf-8"))
            end_byte = len(text[:i].encode("utf-8"))
            ranges.append(ByteRange(start_byte=start_byte, end_byte=end_byte))
        else:
            i += 1

    return ranges


def _extract_latex_comments(text: str) -> list[CommentSegment]:
    """Extract comments from LaTeX source."""
    segments: list[CommentSegment] = []
    pos = 0

    while pos < len(text):
        line_start = pos
        line_end = text.find("\n", pos)
        if line_end == -1:
            line_end = len(text)

        # Find comment start
        current = line_start
        while current < line_end:
            if text[current] == "%" and not _is_escaped(text, current):
                # Found comment
                start_byte = len(text[:current].encode("utf-8"))
                end_byte = len(text[:line_end].encode("utf-8"))
                comment_text = text[current:line_end]
                sanitized = _sanitize_latex_comment(comment_text)
                segments.append(
                    CommentSegment(
                        start_byte=start_byte,
                        end_byte=end_byte,
                        sanitized=sanitized,
                    )
                )
                break
            current += 1

        if line_end >= len(text):
            break
        pos = line_end + 1

    return segments


def _is_escaped(text: str, pos: int) -> bool:
    """Check if character at position is escaped."""
    count = 0
    while pos > count and text[pos - count - 1] == "\\":
        count += 1
    return count % 2 == 1


def _find_closing_dollar(text: str, pos: int) -> int:
    """Find closing $ for inline math."""
    for i in range(pos, len(text)):
        if text[i] == "$" and not _is_escaped(text, i):
            return i
    return -1


def _find_closing_double_dollar(text: str, pos: int) -> int:
    """Find closing $$ for display math."""
    for i in range(pos, len(text) - 1):
        if text[i] == "$" and text[i + 1] == "$" and not _is_escaped(text, i):
            return i
    return -1


def _sanitize_comment(text: str, language_id: str) -> str:
    """Sanitize comment text by replacing leading markers with spaces.

    IMPORTANT: The returned string must have the same length as the input
    to preserve byte offsets when masking text.
    """
    if not text:
        return text

    result = list(text)

    if language_id == "python":
        # Replace # and following whitespace with spaces (maintaining length)
        i = 0
        while i < len(result) and result[i] == "#":
            result[i] = " "
            i += 1
        while i < len(result) and result[i] in " \t":
            result[i] = " "
            i += 1
    elif language_id in ("javascript", "typescript", "c", "cpp", "java", "go", "rust"):
        # Handle line comments: // ...
        if len(result) >= 2 and result[0] == "/" and result[1] == "/":
            i = 0
            while i < len(result) and result[i] == "/":
                result[i] = " "
                i += 1
            while i < len(result) and result[i] in " \t":
                result[i] = " "
                i += 1
        # Handle block comments: /* ... */
        elif len(result) >= 2 and result[0] == "/" and result[1] == "*":
            # Replace leading /*
            result[0] = " "
            result[1] = " "
            i = 2
            while i < len(result) and result[i] == "*":
                result[i] = " "
                i += 1
            while i < len(result) and result[i] in " \t":
                result[i] = " "
                i += 1
            # Replace trailing */
            if len(result) >= 2 and result[-1] == "/" and result[-2] == "*":
                result[-1] = " "
                result[-2] = " "
                # Replace any additional * before */
                j = len(result) - 3
                while j >= 0 and result[j] == "*":
                    result[j] = " "
                    j -= 1

    return "".join(result)


def _sanitize_latex_comment(text: str) -> str:
    """Sanitize LaTeX comment text."""
    if not text:
        return text

    result = list(text)
    result[0] = " "  # Replace %

    # Replace consecutive % at start
    idx = 1
    while idx < len(result) and result[idx] == "%":
        result[idx] = " "
        idx += 1

    # Replace leading whitespace
    while idx < len(result) and result[idx] in " \t":
        result[idx] = " "
        idx += 1

    return "".join(result)


def mask_text_except_comments(
    language_id: str, text: str, segments: list[CommentSegment]
) -> str:
    """Mask text except comment regions.

    Args:
        language_id: Language identifier
        text: Source code
        segments: Comment segments

    Returns:
        Text with non-comment parts replaced by spaces
    """
    if not segments:
        # Mask everything except newlines
        return "".join(" " if c not in "\n\r" else c for c in text)

    masked = list(text)

    # First, mask everything except newlines
    for i, c in enumerate(masked):
        if c not in "\n\r":
            masked[i] = " "

    # Then, restore comment segments
    text_bytes = text.encode("utf-8")
    for segment in segments:
        # Convert byte offset to character offset
        start_char = len(text_bytes[: segment.start_byte].decode("utf-8", errors="replace"))
        sanitized = segment.sanitized

        # Copy sanitized comment to masked text
        for j, c in enumerate(sanitized):
            if start_char + j < len(masked):
                masked[start_char + j] = c

    return "".join(masked)


def mask_text_except_content(
    language_id: str, text: str, content_ranges: list[ByteRange]
) -> str:
    """Mask text except content regions.

    Args:
        language_id: Language identifier
        text: Source code
        content_ranges: Content byte ranges

    Returns:
        Text with non-content parts replaced by spaces
    """
    if not content_ranges:
        return "".join(" " if c not in "\n\r" else c for c in text)

    masked = list(text)

    # First, mask everything except newlines
    for i, c in enumerate(masked):
        if c not in "\n\r":
            masked[i] = " "

    # Then, restore content ranges
    text_bytes = text.encode("utf-8")
    for byte_range in content_ranges:
        start_char = len(text_bytes[: byte_range.start_byte].decode("utf-8", errors="replace"))
        end_char = len(text_bytes[: byte_range.end_byte].decode("utf-8", errors="replace"))

        for j in range(start_char, min(end_char, len(masked))):
            masked[j] = text[j]

    return "".join(masked)
