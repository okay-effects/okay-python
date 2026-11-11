"""Operations, capabilities, programs, handlers: what a program is made of. ``machine`` runs it."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, overload

R = TypeVar("R")
A = TypeVar("A")
B = TypeVar("B")
S = TypeVar("S")
O = TypeVar("O", bound="Op[Any]")
O_contra = TypeVar("O_contra", bound="Op[Any]", contravariant=True)


class Op(Generic[R]):
    """An operation answering R: subclass it, usually as a frozen dataclass.

    ``class Reserve(Op[bool])`` answers a bool."""

    __slots__ = ()


class Prog(Generic[A]):
    """A program answering A, as the machine runs it. A generator binds its value with
    ``yield from``. Its type does not say what is inside: a generator may wait in it,
    and a generator cannot be copied, so the rest of a Prog goes on once."""

    __slots__ = ()

    def __iter__(self) -> Generator[Prog[Any], Any, A]:
        a: A = yield self  # the machine sends back this program's value
        return a


class Data(Prog[A]):
    """A program that is data all through: a value, an operation, or a program and what
    follows it — a function of its value. No generator in it, so the rest of it after an
    operation is a value too, and a handler may resume that rest any number of times."""

    __slots__ = ()

    def bind(self, f: Callable[[A], Data[B]]) -> Data[B]:
        """This program, then the program f makes of its value."""
        return Bind(self, f)

    def map(self, f: Callable[[A], B]) -> Data[B]:
        """This program, its value changed by f."""
        return Bind(self, lambda a: Pure(f(a)))

    def then(self, p: Data[B]) -> Data[B]:
        """This program for its effects, then p."""
        return Bind(self, lambda _: p)


Eff = Generator[Prog[Any], Any, A]
"""A program answering A written as a generator, performing operations with ``yield from``.
Its rest is the generator's own state: resumed once."""


class Escaped(Exception):
    """A capability used after its handler returned."""


class Unhandled(Exception):
    """An operation no live handler owns."""


class OneShot(Exception):
    """A generator where a handler that resumes many times needs data."""


class Cap(Generic[O_contra]):
    """A capability: made by a handler, handed to its body; the body performs through it.
    Contravariant: a capability taking any operation serves where a Cap[Reserve] is wanted."""

    __slots__ = ("_kind", "live")

    def __init__(self, kind: type[O_contra]) -> None:
        self._kind: type[Any] = kind
        self.live = True

    def perform(self, op: Op[R]) -> Data[R]:
        """The operation as a program, answered by this capability's owner:
        ``x = yield from cap.perform(op)`` in a generator, ``cap.perform(op).bind(f)`` as data."""
        if not isinstance(op, self._kind):
            raise TypeError(f"{type(op).__name__} through a capability for {self._kind.__name__}")
        return Ask(self, op)

    def __repr__(self) -> str:
        return f"Cap({self._kind.__name__})@{id(self):x}"


# ---- the data: each a value the machine reads; making one runs nothing


@dataclass(frozen=True, eq=False, slots=True)
class Pure(Data[A]):
    """A value."""

    value: A


@dataclass(frozen=True, eq=False, slots=True)
class Ask(Data[R]):
    """An operation on its way to the handler that owns its capability."""

    cap: Cap[Any]
    op: Op[R]


@dataclass(frozen=True, eq=False, slots=True)
class Bind(Data[B]):
    """A program, and what follows it: a function of its value."""

    first: Prog[Any]
    next: Callable[[Any], Any]


@dataclass(frozen=True, eq=False, slots=True)
class Handle(Data[A]):
    """A program under a handler whose clause is a program answering the operation."""

    kind: type[Any]
    answer: Callable[[Any], Any]
    body: Callable[[Cap[Any]], Any]


@dataclass(frozen=True, eq=False, slots=True)
class Stateful(Data[A]):
    """A program under a handler that answers from a state."""

    kind: type[Any]
    state: Any
    step: Callable[[Any, Any], tuple[Any, Any]]
    body: Callable[[Cap[Any]], Any]


@dataclass(frozen=True, eq=False, slots=True)
class Resuming(Data[A]):
    """A program under a handler whose clause is given the rest of the program."""

    kind: type[Any]
    on: Callable[[Any, Callable[[Any], Any]], Any]
    ret: Callable[[Any], Any]
    body: Callable[[Cap[Any]], Any]


