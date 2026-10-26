"""A summarizer with no model behind it.

Used when Ollama is down, slow, or not pulled yet. It does not understand the
meeting. It splits the transcript into sentences and guesses at tasks from a
short list of phrases, so the notes page still has something to show.
"""

from __future__ import annotations

import re

from kinig_hub.models.base import Summarizer

# English and Taglish cues. "will" also matches words like "willing";
# that false positive is acceptable for a fallback.
_ACTION_CUES = (
    "need to",
    "needs to",
    "have to",
    "has to",
    "will",
    "i'll",
    "we'll",
    "should",
    "action:",
    "todo",
    "deadline",
    "kailangan",
    "gagawin",
    "dapat",
    "i-follow up",
    "i-follow-up",
    "assigned",
)

# "by Friday" or "by 10/29" is a deadline. "by the way" is not, because
# "the" is not a day or a date.
_BY_WHEN = re.compile(
    r"\bby\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|today|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b",
    re.IGNORECASE,
)

# "si Lance will ..." / "kay Ana will ..."
_SI_KAY_WILL = re.compile(
    r"\b(?:si|kay)\s+[A-Za-z][\w.-]*\s+will\b",
    re.IGNORECASE,
)

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


class RuleBasedSummarizer(Summarizer):
    def __init__(self, options: dict):
        # The registry passes the same [summarizer.options] table every backend
        # gets. This one does not read model, prompt_file, or output_language.
        self.options = options

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        text = transcript.strip()
        if not text:
            return {"summary": "", "key_points": [], "action_items": []}

        sentences = [part.strip() for part in _SENTENCE_BREAK.split(text) if part.strip()]
        if not sentences:
            sentences = [text]

        # Two sentences is enough of an overview when no LLM is available.
        summary = " ".join(sentences[:2])
        actions = [sentence for sentence in sentences if _looks_like_action(sentence)]
        return {
            "summary": summary,
            "key_points": sentences,
            "action_items": actions,
        }


def _looks_like_action(sentence: str) -> bool:
    lowered = sentence.lower()
    if any(cue in lowered for cue in _ACTION_CUES):
        return True
    if _BY_WHEN.search(sentence):
        return True
    return _SI_KAY_WILL.search(sentence) is not None
