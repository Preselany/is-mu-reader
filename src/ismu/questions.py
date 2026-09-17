"""Immutable question helpers; constructing an Answer never sends a request."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from .errors import InvalidInput

AnswerValue: TypeAlias = str | list[str]


@dataclass(frozen=True)
class Choice:
    """One exposed option. Pass value to answer(); label is for display."""

    value: str
    label: str


@dataclass(frozen=True)
class Answer:
    """A local answer bound to one captured form; pass it to save() or submit().

    Obtain this from Question.answer or AnswerField.answer. No answer is staged
    or sent implicitly. Recreate answers after a successful save/refresh.
    """

    _name: str = field(repr=False)
    _value: str | tuple[str, ...] = field(repr=False)
    _origin: str = field(repr=False)
    _capture: str = field(repr=False)


@dataclass(frozen=True)
class AnswerField:
    """One logical input, grouping radio/checkbox options by their native name."""

    index: int
    kind: str
    label: str
    multiple: bool
    choices: tuple[Choice, ...]
    required: bool
    maxlength: int | None
    _name: str = field(repr=False)
    _origin: str = field(repr=False)
    _capture: str = field(repr=False)

    def answer(self, value: AnswerValue) -> Answer:
        """Build a validated local answer. Lists select multiple options."""
        if not isinstance(value, (str, list)) or (
            isinstance(value, list) and not all(isinstance(v, str) for v in value)
        ):
            raise InvalidInput("An answer must be a string or a list of strings.")
        values = [value] if isinstance(value, str) else value
        if not self.multiple and len(values) != 1:
            raise InvalidInput("This field requires exactly one answer value.")
        if self.kind in ("radio", "checkbox", "select") and any(
            v not in {choice.value for choice in self.choices} for v in values
        ):
            raise InvalidInput("Choose a value from this field's choices.")
        if self.maxlength is not None and any(len(v) > self.maxlength for v in values):
            raise InvalidInput("An answer exceeds the field length limit.")
        stored = value if isinstance(value, str) else tuple(value)
        return Answer(self._name, stored, self._origin, self._capture)


@dataclass(frozen=True)
class Question:
    """A displayed question; number is IS's number, not a guessed list index."""

    id: str | None
    number: str | None
    title: str
    text: str
    fields: tuple[AnswerField, ...]

    def answer(self, value: AnswerValue) -> Answer:
        """Answer a question with one logical field without knowing its HTML name.

        For multiple blanks use question.fields[index].answer(value) and pass
        the resulting answers together to the session. Field indices are zero-based.
        """
        if len(self.fields) != 1:
            raise InvalidInput(
                "Select a field explicitly for a question with zero or multiple inputs."
            )
        return self.fields[0].answer(value)
