"""Every path: a choice whose handler resumes the rest of the program with each answer in turn.

The rest is a value, the program being data, so nothing is run again to come back to
the choice: what the program did before it, it did once."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import reduce
from typing import Any, TypeVar

from okay_effects.core import Cap, Data, O, pure, resuming

A = TypeVar("A")


def all_paths(kind: type[O], answers: Callable[[O], Iterable[Any]], body: Callable[[Cap[O]], Data[A]]) -> Data[list[A]]:
    """The body's value for every combination of answers, depth first."""

    def on(op: O, k: Callable[[Any], Data[list[A]]]) -> Data[list[A]]:
        def and_with(found: Data[list[A]], answer: Any) -> Data[list[A]]:
            return found.bind(lambda xs: k(answer).map(lambda ys: xs + ys))

        return reduce(and_with, answers(op), pure([]))

    return resuming(kind, on, lambda a: pure([a]), body)
