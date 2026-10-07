"""Rule-based English -> ASL-style gloss. Offline fallback and the evaluation baseline.

Order of matching (per MASTER_PROMPT 7.1):
  1. multi-word library phrases, longest match first ("i love you" -> ILY)
  2. single words, digits and number words found in the library's `english` lists
  3. words ASL doesn't sign are dropped (articles, forms of "be", "to", "of", "do")
  4. pronouns are skipped: a pointing sign needs arm movement this hand doesn't have
  5. anything else is fingerspelled if every letter is an available letter sign and it is
     at most 8 letters long; otherwise it is skipped with a reason naming the missing letters
"""

from __future__ import annotations

import re

from ..library import Library, tokenize
from .types import GlossItem, GlossResult

DROP_WORDS = frozenset(
    {"a", "an", "the", "is", "am", "are", "was", "were", "be", "been", "to", "of", "do", "does", "did"}
)
POINTING_WORDS = frozenset(
    {"i", "me", "my", "you", "your", "he", "she", "we", "they", "him", "her", "them", "our"}
)
MAX_FINGERSPELL = 8
POINTING_REASON = "pointing sign needs arm movement"

RULES_NOTE = (
    "Rule-based gloss: matched library phrases and numbers, dropped words ASL doesn't sign, "
    "skipped pronouns (pointing needs an arm) and fingerspelled words the hand can spell. "
    "Word order is left as spoken."
)


def letters_of(word: str) -> str:
    return word.replace("'", "").upper()


def fingerspell_problem(word: str, letters: set[str], max_len: int = MAX_FINGERSPELL) -> str | None:
    """Return why `word` can't be fingerspelled with `letters`, or None if it can."""
    spelled = letters_of(word)
    if not spelled:
        return "nothing to spell"
    if not spelled.isalpha():
        return "contains digits or symbols the hand can't fingerspell"
    missing: list[str] = []
    for ch in spelled:
        if ch not in letters and ch not in missing:
            missing.append(ch)
    if missing:
        return "can't fingerspell: no handshape for " + ", ".join(missing)
    if len(spelled) > max_len:
        return f"too long to fingerspell ({len(spelled)} letters, max {max_len})"
    return None


def classify_word(word: str, library: Library, letters: set[str] | None = None) -> GlossItem:
    """Classify one token that did not match a library phrase."""
    letters = library.letters if letters is None else letters
    if word in DROP_WORDS:
        return GlossItem(type="drop", word=word)
    if word in POINTING_WORDS:
        return GlossItem(type="skip", word=word, reason=POINTING_REASON)
    if word.isdigit():
        return GlossItem(type="skip", word=word, reason=f"no number sign for {word} in the library yet")
    problem = fingerspell_problem(word, letters)
    if problem is None:
        return GlossItem(type="fs", word=word.replace("'", ""))
    return GlossItem(type="skip", word=word, reason=problem)


_SPELLED = re.compile(r"(?<![A-Za-z])[A-Za-z](?:[-.]\s?[A-Za-z])+(?![A-Za-z])")


def join_spelled_letters(text: str) -> str:
    """Whisper writes spelled words as 'C-O-D-E' or 'C.O.D.E'; turn them back into 'CODE'."""
    return _SPELLED.sub(lambda m: re.sub(r"[-.\s]", "", m.group(0)), text)


def rule_gloss(text: str, library: Library) -> GlossResult:
    tokens = tokenize(join_spelled_letters(text))
    phrases = library.phrase_map()
    longest = max((len(k) for k in phrases), default=1)
    letters = library.letters
    items: list[GlossItem] = []

    i = 0
    while i < len(tokens):
        match: tuple[str, ...] | None = None
        for n in range(min(longest, len(tokens) - i), 0, -1):
            key = tuple(tokens[i : i + n])
            if key in phrases:
                match = key
                break
        if match is not None:
            items.append(GlossItem(type="sign", id=phrases[match], word=" ".join(match)))
            i += len(match)
            continue
        items.append(classify_word(tokens[i], library, letters))
        i += 1

    note = RULES_NOTE if items else "Nothing to sign."
    return GlossResult(text=text, items=items, note=note, engine="rules", requested_engine="rules")
