"""Grammar checking module for Japanese text."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spacy.tokens import Doc, Token

from .analyzer import Position, Range, SentenceBoundary, TokenData


@dataclass
class Diagnostic:
    """LSP Diagnostic."""

    range: Range = field(default_factory=Range)
    severity: int = 2  # 1=Error, 2=Warning, 3=Info, 4=Hint
    message: str = ""
    source: str = "mozuku"


@dataclass
class RuleToggles:
    """Rule toggle settings."""

    comma_limit: bool = True
    adversative_ga: bool = True
    duplicate_particle_surface: bool = True
    adjacent_particles: bool = True
    conjunction_repeat: bool = True
    ra_dropping: bool = True
    comma_limit_max: int = 3
    adversative_ga_max: int = 1
    duplicate_particle_surface_max_repeat: int = 1
    adjacent_particles_max_repeat: int = 1
    conjunction_repeat_max: int = 1


@dataclass
class AnalysisConfig:
    """Analysis configuration."""

    grammar_check: bool = True
    min_japanese_ratio: float = 0.1
    warning_min_severity: int = 2
    rules: RuleToggles = field(default_factory=RuleToggles)


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled."""
    return os.environ.get("MOZUKU_DEBUG") is not None


class GrammarChecker:
    """Japanese grammar checker."""

    def __init__(self, config: AnalysisConfig | None = None):
        self.config = config or AnalysisConfig()

    def check_grammar(
        self,
        text: str,
        tokens: list[TokenData],
        sentences: list[SentenceBoundary],
    ) -> list[Diagnostic]:
        """Check grammar and return diagnostics.

        Args:
            text: Original text
            tokens: Analyzed tokens
            sentences: Sentence boundaries

        Returns:
            List of diagnostics
        """
        if not self.config.grammar_check:
            return []

        diagnostics: list[Diagnostic] = []

        # Compute line starts for position calculation
        line_starts = _compute_line_starts(text)

        # Compute token byte positions
        token_byte_positions = self._compute_token_byte_positions(tokens, text, line_starts)

        # Run each grammar rule
        if self.config.rules.comma_limit:
            self._check_comma_limit(
                text, sentences, line_starts, diagnostics, self.config.rules.comma_limit_max
            )

        if self.config.rules.adversative_ga:
            self._check_adversative_ga(
                text,
                tokens,
                sentences,
                line_starts,
                token_byte_positions,
                diagnostics,
                self.config.rules.adversative_ga_max,
            )

        if self.config.rules.duplicate_particle_surface:
            self._check_duplicate_particle_surface(
                text,
                tokens,
                sentences,
                line_starts,
                token_byte_positions,
                diagnostics,
                self.config.rules.duplicate_particle_surface_max_repeat,
            )

        if self.config.rules.adjacent_particles:
            self._check_adjacent_particles(
                text,
                tokens,
                sentences,
                line_starts,
                token_byte_positions,
                diagnostics,
                self.config.rules.adjacent_particles_max_repeat,
            )

        if self.config.rules.conjunction_repeat:
            self._check_conjunction_repeats(
                text,
                tokens,
                line_starts,
                token_byte_positions,
                diagnostics,
                self.config.rules.conjunction_repeat_max,
            )

        if self.config.rules.ra_dropping:
            self._check_ra_dropping(
                text, tokens, line_starts, token_byte_positions, diagnostics
            )

        return diagnostics

    def _compute_token_byte_positions(
        self, tokens: list[TokenData], text: str, line_starts: list[int]
    ) -> list[int]:
        """Compute byte positions for each token."""
        positions = []
        for token in tokens:
            byte_pos = self._to_byte_offset(token, text, line_starts)
            positions.append(byte_pos)
        return positions

    def _to_byte_offset(
        self, token: TokenData, text: str, line_starts: list[int]
    ) -> int:
        """Convert token position to byte offset."""
        if token.line >= len(line_starts):
            return len(text.encode("utf-8"))

        line_start = line_starts[token.line]
        line_text = text[line_start:]

        # Find end of line
        newline_pos = line_text.find("\n")
        if newline_pos != -1:
            line_text = line_text[:newline_pos]

        # Convert UTF-16 offset to character offset
        char_offset = _utf16_to_char_offset(line_text, token.start_char)

        # Convert to byte offset
        full_text_char_offset = line_start + char_offset
        return len(text[:full_text_char_offset].encode("utf-8"))

    def _in_sentence(self, byte_pos: int, sentence: SentenceBoundary) -> bool:
        """Check if byte position is within sentence."""
        return sentence.start <= byte_pos < sentence.end

    def _make_range(
        self, text: str, line_starts: list[int], start_byte: int, end_byte: int
    ) -> Range:
        """Create LSP Range from byte offsets."""
        start_pos = _byte_offset_to_position(text, line_starts, start_byte)
        end_pos = _byte_offset_to_position(text, line_starts, end_byte)
        return Range(start=start_pos, end=end_pos)

    def _count_commas(self, text: str) -> int:
        """Count Japanese commas in text."""
        return text.count("、")

    def _check_comma_limit(
        self,
        text: str,
        sentences: list[SentenceBoundary],
        line_starts: list[int],
        diagnostics: list[Diagnostic],
        limit: int,
    ) -> None:
        """Check comma limit per sentence."""
        if limit <= 0:
            return

        for sentence in sentences:
            comma_count = self._count_commas(sentence.text)
            if comma_count <= limit:
                continue

            diag = Diagnostic(
                range=self._make_range(text, line_starts, sentence.start, sentence.end),
                severity=2,
                message=f"一文に使用できる読点「、」は最大{limit}個までです (現在{comma_count}個)",
            )

            if _is_debug_enabled():
                import sys

                print(
                    f"[DEBUG] Comma limit exceeded in sentence {sentence.sentence_id}: count={comma_count}",
                    file=sys.stderr,
                )

            diagnostics.append(diag)

    def _is_adversative_ga(self, feature: str) -> bool:
        """Check if token is adversative 'が' (逆接の接続助詞)."""
        # Feature format: 品詞,細分類1,細分類2,細分類3,活用型,活用形,原形,...
        parts = feature.split(",")
        if len(parts) < 7:
            return False

        pos = parts[0]
        sub1 = parts[1]
        base = parts[6]

        return pos == "助詞" and sub1 == "接続助詞" and base == "が"

    def _check_adversative_ga(
        self,
        text: str,
        tokens: list[TokenData],
        sentences: list[SentenceBoundary],
        line_starts: list[int],
        token_byte_positions: list[int],
        diagnostics: list[Diagnostic],
        max_count: int,
    ) -> None:
        """Check for excessive use of adversative 'が'."""
        if max_count <= 0:
            return

        for sentence in sentences:
            count = 0
            for i, token in enumerate(tokens):
                if not self._is_adversative_ga(token.feature):
                    continue
                byte_pos = token_byte_positions[i]
                if self._in_sentence(byte_pos, sentence):
                    count += 1

            if count <= max_count:
                continue

            diag = Diagnostic(
                range=self._make_range(text, line_starts, sentence.start, sentence.end),
                severity=2,
                message=f"逆接の接続助詞「が」が同一文で{max_count + 1}回以上使われています ({count}回)",
            )

            if _is_debug_enabled():
                import sys

                print(
                    f"[DEBUG] Adversative 'が' exceeded in sentence {sentence.sentence_id}: count={count}",
                    file=sys.stderr,
                )

            diagnostics.append(diag)

    def _is_particle(self, feature: str) -> bool:
        """Check if token is a particle (助詞)."""
        parts = feature.split(",")
        if not parts:
            return False
        return parts[0] == "助詞"

    def _particle_key(self, feature: str) -> str:
        """Get particle category key (品詞,細分類1)."""
        parts = feature.split(",")
        if len(parts) < 2:
            return parts[0] if parts else ""
        return f"{parts[0]},{parts[1]}"

    def _check_duplicate_particle_surface(
        self,
        text: str,
        tokens: list[TokenData],
        sentences: list[SentenceBoundary],
        line_starts: list[int],
        token_byte_positions: list[int],
        diagnostics: list[Diagnostic],
        max_repeat: int,
    ) -> None:
        """Check for duplicate particle surface forms."""
        if max_repeat <= 0:
            return

        for sentence in sentences:
            last_surface = ""
            last_key = ""
            last_start_byte = 0
            last_line = -1
            streak = 1
            has_last = False

            for i, token in enumerate(tokens):
                byte_pos = token_byte_positions[i]
                if not self._in_sentence(byte_pos, sentence):
                    continue

                if not self._is_particle(token.feature):
                    continue

                current_key = self._particle_key(token.feature)

                # Reset streak if on a different line (don't check across line breaks)
                if has_last and token.line != last_line:
                    streak = 1
                    last_start_byte = byte_pos
                    has_last = False

                if has_last and token.surface == last_surface and current_key == last_key:
                    streak += 1
                    if streak > max_repeat:
                        current_end = byte_pos + len(token.surface.encode("utf-8"))
                        diag = Diagnostic(
                            range=self._make_range(
                                text, line_starts, last_start_byte, current_end
                            ),
                            severity=2,
                            message=f"同じ助詞「{token.surface}」が連続しています",
                        )

                        if _is_debug_enabled():
                            import sys

                            print(
                                f"[DEBUG] Duplicate particle '{token.surface}' in sentence {sentence.sentence_id}",
                                file=sys.stderr,
                            )

                        diagnostics.append(diag)
                else:
                    streak = 1
                    last_start_byte = byte_pos

                last_surface = token.surface
                last_key = current_key
                last_line = token.line
                has_last = True

    def _check_adjacent_particles(
        self,
        text: str,
        tokens: list[TokenData],
        sentences: list[SentenceBoundary],
        line_starts: list[int],
        token_byte_positions: list[int],
        diagnostics: list[Diagnostic],
        max_repeat: int,
    ) -> None:
        """Check for adjacent particles of the same type."""
        if max_repeat <= 0:
            return

        for sentence in sentences:
            prev_is_particle = False
            prev_key = ""
            prev_token: TokenData | None = None
            prev_start_byte = 0
            streak = 1

            for i, token in enumerate(tokens):
                byte_pos = token_byte_positions[i]
                if not self._in_sentence(byte_pos, sentence):
                    continue

                current_is_particle = self._is_particle(token.feature)
                current_key = self._particle_key(token.feature)

                if (
                    current_is_particle
                    and prev_is_particle
                    and current_key == prev_key
                    and prev_token is not None
                ):
                    # Check if adjacent
                    prev_end = prev_start_byte + len(prev_token.surface.encode("utf-8"))
                    if byte_pos == prev_end:
                        streak += 1
                        if streak > max_repeat:
                            current_end = byte_pos + len(token.surface.encode("utf-8"))
                            diag = Diagnostic(
                                range=self._make_range(
                                    text, line_starts, prev_start_byte, current_end
                                ),
                                severity=2,
                                message="助詞が連続して使われています",
                            )

                            if _is_debug_enabled():
                                import sys

                                print(
                                    f"[DEBUG] Consecutive particles '{prev_token.surface}' -> '{token.surface}' in sentence {sentence.sentence_id}",
                                    file=sys.stderr,
                                )

                            diagnostics.append(diag)
                else:
                    streak = 1
                    if current_is_particle:
                        prev_start_byte = byte_pos

                prev_is_particle = current_is_particle
                if current_is_particle:
                    prev_token = token
                    prev_start_byte = byte_pos
                    prev_key = current_key

    def _is_conjunction(self, feature: str) -> bool:
        """Check if token is a conjunction (接続詞)."""
        parts = feature.split(",")
        if not parts:
            return False
        return parts[0] == "接続詞"

    def _check_conjunction_repeats(
        self,
        text: str,
        tokens: list[TokenData],
        line_starts: list[int],
        token_byte_positions: list[int],
        diagnostics: list[Diagnostic],
        max_repeat: int,
    ) -> None:
        """Check for repeated conjunctions."""
        if max_repeat <= 0:
            return

        last_surface = ""
        last_start_byte = 0
        last_end_byte = 0
        streak = 1
        has_last = False

        for i, token in enumerate(tokens):
            if not self._is_conjunction(token.feature):
                continue

            current_start = token_byte_positions[i]
            current_end = current_start + len(token.surface.encode("utf-8"))

            # Check if separated by newline
            separated_by_newline = False
            if has_last:
                text_between = text.encode("utf-8")[last_end_byte:current_start].decode(
                    "utf-8", errors="replace"
                )
                if "\n" in text_between:
                    separated_by_newline = True

            if has_last and token.surface == last_surface and not separated_by_newline:
                streak += 1
                if streak > max_repeat:
                    diag = Diagnostic(
                        range=self._make_range(
                            text, line_starts, last_start_byte, current_end
                        ),
                        severity=2,
                        message=f"同じ接続詞「{token.surface}」が連続しています",
                    )

                    if _is_debug_enabled():
                        import sys

                        print(
                            f"[DEBUG] Duplicate conjunction '{token.surface}' detected",
                            file=sys.stderr,
                        )

                    diagnostics.append(diag)
            else:
                streak = 1
                last_start_byte = current_start

            last_surface = token.surface
            last_start_byte = current_start
            last_end_byte = current_end
            has_last = True

    def _is_target_verb(self, feature: str) -> bool:
        """Check if token is a target verb for ra-dropping check."""
        # 動詞,自立 + 一段 + 未然形
        parts = feature.split(",")
        if len(parts) < 6:
            return False

        main_pos = parts[0]
        sub1 = parts[1]
        inflection = parts[4]
        conjugation = parts[5]

        return (
            main_pos == "動詞"
            and sub1 == "自立"
            and inflection == "一段"
            and conjugation == "未然形"
        )

    def _is_ra_word(self, feature: str) -> bool:
        """Check if token is 'れる' suffix."""
        parts = feature.split(",")
        if len(parts) < 7:
            return False

        main_pos = parts[0]
        sub1 = parts[1]
        base = parts[6]

        return main_pos == "動詞" and sub1 == "接尾" and base == "れる"

    def _is_special_ra_case(self, feature: str) -> bool:
        """Check for special ra-dropping cases like 来れる, 見れる."""
        parts = feature.split(",")
        if len(parts) < 7:
            return False

        main_pos = parts[0]
        base = parts[6]

        return main_pos == "動詞" and base in ("来れる", "見れる")

    def _check_ra_dropping(
        self,
        text: str,
        tokens: list[TokenData],
        line_starts: list[int],
        token_byte_positions: list[int],
        diagnostics: list[Diagnostic],
    ) -> None:
        """Check for ra-dropping (ら抜き言葉)."""
        message_ra = "ら抜き言葉を使用しています"

        # Check special cases (単体で来れる、見れる)
        for i, token in enumerate(tokens):
            if self._is_special_ra_case(token.feature):
                start_byte = token_byte_positions[i]
                end_byte = start_byte + len(token.surface.encode("utf-8"))
                diag = Diagnostic(
                    range=self._make_range(text, line_starts, start_byte, end_byte),
                    severity=2,
                    message=message_ra,
                )
                diagnostics.append(diag)

                if _is_debug_enabled():
                    import sys

                    print(
                        f"[DEBUG] Ra-dropping special case detected: {token.surface}",
                        file=sys.stderr,
                    )

        # Check 2-token combinations (動詞一段未然形 + 接尾「れる」)
        prev_token: TokenData | None = None
        has_prev = False

        for i, token in enumerate(tokens):
            if has_prev and prev_token is not None:
                if self._is_target_verb(prev_token.feature) and self._is_ra_word(
                    token.feature
                ):
                    start_byte = token_byte_positions[i - 1]
                    end_byte = token_byte_positions[i] + len(token.surface.encode("utf-8"))
                    diag = Diagnostic(
                        range=self._make_range(text, line_starts, start_byte, end_byte),
                        severity=2,
                        message=message_ra,
                    )
                    diagnostics.append(diag)

                    if _is_debug_enabled():
                        import sys

                        print(
                            f"[DEBUG] Ra-dropping detected between tokens '{prev_token.surface}' + '{token.surface}'",
                            file=sys.stderr,
                        )

            prev_token = token
            has_prev = True


def _compute_line_starts(text: str) -> list[int]:
    """Compute character offsets of line starts."""
    line_starts = [0]
    for i, c in enumerate(text):
        if c == "\n":
            line_starts.append(i + 1)
    return line_starts


def _byte_offset_to_position(text: str, line_starts: list[int], byte_offset: int) -> Position:
    """Convert byte offset to LSP Position."""
    # Convert byte offset to character offset
    text_bytes = text.encode("utf-8")
    if byte_offset >= len(text_bytes):
        byte_offset = len(text_bytes)
    char_offset = len(text_bytes[:byte_offset].decode("utf-8", errors="replace"))

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


def _utf16_to_char_offset(text: str, utf16_offset: int) -> int:
    """Convert UTF-16 offset to character offset."""
    char_offset = 0
    utf16_count = 0

    for c in text:
        if utf16_count >= utf16_offset:
            break
        code = ord(c)
        if code > 0xFFFF:
            utf16_count += 2  # Surrogate pair
        else:
            utf16_count += 1
        char_offset += 1

    return char_offset


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
