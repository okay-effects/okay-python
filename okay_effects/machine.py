"""The machine: one loop runs a program, in constant stack.

What waits for a value is a stack of frames, a linked list never written after it is
made. So a part of it is a value: the rest of a program after an operation — the
frames from the operation down to its handler — is kept as it is and laid again each
time it is resumed. Nothing is run again to get there.

One kind of frame is not data: a generator waiting for a value. It moves when it is
resumed and cannot be copied, so a rest that holds one goes on once."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from okay_effects.core import (Abort, Ask, Bind, Cap, Eff, Escaped, Handle, OneShot, Op, Prog, Pure, Raise, Resume,
                               Resuming, Stateful, Unhandled)

A = TypeVar("A")


class Frame:
    """What is done with a value coming back; ``next`` is the frame under it."""

    __slots__ = ("next",)
    next: Frame | None


class Then(Frame):
    """A bind waiting: ``f`` of the value is the program that follows."""

    __slots__ = ("f",)

    def __init__(self, f: Any, next: Frame | None) -> None:
        self.f = f
        self.next = next

    def above(self, next: Frame | None) -> Frame:
        return Then(self.f, next)


class Running(Frame):
    """A generator waiting for the value of what it yielded."""

    __slots__ = ("gen",)

    def __init__(self, gen: Generator[Any, Any, Any], next: Frame | None) -> None:
        self.gen = gen
        self.next = next

    def above(self, next: Frame | None) -> Frame:
        return Running(self.gen, next)


class Handled(Frame):
    """A handler around what is above it: its capability, its node, its state."""

    __slots__ = ("cap", "node", "state")

    def __init__(self, cap: Cap[Any], node: Any, state: Any, next: Frame | None) -> None:
        self.cap = cap
        self.node = node
        self.state = state
        self.next = next

    def above(self, next: Frame | None) -> Frame:
        return Handled(self.cap, self.node, self.state, next)


class Answered(Frame):
    """A ``handle`` clause at work: its value answers the operation ``rest`` waits at."""

    __slots__ = ("rest",)

    def __init__(self, rest: Rest, next: Frame | None) -> None:
        self.rest = rest
        self.next = next

    def above(self, next: Frame | None) -> Frame:
        return Answered(self.rest, next)


class Rest:
    """The rest of a program after an operation, as a value: the frames from ``top`` down
    to its handler's, ``hand``. ``once``: a generator waits in it. Called with the
    operation's answer it is the program that goes on from there — the ``k`` of ``resuming``."""

    __slots__ = ("top", "hand", "once")

    def __init__(self, top: Frame, hand: Handled, once: bool) -> None:
        self.top = top
        self.hand = hand
        self.once = once

    def __call__(self, answer: Any) -> Resume[Any]:
        return Resume(self, answer)


def relay(rest: Rest, below: Frame | None, state: Any) -> Frame:
    """The frames of ``rest`` laid again above ``below``, its handler holding ``state``."""
    hand = rest.hand
    if hand.next is below and hand.state is state:
        return rest.top  # nothing moved: the frames as they are
    part = []
    f: Any = rest.top
    while f is not hand:
        part.append(f)
        f = f.next
    stack: Frame = Handled(hand.cap, hand.node, state, below)
    for f in reversed(part):
        stack = f.above(stack)
    return stack


def unwind(top: Frame | None, until: Frame | None) -> None:
    """The frames from ``top`` down to ``until`` (not it) left for good, the innermost
    first: each generator closed — its ``finally`` blocks run."""
    f: Any = top
    while f is not until:
        if type(f) is Running:
            f.gen.close()
        elif type(f) is Handled:
            f.cap.live = False
        elif type(f) is Answered:
            unwind(f.rest.top, f.rest.hand.next)
        f = f.next


@dataclass(frozen=True, slots=True)
class Done(Generic[A]):
    """The program came to its value."""

    value: A


class Asked(Generic[A]):
    """The program stopped at an operation no handler in it owns. Whoever runs it
    answers: ``resume``, ``fail`` or ``cancel`` — one of them, once."""

    __slots__ = ("cap", "op", "_stack")

    def __init__(self, cap: Cap[Any], op: Op[Any], stack: Frame | None) -> None:
        self.cap = cap
        self.op = op
        self._stack = stack

    def resume(self, answer: Any) -> Done[A] | Asked[A]:
        """On, with the operation's answer."""
        return _loop(_RETURN, answer, self._stack)

    def fail(self, error: BaseException) -> Done[A] | Asked[A]:
        """On, the error raised where the operation was performed."""
        return _loop(_FAIL, error, self._stack)

    def cancel(self) -> None:
        """Left where it is: its generators' ``finally`` blocks run."""
        unwind(self._stack, None)


