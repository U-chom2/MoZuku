"""MoZuku LSP Server - Japanese NLP Language Server using GinZA."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from .analyzer import TOKEN_MODIFIERS, TOKEN_TYPES, Analyzer, TokenData
from .comment_extractor import (
    ByteRange,
    CommentSegment,
    extract_comments,
    extract_html_content_ranges,
    extract_latex_content_ranges,
    is_language_supported,
    mask_text_except_comments,
    mask_text_except_content,
)
from .grammar_checker import AnalysisConfig, Diagnostic, GrammarChecker, RuleToggles
from .wikipedia import (
    fetch_summary_sync,
    get_cached_entry,
    get_japanese_error_message,
    prefetch_summary,
)


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled."""
    return True  # Always enable debug for now
    # return os.environ.get("MOZUKU_DEBUG") is not None


@dataclass
class MoZukuConfig:
    """Server configuration."""

    model_name: str = "ja_ginza"
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)


@dataclass
class DocumentState:
    """State for an open document."""

    text: str = ""
    language_id: str = ""
    tokens: list[TokenData] = field(default_factory=list)
    comment_segments: list[CommentSegment] = field(default_factory=list)
    content_ranges: list[ByteRange] = field(default_factory=list)


class MoZukuLanguageServer(LanguageServer):
    """MoZuku Language Server."""

    def __init__(self):
        super().__init__("mozuku-lsp", "v1.0.0")
        self.config = MoZukuConfig()
        self.analyzer = Analyzer()
        self.grammar_checker = GrammarChecker()
        self.documents: dict[str, DocumentState] = {}

    def initialize_analyzer(self) -> bool:
        """Initialize the GinZA analyzer."""
        return self.analyzer.initialize(self.config.model_name)


# Create server instance
server = MoZukuLanguageServer()


@server.feature(lsp.INITIALIZE)
def on_initialize(params: lsp.InitializeParams) -> lsp.InitializeResult:
    """Handle initialize request."""
    # Extract configuration from initialization options
    if params.initialization_options:
        opts = params.initialization_options

        # Model configuration
        if "model" in opts and isinstance(opts["model"], str):
            server.config.model_name = opts["model"]

        # Analysis configuration
        if "analysis" in opts:
            analysis = opts["analysis"]
            if "grammarCheck" in analysis:
                server.config.analysis.grammar_check = bool(analysis["grammarCheck"])
            if "minJapaneseRatio" in analysis:
                server.config.analysis.min_japanese_ratio = float(
                    analysis["minJapaneseRatio"]
                )
            if "warningMinSeverity" in analysis:
                server.config.analysis.warning_min_severity = int(
                    analysis["warningMinSeverity"]
                )

            # Rules configuration
            if "rules" in analysis:
                rules = analysis["rules"]
                if "commaLimit" in rules:
                    server.config.analysis.rules.comma_limit = bool(rules["commaLimit"])
                if "adversativeGa" in rules:
                    server.config.analysis.rules.adversative_ga = bool(
                        rules["adversativeGa"]
                    )
                if "duplicateParticleSurface" in rules:
                    server.config.analysis.rules.duplicate_particle_surface = bool(
                        rules["duplicateParticleSurface"]
                    )
                if "adjacentParticles" in rules:
                    server.config.analysis.rules.adjacent_particles = bool(
                        rules["adjacentParticles"]
                    )
                if "conjunctionRepeat" in rules:
                    server.config.analysis.rules.conjunction_repeat = bool(
                        rules["conjunctionRepeat"]
                    )
                if "raDropping" in rules:
                    server.config.analysis.rules.ra_dropping = bool(rules["raDropping"])
                if "commaLimitMax" in rules:
                    server.config.analysis.rules.comma_limit_max = int(
                        rules["commaLimitMax"]
                    )
                if "adversativeGaMax" in rules:
                    server.config.analysis.rules.adversative_ga_max = int(
                        rules["adversativeGaMax"]
                    )
                if "duplicateParticleSurfaceMaxRepeat" in rules:
                    server.config.analysis.rules.duplicate_particle_surface_max_repeat = int(
                        rules["duplicateParticleSurfaceMaxRepeat"]
                    )
                if "adjacentParticlesMaxRepeat" in rules:
                    server.config.analysis.rules.adjacent_particles_max_repeat = int(
                        rules["adjacentParticlesMaxRepeat"]
                    )
                if "conjunctionRepeatMax" in rules:
                    server.config.analysis.rules.conjunction_repeat_max = int(
                        rules["conjunctionRepeatMax"]
                    )

    # Update grammar checker with new config
    server.grammar_checker = GrammarChecker(server.config.analysis)

    # Initialize analyzer
    server.initialize_analyzer()

    # Define semantic token legend
    legend = lsp.SemanticTokensLegend(
        token_types=TOKEN_TYPES,
        token_modifiers=TOKEN_MODIFIERS,
    )

    return lsp.InitializeResult(
        capabilities=lsp.ServerCapabilities(
            text_document_sync=lsp.TextDocumentSyncOptions(
                open_close=True,
                change=lsp.TextDocumentSyncKind.Incremental,
                save=lsp.SaveOptions(include_text=False),
            ),
            semantic_tokens_provider=lsp.SemanticTokensOptions(
                legend=legend,
                range=True,
                full=True,
            ),
            hover_provider=True,
        ),
    )


