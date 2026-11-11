"""Time per operation on the paths that matter, best of several rounds.

    python bench/bench.py                 # this checkout
    python bench/bench.py --lib DIR       # okay_effects from another checkout (an older commit)

A lane the library at DIR cannot run (no data programs, no ``stateful``) is printed as n/a."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ap = argparse.ArgumentParser()
ap.add_argument("--lib", help="a checkout whose okay_effects to measure")
ap.add_argument("-n", type=int, default=200_000, help="operations per lane")
ap.add_argument("-r", "--rounds", type=int, default=5)
args = ap.parse_args()
sys.path.insert(0, args.lib or __file__.rsplit("/bench/", 1)[0])

import okay_effects as ok  # noqa: E402

N: int = args.n
has_data = hasattr(ok, "Data")


@dataclass(frozen=True)
class Tick(ok.Op[int]):
    pass


@dataclass(frozen=True)
class Other(ok.Op[int]):
    pass


class Cell(ok.Op[Any]):
    pass


@dataclass(frozen=True)
class Get(Cell):
    pass


@dataclass(frozen=True)
class Put(Cell):
    value: int


def cell(s: int, op: Cell) -> tuple[Any, int]:
    return (None, op.value) if isinstance(op, Put) else (s, s)


def gen_loop(c: Any) -> Any:
    t = Tick()
    s = 0
    for _ in range(N):
        s += yield from c.perform(t)
    return s


def gen_answered() -> Any:
    return ok.run(ok.answering(Tick, lambda _: 1, gen_loop))


def gen_three_away() -> Any:
    # Tick passes two handlers of another effect on its way to its own
    return ok.run(ok.answering(Tick, lambda _: 1, lambda c: ok.answering(
        Other, lambda _: 2, lambda _o1: ok.answering(Other, lambda _: 3, lambda _o2: gen_loop(c)))))


def gen_state() -> Any:
    def body(c: Any) -> Any:
        g, n = Get(), N // 2
        for _ in range(n):
            s = yield from c.perform(g)
            yield from c.perform(Put(s + 1))
        return n

    return ok.run(ok.stateful(Cell, 0, cell, body))


def data_loop(c: Any) -> Any:
    t = Tick()

    def go(i: int, s: int) -> Any:
        if i == N:
            return ok.pure(s)
        return c.perform(t).bind(lambda a: go(i + 1, s + a))

    return go(0, 0)


def data_answered() -> Any:
    return ok.run(ok.answering(Tick, lambda _: 1, data_loop))


def data_state() -> Any:
    def body(c: Any) -> Any:
        g, n = Get(), N // 2

        def go(i: int) -> Any:
            if i == n:
                return ok.pure(n)
            return c.perform(g).bind(lambda s: c.perform(Put(s + 1))).bind(lambda _: go(i + 1))

        return go(0)

    return ok.run(ok.stateful(Cell, 0, cell, body))


DEPTH = max(1, N.bit_length() - 2)  # 2**(DEPTH+1) - 2 resumptions, about N


def data_choice() -> Any:
    # every step a choice whose handler resumes the rest twice: all paths of DEPTH choices
    def body(c: Any) -> Any:
        def go(i: int) -> Any:
            return ok.pure(0) if i == DEPTH else c.perform(Tick()).bind(lambda _: go(i + 1))

        return go(0)

    def on(op: Any, k: Any) -> Any:
        return k(0).bind(lambda a: k(1).map(lambda b: a + b))

    return ok.run(ok.resuming(Tick, on, lambda _: ok.pure(1), body))


def binds() -> Any:
    def go(i: int) -> Any:
        return ok.pure(i) if i == N else ok.pure(i + 1).bind(go)

    return ok.run(go(0))


Lane = tuple[str, Callable[[], Any], int, bool]  # name, run, operations, needs data programs
LANES: list[Lane] = [
    ("gen: answered in place", gen_answered, N, False),
    ("gen: answered 3 handlers away", gen_three_away, N, False),
    ("gen: state get/put", gen_state, N, True),
    ("data: answered in place", data_answered, N, True),
    ("data: state get/put", data_state, N, True),
    ("data: choice resumed twice", data_choice, 2 ** (DEPTH + 1) - 2, True),
    ("data: binds alone", binds, N, True),
]


def best(f: Callable[[], Any]) -> float:
    times = []
    for _ in range(args.rounds):
        t0 = time.perf_counter()
        f()
        times.append(time.perf_counter() - t0)
    return min(times)


print(f"python {sys.version.split()[0]}, n={N}, best of {args.rounds}, lib={args.lib or 'this checkout'}")
for name, f, ops, needs_data in LANES:
    if needs_data and not has_data:
        print(f"{name:32} {'n/a':>10}")
        continue
    t = best(f)
    print(f"{name:32} {t / ops * 1e9:8.0f} ns/op")