def step(program: Eff[A] | Prog[A]) -> Done[A] | Asked[A]:
    """Run a program to its value or to the first operation that is not handled in it.
    A failure nothing in the program catches is raised."""
    return _loop(_RUN, program, None)


def run(program: Eff[A] | Prog[A]) -> A:
    """Run a program whose every operation is handled inside it."""
    s = step(program)
    if isinstance(s, Done):
        return s.value
    s.cancel()
    raise Unhandled(f"{type(s.op).__name__}: no live handler owns {s.cap}")


_RUN, _RETURN, _FAIL = 0, 1, 2


def _loop(mode: int, x: Any, stack: Frame | None) -> Done[Any] | Asked[Any]:
    """``x`` is the program to run (_RUN), the value going back to the top frame (_RETURN),
    or the error going down the stack to the nearest generator (_FAIL)."""
    while True:
        if mode == _FAIL:
            f: Any = stack
            if f is None:
                raise x
            if type(f) is Running:  # raised in the generator where it waits: its except may go on
                try:
                    x = f.gen.throw(x)
                    mode = _RUN
                except StopIteration as done:
                    stack, mode, x = f.next, _RETURN, done.value
                except Exception as e:
                    stack, x = f.next, e
            else:
                stack = f.next
                if type(f) is Handled:
                    f.cap.live = False
                elif type(f) is Answered:  # the clause failed: the body it was answering is left
                    unwind(f.rest.top, f.rest.hand.next)
            continue
        try:
            if mode == _RETURN:
                f = stack
                if f is None:
                    return Done(x)
                t = type(f)
                if t is Then:
                    stack = f.next
                    x = f.f(x)
                    mode = _RUN
                elif t is Running:  # the frame stays while its generator goes on
                    try:
                        x = f.gen.send(x)
                        mode = _RUN
                    except StopIteration as done:
                        stack, x = f.next, done.value
                    except Exception as e:
                        stack, mode, x = f.next, _FAIL, e
                elif t is Handled:  # the body's value: the handler returns
                    f.cap.live = False
                    stack = f.next
                    if type(f.node) is Stateful:
                        x = (f.state, x)
                    elif type(f.node) is Resuming:
                        x = f.node.ret(x)
                        mode = _RUN
                else:  # Answered: the clause's value answers the operation
                    rest = f.rest
                    if type(x) is Abort:
                        unwind(rest.top, rest.hand.next)
                        stack, x = f.next, x.value
                    else:
                        stack = relay(rest, f.next, rest.hand.state)
                        if type(x) is Raise:
                            mode, x = _FAIL, x.error
                continue
            # _RUN
            t = type(x)
            if t is Pure:
                mode, x = _RETURN, x.value
            elif t is Bind:
                stack = Then(x.next, stack)
                x = x.first
            elif t is Ask:
                # the nearest handler that owns the capability, and whether a generator waits on the way
                cap, once = x.cap, False
                f = stack
                while f is not None:
                    t = type(f)
                    if t is Handled:
                        if f.cap is cap:
                            break
                    elif t is Running or (t is Answered and f.rest.once):
                        once = True
                    f = f.next
                if f is None:
                    if not cap.live:
                        raise Escaped(f"{cap}: used outside its handler, the capability escaped its scope")
                    return Asked(cap, x.op, stack)
                assert stack is not None
                node = f.node
                t = type(node)
                if t is Handle:  # the clause runs outside its handler, the rest waiting under it
                    stack = Answered(Rest(stack, f, once), f.next)
                    x = node.answer(x.op)
                elif t is Stateful:  # answered in place, the new state in the handler's frame
                    answer, state = node.step(f.state, x.op)
                    stack = relay(Rest(stack, f, once), f.next, state)
                    mode, x = _RETURN, answer
                else:  # Resuming: the clause is given the rest
                    if once:
                        raise OneShot(
                            f"okay: {type(x.op).__name__} performed from a generator under a handler that resumes "
                            "many times: a generator's rest goes on once; write that part as data (pure, bind)")
                    rest = Rest(stack, f, False)
                    stack = f.next
                    x = node.on(x.op, rest)
            elif t is Resume:
                stack = relay(x.rest, stack, x.rest.hand.state)
                mode, x = _RETURN, x.value
            elif t is Handle or t is Stateful or t is Resuming:
                cap = Cap(x.kind)
                stack = Handled(cap, x, x.state if t is Stateful else None, stack)
                node, x = x, x.body(cap)
                if t is Resuming and not isinstance(x, Prog):
                    raise OneShot(
                        f"okay: a generator as the body of a handler that resumes many times ({node.kind.__name__}): "
                        "a generator's rest goes on once; write the body as data (pure, bind)")
            elif isinstance(x, Generator):
                stack = Running(x, stack)
                mode, x = _RETURN, None
            else:
                raise TypeError(f"okay: not a program: {x!r}")
        except Exception as e:
            mode, x = _FAIL, e