@server.feature(lsp.INITIALIZED)
def on_initialized(params: lsp.InitializedParams) -> None:
    """Handle initialized notification."""
    if _is_debug_enabled():
        print("[DEBUG] Server initialized", file=sys.stderr)


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def on_did_open(params: lsp.DidOpenTextDocumentParams) -> None:
    """Handle document open."""
    uri = params.text_document.uri
    text = params.text_document.text
    language_id = params.text_document.language_id

    if _is_debug_enabled():
        print(f"[DEBUG] on_did_open: uri={uri}, language_id={language_id}", file=sys.stderr)

    server.documents[uri] = DocumentState(
        text=text,
        language_id=language_id,
    )

    _analyze_and_publish(uri, text)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def on_did_change(params: lsp.DidChangeTextDocumentParams) -> None:
    """Handle document change."""
    uri = params.text_document.uri
    if uri not in server.documents:
        return

    doc = server.documents[uri]
    text = doc.text

    # Apply changes
    for change in params.content_changes:
        if isinstance(change, lsp.TextDocumentContentChangePartial):
            # Incremental change
            start_offset = _position_to_offset(text, change.range.start)
            end_offset = _position_to_offset(text, change.range.end)
            text = text[:start_offset] + change.text + text[end_offset:]
        else:
            # Full change
            text = change.text

    doc.text = text
    _analyze_and_publish(uri, text)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def on_did_save(params: lsp.DidSaveTextDocumentParams) -> None:
    """Handle document save."""
    uri = params.text_document.uri
    if uri in server.documents:
        _analyze_and_publish(uri, server.documents[uri].text)


@server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def on_did_close(params: lsp.DidCloseTextDocumentParams) -> None:
    """Handle document close."""
    uri = params.text_document.uri
    if uri in server.documents:
        del server.documents[uri]


@server.feature(lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL)
def on_semantic_tokens_full(
    params: lsp.SemanticTokensParams,
) -> lsp.SemanticTokens | None:
    """Handle semantic tokens full request."""
    uri = params.text_document.uri
    if uri not in server.documents:
        return None

    doc = server.documents[uri]
    if doc.language_id != "japanese":
        return None

    data = _build_semantic_tokens(doc.tokens)
    return lsp.SemanticTokens(data=data)