@dataclass(frozen=True, eq=False, slots=True)
class Resume(Data[A]):
    """The rest of a program taken at an operation, given the operation's answer."""

    rest: Any
    value: Any


def pure(a: A) -> Data[A]:
    """A program answering a, performing nothing."""
    return Pure(a)


# ---- handlers. Each serves a body of either form; the result is Data when all its parts are


@dataclass(frozen=True, slots=True)
class Abort:
    """As an answer: the body stops where it performed (its ``finally`` blocks run)
    and the handler's value is ``value`` — never resumed (the kernel's Aborting)."""

    value: Any


@dataclass(frozen=True, slots=True)
class Raise:
    """As an answer: ``error`` is raised in the body where the operation was performed."""

    error: BaseException


@overload
def handle(kind: type[O], answer: Callable[[O], Data[Any]], body: Callable[[Cap[O]], Data[A]]) -> Data[A]: ...
@overload
def handle(kind: type[O], answer: Callable[[O], Eff[Any] | Prog[Any]],
           body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[A]: ...
def handle(kind: type[O], answer: Callable[[O], Eff[Any] | Prog[Any]],
           body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[A]:
    """Make a capability for ``kind``, run ``body`` with it, answer each of its operations
    with ``answer`` — itself a program, so it may perform through capabilities from
    outside (a component built on another), and may answer ``Abort(v)`` or ``Raise(e)``.
    Operations of other capabilities pass outward, unchanged. The body goes on once."""
    return Handle(kind, answer, body)


@overload
def answering(kind: type[O], f: Callable[[O], Any], body: Callable[[Cap[O]], Data[A]]) -> Data[A]: ...
@overload
def answering(kind: type[O], f: Callable[[O], Any], body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[A]: ...
def answering(kind: type[O], f: Callable[[O], Any], body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[A]:
    """``handle`` for a plain answer: f answers each operation (Answering)."""
    return Handle(kind, lambda op: Pure(f(op)), body)


@overload
def returning(kind: type[O], answer: Callable[[O], Data[Any]], ret: Callable[[A], B],
              body: Callable[[Cap[O]], Data[A]]) -> Data[B]: ...
@overload
def returning(kind: type[O], answer: Callable[[O], Eff[Any] | Prog[Any]], ret: Callable[[A], B],
              body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[B]: ...
def returning(kind: type[O], answer: Callable[[O], Eff[Any] | Prog[Any]], ret: Callable[[A], B],
              body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[B]:
    """The handler's value made from its body's (Returning)."""
    return Bind(Handle(kind, answer, body), lambda a: Pure(ret(a)))


@overload
def stateful(kind: type[O], state: S, step: Callable[[S, O], tuple[Any, S]],
             body: Callable[[Cap[O]], Data[A]]) -> Data[tuple[S, A]]: ...
@overload
def stateful(kind: type[O], state: S, step: Callable[[S, O], tuple[Any, S]],
             body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[tuple[S, A]]: ...
def stateful(kind: type[O], state: S, step: Callable[[S, O], tuple[Any, S]],
             body: Callable[[Cap[O]], Eff[A] | Prog[A]]) -> Prog[tuple[S, A]]:
    """A handler with a state: ``step(state, op)`` gives the answer and the next state;
    the value is the last state beside the body's. The state is a value kept where the
    handler stands in the program, so a rest taken above it holds the state as it was:
    under a handler that resumes many times each path has its own state; around such
    a handler one state goes through all its paths. Do not mutate it."""
    return Stateful(kind, state, step, body)


def resuming(kind: type[O], on: Callable[[O, Callable[[Any], Data[B]]], Data[B]], ret: Callable[[A], Data[B]],
             body: Callable[[Cap[O]], Data[A]]) -> Data[B]:
    """The general handler: ``on(op, k)`` is given the rest of the body as a value, ``k``,
    and its program is the handler's value — it may resume ``k(answer)`` once, never, or
    many times; ``ret`` makes the handler's value of the body's. Every resumption runs
    under this handler again. The rest must be data: a body that is a generator is
    refused, and so is an operation performed from a generator anywhere inside."""
    return Resuming(kind, on, ret, body)
