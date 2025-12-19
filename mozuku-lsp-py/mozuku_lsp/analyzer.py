"""Japanese text analyzer using GinZA/spaCy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import spacy
from spacy.tokens import Doc, Token

if TYPE_CHECKING:
    from spacy.language import Language

# Token types for semantic tokens (matching C++ implementation)
TOKEN_TYPES = [
    "noun",
    "verb",
    "adjective",
    "adverb",
    "particle",
    "aux",
    "conjunction",
    "symbol",
    "interj",
    "prefix",
    "suffix",
    "unknown",
]

TOKEN_MODIFIERS = ["proper", "numeric", "kana", "kanji"]


@dataclass
class TokenData:
    """Token information for LSP."""

    surface: str = ""
    feature: str = ""
    base_form: str = ""
    reading: str = ""
    pronunciation: str = ""
    token_type: str = "unknown"
    token_modifiers: int = 0
    line: int = 0
    start_char: int = 0  # UTF-16 code units
    end_char: int = 0  # UTF-16 code units


@dataclass
class Position:
    """LSP position (0-based)."""

    line: int = 0
    character: int = 0


@dataclass
class Range:
    """LSP range."""

    start: Position = field(default_factory=Position)
    end: Position = field(default_factory=Position)


@dataclass
class DependencyInfo:
    """Dependency parsing information."""

    chunk_id: int = 0
    head_id: int = -1
    score: float = 0.0
    text: str = ""


@dataclass
class SentenceBoundary:
    """Sentence boundary information."""

    start: int = 0  # byte offset
    end: int = 0  # byte offset
    sentence_id: int = 0
    text: str = ""


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled."""
    return os.environ.get("MOZUKU_DEBUG") is not None