@server.feature(lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_RANGE)
def on_semantic_tokens_range(
    params: lsp.SemanticTokensRangeParams,
) -> lsp.SemanticTokens | None:
    """Handle semantic tokens range request."""
    uri = params.text_document.uri
    if uri not in server.documents:
        return None

    doc = server.documents[uri]
    if doc.language_id != "japanese":
        return None

    data = _build_semantic_tokens(doc.tokens)
    return lsp.SemanticTokens(data=data)


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def on_hover(params: lsp.HoverParams) -> lsp.Hover | None:
    """Handle hover request."""
    uri = params.text_document.uri
    if uri not in server.documents:
        return None

    doc = server.documents[uri]
    line = params.position.line
    character = params.position.character

    # For non-Japanese documents, check if position is in comment/content
    if doc.language_id != "japanese":
        offset = _position_to_offset(doc.text, params.position)
        byte_offset = len(doc.text[:offset].encode("utf-8"))

        in_comment = any(
            seg.start_byte <= byte_offset < seg.end_byte for seg in doc.comment_segments
        )
        in_content = any(
            r.start_byte <= byte_offset < r.end_byte for r in doc.content_ranges
        )

        if not in_comment and not in_content:
            return None

    # Find token at position
    for token in doc.tokens:
        if token.line == line and token.start_char <= character < token.end_char:
            return _build_hover(token)

    return None


def _analyze_and_publish(uri: str, text: str) -> None:
    """Analyze document and publish diagnostics."""
    if not server.analyzer.is_initialized:
        if not server.initialize_analyzer():
            if _is_debug_enabled():
                print("[ERROR] Failed to initialize analyzer", file=sys.stderr)
            return

    doc = server.documents.get(uri)
    if doc is None:
        if _is_debug_enabled():
            print(f"[DEBUG] _analyze_and_publish: doc is None for {uri}", file=sys.stderr)
        return

    if _is_debug_enabled():
        print(f"[DEBUG] _analyze_and_publish: language_id={doc.language_id}", file=sys.stderr)

    # Prepare analysis text
    analysis_text = _prepare_analysis_text(uri, text)

    if _is_debug_enabled():
        print(f"[DEBUG] analysis_text length: {len(analysis_text)}, first 100 chars: {repr(analysis_text[:100])}", file=sys.stderr)

    # Analyze text
    tokens = server.analyzer.analyze_text(analysis_text)
    sentences = server.analyzer.get_sentences(analysis_text)

    if _is_debug_enabled():
        print(f"[DEBUG] tokens: {len(tokens)}, sentences: {len(sentences)}", file=sys.stderr)

    # Check grammar
    diagnostics = server.grammar_checker.check_grammar(analysis_text, tokens, sentences)

    if _is_debug_enabled():
        print(f"[DEBUG] diagnostics: {len(diagnostics)}", file=sys.stderr)

    # Store tokens
    doc.tokens = tokens

    # Convert and publish diagnostics
    lsp_diagnostics = [
        lsp.Diagnostic(
            range=lsp.Range(
                start=lsp.Position(
                    line=diag.range.start.line, character=diag.range.start.character
                ),
                end=lsp.Position(
                    line=diag.range.end.line, character=diag.range.end.character
                ),
            ),
            severity=lsp.DiagnosticSeverity(diag.severity),
            message=diag.message,
            source="mozuku",
        )
        for diag in diagnostics
    ]

    server.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=uri, diagnostics=lsp_diagnostics)
    )

    # Send custom notifications for highlights
    _send_comment_highlights(uri, text, doc.comment_segments)
    _send_content_highlights(uri, text, doc.content_ranges)
    _send_semantic_highlights(uri, tokens, doc.language_id)


