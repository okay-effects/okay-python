"""Async and parallel branches on asyncio: the business code stays a plain generator;
only the runner at the top is async. No coloured functions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from okay_effects.core import Cap, Data, Eff, Op, Prog, Unhandled
from okay_effects.machine import Asked, step

T = TypeVar("T")
A = TypeVar("A")


@dataclass(frozen=True, eq=False, slots=True)
class Await(Op[T], Generic[T]):
    """Wait for an awaitable: the async runner awaits it, the program resumes with its value."""

    awaitable: Awaitable[T]


Async = Cap[Await[Any]]


def wait(cap: Async, aw: Awaitable[T]) -> Data[T]:
    """``x = yield from wait(io, fetch())``"""
    return cap.perform(Await(aw))


def sleep(cap: Async, seconds: float) -> Data[None]:
    return wait(cap, asyncio.sleep(seconds))


async def run_async(body: Callable[[Async], Eff[A] | Prog[A]]) -> A:
    """Run body on the event loop, its Awaits awaited; cancelled, its ``finally`` blocks run.
    Every other operation must be handled inside body."""
    cap: Async = Cap(Await)
    try:
        s = step(body(cap))
        while isinstance(s, Asked):
            if s.cap is not cap:
                s.cancel()
                raise Unhandled(f"{type(s.op).__name__}: a branch answers only its own handlers and Await")
            try:
                v = await cast(Await[Any], s.op).awaitable
            except Exception as e:
                s = s.fail(e)
            except BaseException:  # cancelled
                s.cancel()
                raise
            else:
                s = s.resume(v)
        return s.value
    finally:
        cap.live = False


def all_of(cap: Async, *branches: Callable[[Async], Eff[A] | Prog[A]]) -> Data[list[A]]:
    """Branches at once; a failure in one cancels the others and is raised here."""

    async def together() -> list[A]:
        try:
            async with asyncio.TaskGroup() as g:
                tasks = [g.create_task(run_async(b)) for b in branches]
        except ExceptionGroup as eg:
            raise eg.exceptions[0] from None  # the branch's own failure, as if it were a call
        return [t.result() for t in tasks]

    return wait(cap, together())


def race(cap: Async, *branches: Callable[[Async], Eff[A] | Prog[A]]) -> Data[A]:
    """The first answer wins; the others are cancelled, their ``finally`` blocks run."""

    async def first() -> A:
        tasks = [asyncio.create_task(run_async(b)) for b in branches]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return next(iter(done)).result()

    return wait(cap, first())