class Analyzer:
    """Japanese text analyzer using GinZA."""

    def __init__(self):
        self._nlp: Language | None = None
        self._initialized = False

    def initialize(self, model_name: str = "ja_ginza") -> bool:
        """Initialize the analyzer with specified model.

        Args:
            model_name: GinZA model name ("ja_ginza" or "ja_ginza_electra")

        Returns:
            True if initialization succeeded
        """
        try:
            self._nlp = spacy.load(model_name)
            self._initialized = True
            if _is_debug_enabled():
                import sys

                print(f"[DEBUG] Analyzer initialized with {model_name}", file=sys.stderr)
            return True
        except OSError as e:
            if _is_debug_enabled():
                import sys

                print(f"[ERROR] Failed to load {model_name}: {e}", file=sys.stderr)
            return False

    @property
    def is_initialized(self) -> bool:
        """Check if analyzer is initialized."""
        return self._initialized

    def analyze_text(self, text: str) -> list[TokenData]:
        """Analyze text and return tokens.

        Args:
            text: Input text to analyze

        Returns:
            List of TokenData
        """
        if not self._initialized or self._nlp is None:
            return []

        if not text:
            return []

        tokens: list[TokenData] = []
        doc: Doc = self._nlp(text)

        # Compute line starts for position calculation
        line_starts = _compute_line_starts(text)

        for token in doc:
            token_data = self._token_to_data(token, text, line_starts)
            if token_data:
                tokens.append(token_data)

        if _is_debug_enabled():
            import sys

            print(f"[DEBUG] Analysis completed: {len(tokens)} tokens generated", file=sys.stderr)

        return tokens

    def analyze_dependencies(self, text: str) -> list[DependencyInfo]:
        """Analyze dependency structure.

        Args:
            text: Input text to analyze

        Returns:
            List of DependencyInfo
        """
        if not self._initialized or self._nlp is None:
            return []

        if not text:
            return []

        doc: Doc = self._nlp(text)
        dependencies: list[DependencyInfo] = []

        # GinZA provides bunsetu (文節) level dependency through ginza module
        try:
            import ginza

            for sent in doc.sents:
                bunsetu_list = list(ginza.bunsetu_spans(sent))
                for i, bunsetu in enumerate(bunsetu_list):
                    # Get the head bunsetu
                    head_token = bunsetu.root.head
                    head_bunsetu_id = -1

                    for j, other_bunsetu in enumerate(bunsetu_list):
                        if head_token in other_bunsetu:
                            head_bunsetu_id = j
                            break

                    dep = DependencyInfo(
                        chunk_id=i,
                        head_id=head_bunsetu_id if head_bunsetu_id != i else -1,
                        score=1.0,
                        text=bunsetu.text,
                    )
                    dependencies.append(dep)
        except Exception as e:
            if _is_debug_enabled():
                import sys

                print(f"[DEBUG] Dependency analysis error: {e}", file=sys.stderr)

        return dependencies

    def get_sentences(self, text: str) -> list[SentenceBoundary]:
        """Split text into sentences.

        Args:
            text: Input text

        Returns:
            List of SentenceBoundary
        """
        if not self._initialized or self._nlp is None:
            return []

        if not text:
            return []

        doc: Doc = self._nlp(text)
        sentences: list[SentenceBoundary] = []

        for i, sent in enumerate(doc.sents):
            # Convert character offsets to byte offsets
            start_byte = len(text[: sent.start_char].encode("utf-8"))
            end_byte = len(text[: sent.end_char].encode("utf-8"))

            sentences.append(
                SentenceBoundary(
                    start=start_byte,
                    end=end_byte,
                    sentence_id=i,
                    text=sent.text,
                )
            )

        return sentences

    def _token_to_data(
        self, token: Token, text: str, line_starts: list[int]
    ) -> TokenData | None:
        """Convert spaCy token to TokenData."""
        if token.is_space:
            return None

        # Get token position
        char_start = token.idx
        char_end = token.idx + len(token.text)

        # Convert to line/character position (UTF-16)
        pos = _char_offset_to_position(text, line_starts, char_start)
        end_pos = _char_offset_to_position(text, line_starts, char_end)

        # Build feature string (MeCab compatible format)
        # Format: 品詞,品詞細分類1,品詞細分類2,品詞細分類3,活用型,活用形,原形,読み,発音
        feature = self._build_feature_string(token)

        # Determine token type
        token_type = self._map_pos_to_type(token)

        # Compute modifiers
        modifiers = self._compute_modifiers(token, text[char_start:char_end])

        return TokenData(
            surface=token.text,
            feature=feature,
            base_form=token.lemma_,
            reading=self._get_reading(token),
            pronunciation=self._get_reading(token),  # Use reading as pronunciation
            token_type=token_type,
            token_modifiers=modifiers,
            line=pos.line,
            start_char=pos.character,
            end_char=end_pos.character if pos.line == end_pos.line else pos.character + _utf8_to_utf16_length(token.text),
        )

    def _build_feature_string(self, token: Token) -> str:
        """Build MeCab-compatible feature string from spaCy token."""
        # GinZA stores detailed POS in token.tag_
        # Format varies but typically: 品詞-細分類1-細分類2-細分類3
        tag_parts = token.tag_.split("-") if token.tag_ else []

        main_pos = tag_parts[0] if len(tag_parts) > 0 else token.pos_
        sub1 = tag_parts[1] if len(tag_parts) > 1 else "*"
        sub2 = tag_parts[2] if len(tag_parts) > 2 else "*"
        sub3 = tag_parts[3] if len(tag_parts) > 3 else "*"

        # Inflection info from morphological analysis
        morph = token.morph.to_dict()
        inflection = morph.get("Inflection", "*")
        conjugation = morph.get("VerbForm", "*")

        reading = self._get_reading(token)

        # Format: 品詞,細分類1,細分類2,細分類3,活用型,活用形,原形,読み,発音
        return f"{main_pos},{sub1},{sub2},{sub3},{inflection},{conjugation},{token.lemma_},{reading},{reading}"

    def _get_reading(self, token: Token) -> str:
        """Get reading (読み) from token."""
        # GinZA stores reading in morph or extension attributes
        morph = token.morph.to_dict()
        reading = morph.get("Reading", "")
        if not reading:
            # Try to get from token's custom attributes if available
            try:
                if hasattr(token, "_") and hasattr(token._, "reading"):
                    reading = token._.reading or ""
            except Exception:
                pass
        return reading or token.text

    def _map_pos_to_type(self, token: Token) -> str:
        """Map spaCy POS to token type."""
        pos = token.pos_
        tag = token.tag_ if token.tag_ else ""

        # Map Universal POS to our token types
        pos_map = {
            "NOUN": "noun",
            "PROPN": "noun",
            "VERB": "verb",
            "ADJ": "adjective",
            "ADV": "adverb",
            "ADP": "particle",  # Adposition (includes Japanese particles)
            "AUX": "aux",
            "CCONJ": "conjunction",
            "SCONJ": "conjunction",
            "PUNCT": "symbol",
            "SYM": "symbol",
            "INTJ": "interj",
            "NUM": "noun",
        }

        # Check tag for more specific classification
        if "助詞" in tag:
            return "particle"
        if "助動詞" in tag:
            return "aux"
        if "接頭" in tag:
            return "prefix"
        if "接尾" in tag:
            return "suffix"

        return pos_map.get(pos, "unknown")

    def _compute_modifiers(self, token: Token, surface: str) -> int:
        """Compute token modifiers bitmask."""
        modifiers = 0

        # Check if proper noun
        if token.pos_ == "PROPN" or (token.tag_ and "固有名詞" in token.tag_):
            modifiers |= 1  # proper

        # Check if numeric
        if token.pos_ == "NUM" or surface.isdigit() or _is_numeric_kanji(surface):
            modifiers |= 2  # numeric

        # Check character type
        if _is_all_kana(surface):
            modifiers |= 4  # kana
        elif _is_all_kanji(surface):
            modifiers |= 8  # kanji

        return modifiers