def _prepare_analysis_text(uri: str, text: str) -> str:
    """Prepare text for analysis based on document language."""
    doc = server.documents.get(uri)
    if doc is None:
        return text

    language_id = doc.language_id

    if language_id == "japanese":
        doc.comment_segments = []
        doc.content_ranges = []
        return text

    # HTML: Extract text content from tags
    if language_id == "html":
        comment_segments = extract_comments(language_id, text)
        content_ranges = extract_html_content_ranges(text)
        doc.comment_segments = comment_segments
        doc.content_ranges = content_ranges

        # Combine content ranges with comment segments
        all_ranges = list(content_ranges)
        for seg in comment_segments:
            all_ranges.append(ByteRange(start_byte=seg.start_byte, end_byte=seg.end_byte))

        return mask_text_except_content(language_id, text, all_ranges)

    # LaTeX: Extract text content
    if language_id == "latex":
        from .comment_extractor import _extract_latex_comments

        comment_segments = _extract_latex_comments(text)
        content_ranges = extract_latex_content_ranges(text)
        doc.comment_segments = comment_segments
        doc.content_ranges = content_ranges

        # Combine
        all_ranges = list(content_ranges)
        for seg in comment_segments:
            all_ranges.append(ByteRange(start_byte=seg.start_byte, end_byte=seg.end_byte))

        return mask_text_except_content(language_id, text, all_ranges)

    # Other languages: Extract comments
    if is_language_supported(language_id):
        comment_segments = extract_comments(language_id, text)
        if _is_debug_enabled():
            print(f"[DEBUG] extract_comments: found {len(comment_segments)} comments", file=sys.stderr)
            for seg in comment_segments:
                print(f"[DEBUG]   segment: {seg.start_byte}:{seg.end_byte} = {repr(seg.sanitized[:50])}", file=sys.stderr)
        doc.comment_segments = comment_segments
        doc.content_ranges = []
        return mask_text_except_comments(language_id, text, comment_segments)

    doc.comment_segments = []
    doc.content_ranges = []
    return text


def _build_semantic_tokens(tokens: list[TokenData]) -> list[int]:
    """Build semantic tokens data array."""
    data: list[int] = []
    prev_line = 0
    prev_char = 0

    for token in tokens:
        delta_line = token.line - prev_line
        delta_char = token.start_char - prev_char if delta_line == 0 else token.start_char

        try:
            type_index = TOKEN_TYPES.index(token.token_type)
        except ValueError:
            type_index = 0

        data.extend(
            [
                delta_line,
                delta_char,
                token.end_char - token.start_char,
                type_index,
                token.token_modifiers,
            ]
        )

        prev_line = token.line
        prev_char = token.start_char

    return data


def _build_hover(token: TokenData) -> lsp.Hover:
    """Build hover response for a token."""
    lines = [
        f"**{token.surface}**",
        "```",
        token.feature,
        "```",
    ]

    if token.base_form:
        lines.append(f"**原形**: {token.base_form}")
    if token.reading:
        lines.append(f"**読み**: {token.reading}")
    if token.pronunciation:
        lines.append(f"**発音**: {token.pronunciation}")

    # Add Wikipedia info for nouns
    if _is_noun(token):
        query = token.base_form or token.surface
        cached = get_cached_entry(query)

        if cached:
            if cached.response_code == 200:
                lines.extend(["", "---", f"**Wikipedia**: {cached.content}"])
            else:
                lines.extend(
                    ["", "---", f"**Wikipedia**: {get_japanese_error_message(cached.response_code)}"]
                )
        else:
            # Start prefetching for next hover
            prefetch_summary(query)

    content = "\n".join(lines)

    return lsp.Hover(
        contents=lsp.MarkupContent(
            kind=lsp.MarkupKind.Markdown,
            value=content,
        ),
        range=lsp.Range(
            start=lsp.Position(line=token.line, character=token.start_char),
            end=lsp.Position(line=token.line, character=token.end_char),
        ),
    )


def _is_noun(token: TokenData) -> bool:
    """Check if token is a noun."""
    if token.token_type == "noun":
        return True

    parts = token.feature.split(",")
    if parts:
        return parts[0] == "名詞"
    return False


def _send_comment_highlights(uri: str, text: str, segments: list[CommentSegment]) -> None:
    """Send comment highlight notification."""
    ranges = []
    for seg in segments:
        start_pos = _byte_offset_to_position(text, seg.start_byte)
        end_pos = _byte_offset_to_position(text, seg.end_byte)
        ranges.append(
            {
                "start": {"line": start_pos.line, "character": start_pos.character},
                "end": {"line": end_pos.line, "character": end_pos.character},
            }
        )

    server.protocol.notify("mozuku/commentHighlights", {"uri": uri, "ranges": ranges})


