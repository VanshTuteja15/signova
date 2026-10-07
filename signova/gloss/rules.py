"""Rule-based English -> ASL-style gloss. Offline fallback and the evaluation baseline.

Order of matching (per MASTER_PROMPT 7.1):
  1. multi-word library phrases, longest match first ("i love you" -> ILY)
  2. single words, digits and number words found in the library's `english` lists
  3. words ASL doesn't sign are dropped (articles, forms of "be", "to", "of", "do")
  4. pronouns are skipped: a pointing sign needs arm movement this hand doesn't have
  5. numbers that aren't in the library are signed digit by digit (25 -> 2 5)
  6. anything else is fingerspelled if every letter is an available letter sign and it is
     at most 12 letters long; otherwise it is skipped with a reason naming the missing letters

Contractions are expanded first ("I'm" -> "i am", "don't" -> "do not") so everyday speech
from Whisper matches the library phrases.
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
MAX_FINGERSPELL = 12
MAX_DIGITS = 4
POINTING_REASON = "pointing sign needs arm movement"

RULES_NOTE = (
    "Rule-based gloss: matched library phrases and numbers, dropped words ASL doesn't sign, "
    "skipped pronouns (pointing needs an arm) and fingerspelled words the hand can spell. "
    "Word order is left as spoken."
)


CONTRACTIONS: dict[str, str] = {
    "i'm": "i am",
    "you're": "you are",
    "we're": "we are",
    "they're": "they are",
    "he's": "he is",
    "she's": "she is",
    "it's": "it is",
    "that's": "that is",
    "what's": "what is",
    "where's": "where is",
    "who's": "who is",
    "how's": "how is",
    "there's": "there is",
    "here's": "here is",
    "let's": "let us",
    "i'll": "i will",
    "you'll": "you will",
    "we'll": "we will",
    "they'll": "they will",
    "i've": "i have",
    "you've": "you have",
    "we've": "we have",
    "they've": "they have",
    "i'd": "i would",
    "you'd": "you would",
    "can't": "can not",
    "cannot": "can not",
    "won't": "will not",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "haven't": "have not",
    "hasn't": "has not",
    "couldn't": "could not",
    "shouldn't": "should not",
    "wouldn't": "would not",
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "got to",
}
_CONTRACTION_RE = re.compile(
    r"(?<![a-z'])("
    + "|".join(re.escape(k) for k in sorted(CONTRACTIONS, key=len, reverse=True))
    + r")(?![a-z'])"
)


def expand_contractions(text: str) -> str:
    """ "I'm Vansh" -> "i am Vansh". Lowercases; handles curly apostrophes."""
    lowered = text.lower().replace("\u2019", "'")
    return _CONTRACTION_RE.sub(lambda m: CONTRACTIONS[m.group(1)], lowered)


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
    tokens = tokenize(expand_contractions(join_spelled_letters(text)))
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
        word = tokens[i]
        if word.isdigit() and len(word) <= MAX_DIGITS and all(library.available(d) for d in word):
            items.extend(GlossItem(type="sign", id=d, word=word) for d in word)
        else:
            items.append(classify_word(word, library, letters))
        i += 1

    note = RULES_NOTE if items else "Nothing to sign."
    return GlossResult(text=text, items=items, note=note, engine="rules", requested_engine="rules")
