"""The kernel's protocol: a program handed out, stepped by a host that sees only
operations and answers (as in okay-kernel)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from okay_effects.core import Cap, Eff, O, Op, Prog
from okay_effects.machine import Asked, Done, step

A = TypeVar("A")


class Host:
    """The outermost owner of a handed-out program: capabilities the host answers."""

    def __init__(self) -> None:
        self.caps: list[Cap[Any]] = []

    def outside(self, kind: type[O]) -> Cap[O]:
        """A capability whose operations go to whoever steps the program."""
        cap = Cap(kind)
        self.caps.append(cap)
        return cap


@dataclass(frozen=True, slots=True)
class Step(Generic[A]):
    """One stop of a handed-out program."""

    kind: Literal["done", "yielded", "failed", "cancelled"]
    value: A | None = None
    op: Op[Any] | None = None
    failure: BaseException | None = None


class Computation(Generic[A]):
    """A program handed out to a host, run one step at a time."""

    def __init__(self, body: Callable[[Host], Eff[A] | Prog[A]]) -> None:
        self._host = Host()
        self._program = body(self._host)
        self._at: Asked[A] | None = None  # the operation it is stopped at
        self._over = False

    def step(self) -> Step[A]:
        """Run to the value, the failure, or the first operation for the host."""
        return self._next(lambda: step(self._program))

    def resume(self, v: Any) -> Step[A]:
        """The operation answered with v; run to the next stop."""
        return self._next(lambda: self._stopped().resume(v))

    def fail(self, error: BaseException) -> Step[A]:
        """The operation answered with a failure, raised where it was performed —
        an ``except`` around it in the program sees it."""
        return self._next(lambda: self._stopped().fail(error))

    def cancel(self) -> Step[A]:
        """Stopped where it is; its ``finally`` blocks run."""
        if self._at is not None:
            self._at.cancel()
        self._finish()
        return Step("cancelled")

    def _stopped(self) -> Asked[A]:
        if self._at is None:
            raise RuntimeError("okay: a computation answered before its first step")
        return self._at

    def _next(self, go: Callable[[], Done[A] | Asked[A]]) -> Step[A]:
        if self._over:
            raise RuntimeError("okay: a finished computation stepped again")
        try:
            s = go()
        except Exception as e:
            self._finish()
            return Step("failed", failure=e)
        if isinstance(s, Done):
            self._finish()
            return Step("done", value=s.value)
        if not any(s.cap is c for c in self._host.caps):
            s.cancel()
            self._finish()
            return Step("failed", failure=RuntimeError(f"okay: {type(s.op).__name__}: no handler owns {s.cap}"))
        self._at = s
        return Step("yielded", op=s.op)

    def _finish(self) -> None:
        self._over = True
        for c in self._host.caps:
            c.live = False


def start(body: Callable[[Host], Eff[A] | Prog[A]]) -> Computation[A]:
    """Hand body out: nothing runs until the first ``step()``."""
    return Computation(body)