def _send_content_highlights(uri: str, text: str, ranges: list[ByteRange]) -> None:
    """Send content highlight notification."""
    lsp_ranges = []
    for r in ranges:
        start_pos = _byte_offset_to_position(text, r.start_byte)
        end_pos = _byte_offset_to_position(text, r.end_byte)
        lsp_ranges.append(
            {
                "start": {"line": start_pos.line, "character": start_pos.character},
                "end": {"line": end_pos.line, "character": end_pos.character},
            }
        )

    server.protocol.notify("mozuku/contentHighlights", {"uri": uri, "ranges": lsp_ranges})


def _send_semantic_highlights(uri: str, tokens: list[TokenData], language_id: str) -> None:
    """Send semantic highlight notification."""
    if language_id == "japanese":
        # Japanese documents use LSP semantic tokens directly
        server.protocol.notify("mozuku/semanticHighlights", {"uri": uri, "tokens": []})
        return

    token_entries = []
    for token in tokens:
        token_entries.append(
            {
                "range": {
                    "start": {"line": token.line, "character": token.start_char},
                    "end": {"line": token.line, "character": token.end_char},
                },
                "type": token.token_type,
                "modifiers": token.token_modifiers,
            }
        )

    server.protocol.notify("mozuku/semanticHighlights", {"uri": uri, "tokens": token_entries})


def _position_to_offset(text: str, position: lsp.Position) -> int:
    """Convert LSP position to character offset."""
    lines = text.split("\n")
    offset = 0

    for i in range(position.line):
        if i < len(lines):
            offset += len(lines[i]) + 1  # +1 for newline

    if position.line < len(lines):
        line = lines[position.line]
        # Convert UTF-16 offset to character offset
        utf16_count = 0
        char_offset = 0
        for c in line:
            if utf16_count >= position.character:
                break
            code = ord(c)
            if code > 0xFFFF:
                utf16_count += 2
            else:
                utf16_count += 1
            char_offset += 1
        offset += char_offset

    return offset


def _byte_offset_to_position(text: str, byte_offset: int) -> lsp.Position:
    """Convert byte offset to LSP position."""
    text_bytes = text.encode("utf-8")
    if byte_offset >= len(text_bytes):
        byte_offset = len(text_bytes)

    # Decode up to byte offset
    prefix = text_bytes[:byte_offset].decode("utf-8", errors="replace")

    # Count lines
    lines = prefix.split("\n")
    line = len(lines) - 1
    last_line = lines[-1] if lines else ""

    # Convert to UTF-16 code units
    character = 0
    for c in last_line:
        code = ord(c)
        if code > 0xFFFF:
            character += 2
        else:
            character += 1

    return lsp.Position(line=line, character=character)


def main():
    """Main entry point."""
    import os
    if _is_debug_enabled():
        print("[DEBUG] Starting MoZuku LSP server", file=sys.stderr)
        print(f"[DEBUG] Python executable: {sys.executable}", file=sys.stderr)
        print(f"[DEBUG] Python path: {sys.path[:3]}", file=sys.stderr)
        print(f"[DEBUG] PYTHONPATH env: {os.environ.get('PYTHONPATH', '(not set)')}", file=sys.stderr)
        print(f"[DEBUG] PYTHONHOME env: {os.environ.get('PYTHONHOME', '(not set)')}", file=sys.stderr)
        # Test tree-sitter
        try:
            import tree_sitter
            import tree_sitter_languages
            print(f"[DEBUG] tree_sitter location: {tree_sitter.__file__}", file=sys.stderr)
            print(f"[DEBUG] tree_sitter_languages location: {tree_sitter_languages.__file__}", file=sys.stderr)
            from tree_sitter_languages import get_parser
            p = get_parser('python')
            print(f"[DEBUG] tree-sitter test: OK, parser={p}", file=sys.stderr)
        except Exception as e:
            import traceback
            print(f"[DEBUG] tree-sitter test FAILED: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    server.start_io()


if __name__ == "__main__":
    main()