def _compute_line_starts(text: str) -> list[int]:
    """Compute byte offsets of line starts."""
    line_starts = [0]
    for i, c in enumerate(text):
        if c == "\n":
            line_starts.append(i + 1)
    return line_starts


def _char_offset_to_position(text: str, line_starts: list[int], char_offset: int) -> Position:
    """Convert character offset to LSP Position (UTF-16)."""
    # Find line
    line = 0
    for i, start in enumerate(line_starts):
        if char_offset < start:
            break
        line = i

    line_start = line_starts[line]
    line_text = text[line_start:char_offset]

    # Convert to UTF-16 code units
    utf16_offset = _utf8_to_utf16_length(line_text)

    return Position(line=line, character=utf16_offset)


def _utf8_to_utf16_length(text: str) -> int:
    """Calculate UTF-16 code unit length of a string."""
    length = 0
    for c in text:
        code = ord(c)
        if code > 0xFFFF:
            length += 2  # Surrogate pair
        else:
            length += 1
    return length


def _is_all_kana(text: str) -> bool:
    """Check if text contains only kana characters."""
    if not text:
        return False
    for c in text:
        code = ord(c)
        # Hiragana: 0x3040-0x309F, Katakana: 0x30A0-0x30FF
        if not (0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF):
            return False
    return True


def _is_all_kanji(text: str) -> bool:
    """Check if text contains only kanji characters."""
    if not text:
        return False
    for c in text:
        code = ord(c)
        # CJK Unified Ideographs: 0x4E00-0x9FFF
        if not (0x4E00 <= code <= 0x9FFF):
            return False
    return True


def _is_numeric_kanji(text: str) -> bool:
    """Check if text is numeric kanji."""
    numeric_kanji = set("〇一二三四五六七八九十百千万億兆")
    return all(c in numeric_kanji for c in text)
