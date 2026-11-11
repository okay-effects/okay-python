import asyncio
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from okay_effects import (Async, Cap, Data, Eff, Entry, OneShot, Op, Routes, all_paths, answering, drive, pure,
                          record, resuming, run, run_async, start, stateful, wait, with_)

T = TypeVar("T")
R = TypeVar("R")
A = TypeVar("A")


@dataclass(frozen=True)
class Ask(Op[T], Generic[T]):
    pass


@dataclass(frozen=True)
class Among(Op[T], Generic[T]):
    """a choice: one of these"""

    options: tuple[T, ...]


class Cell(Op[R], Generic[R]):
    """a state of one int"""


@dataclass(frozen=True)
class Get(Cell[int]):
    pass


@dataclass(frozen=True)
class Put(Cell[None]):
    value: int


def cell(s: int, op: Cell[Any]) -> tuple[Any, int]:
    return (None, op.value) if isinstance(op, Put) else (s, s)


def options(op: Among[Any]) -> tuple[Any, ...]:
    return op.options


class TestData(unittest.TestCase):
    def test_one_handler_serves_a_generator_and_data(self) -> None:
        def as_generator(c: Cap[Ask[int]]) -> Eff[int]:
            a = yield from c.perform(Ask[int]())
            b = yield from c.perform(Ask[int]())
            return a * 10 + b

        def as_data(c: Cap[Ask[int]]) -> Data[int]:
            return c.perform(Ask[int]()).bind(lambda a: c.perform(Ask[int]()).map(lambda b: a * 10 + b))

        self.assertEqual(run(answering(Ask, lambda _: 4, as_generator)), 44)
        self.assertEqual(run(answering(Ask, lambda _: 4, as_data)), 44)

    def test_every_path_runs_each_part_of_the_body_once_per_path_through_it(self) -> None:
        runs = {"before": 0, "between": 0, "after": 0}

        def count(part: str, value: A) -> A:
            runs[part] += 1
            return value

        def body(c: Cap[Among[bool]]) -> Data[tuple[bool, bool]]:
            flip = c.perform(Among((True, False)))
            return (pure(None).map(lambda _: count("before", None)).then(flip)
                    .bind(lambda a: pure(count("between", a)).then(flip)
                          .map(lambda b: count("after", (a, b)))))

        got = run(all_paths(Among, options, body))
        self.assertEqual(got, [(True, True), (True, False), (False, True), (False, False)])
        # the part before the first choice ran once: no path began the body again
        # (by replay the same body ran them 7, 6 and 4 times)
        self.assertEqual(runs, {"before": 1, "between": 2, "after": 4})

    def test_every_path_of_two_choices(self) -> None:
        def body(c: Cap[Among[int]]) -> Data[tuple[int, int]]:
            return c.perform(Among((1, 2, 3))).bind(lambda x: c.perform(Among((10, 20))).map(lambda y: (x, y)))

        self.assertEqual(run(all_paths(Among, options, body)), [(1, 10), (1, 20), (2, 10), (2, 20), (3, 10), (3, 20)])

    def test_state_each_path_its_own_or_one_through_all(self) -> None:
        def p(choice: Cap[Among[int]], state: Cap[Cell[Any]]) -> Data[int]:
            return choice.perform(Among((1, 2))).bind(
                lambda x: state.perform(Get()).bind(
                    lambda s: state.perform(Put(s + x)).then(pure(s + x))))

        # the state inside the choice: it is in the rest the choice resumes, as it was there
        inside = all_paths(Among, options, lambda c: stateful(Cell, 0, cell, lambda s: p(c, s)))
        self.assertEqual(run(inside), [(1, 1), (2, 2)])
        # the state around the choice: not in the rest, so one state goes through both paths
        around = stateful(Cell, 0, cell, lambda s: all_paths(Among, options, lambda c: p(c, s)))
        self.assertEqual(run(around), (3, [1, 3]))

    def test_the_rest_is_a_value_resumed_never_once_or_twice(self) -> None:
        def body(c: Cap[Ask[int]]) -> Data[int]:
            return c.perform(Ask[int]()).map(lambda a: a + 1)

        def under(on: Callable[[Ask[int], Callable[[Any], Data[str]]], Data[str]]) -> str:
            return run(resuming(Ask, on, lambda a: pure(str(a)), body))

        self.assertEqual(under(lambda op, k: pure("left")), "left")
        self.assertEqual(under(lambda op, k: k(1)), "2")
        self.assertEqual(under(lambda op, k: k(1).bind(lambda x: k(10).map(lambda y: x + y))), "211")

    def test_a_hundred_thousand_binds_in_constant_stack(self) -> None:
        n = 100_000
        left: Data[int] = pure(0)
        for _ in range(n):
            left = left.map(lambda x: x + 1)  # ((pure, f), f), f …: the nesting to the left
        self.assertEqual(run(left), n)

        def right(i: int) -> Data[int]:  # pure, (f, (f, …)): each bind made by the one before
            return pure(i) if i == n else pure(i + 1).bind(right)

        self.assertEqual(run(right(0)), n)

        def count(state: Cap[Cell[Any]], i: int) -> Data[int]:
            if i == n:
                return state.perform(Get())
            return state.perform(Get()).bind(lambda s: state.perform(Put(s + 1))).bind(lambda _: count(state, i + 1))

        self.assertEqual(run(stateful(Cell, 0, cell, lambda s: count(s, 0))), (n, n))

        # and resumed twice at the end of them: every path goes back through all the frames
        def choose_last(c: Cap[Among[int]]) -> Data[int]:
            return left.bind(lambda x: c.perform(Among((1, 2))).map(lambda y: x + y))

        self.assertEqual(run(all_paths(Among, options, choose_last)), [n + 1, n + 2])

    def test_a_generator_as_the_body_is_refused_before_it_runs(self) -> None:
        ran: list[str] = []

        def body(c: Cap[Among[int]]) -> Eff[int]:
            ran.append("body")
            return (yield from c.perform(Among((1, 2))))

        with self.assertRaisesRegex(OneShot, "a generator as the body of a handler that resumes many times"):
            run(all_paths(Among, options, body))  # type: ignore[arg-type]  # the types refuse it too
        self.assertEqual(ran, [])

    def test_a_generator_inside_is_refused_at_its_operation(self) -> None:
        ran: list[str] = []

        def asks(c: Cap[Among[int]]) -> Callable[[Cap[Ask[int]]], Eff[int]]:
            def body(a: Cap[Ask[int]]) -> Eff[int]:
                ran.append("before")
                x = yield from c.perform(Among((1, 2)))
                ran.append("after")
                return x

            return body

        # a handler over a generator is a Prog, not Data: the types refuse it; forced, the run does
        def forced(c: Cap[Among[int]]) -> Data[int]:
            return answering(Ask, lambda _: 0, asks(c))  # type: ignore[return-value]

        with self.assertRaisesRegex(OneShot, "Among performed from a generator"):
            run(all_paths(Among, options, forced))
        self.assertEqual(ran, ["before"])

    def test_a_generator_around_a_choice_handled_inside_it(self) -> None:
        def body(c: Cap[Among[int]]) -> Data[int]:
            return c.perform(Among((1, 2))).map(lambda x: x * 10)

        def main(a: Cap[Ask[int]]) -> Eff[int]:
            base = yield from a.perform(Ask[int]())
            paths = yield from all_paths(Among, options, body)  # the rest taken ends at the handler: data
            return base + sum(paths)

        self.assertEqual(run(answering(Ask, lambda _: 100, main)), 130)

    def test_a_host_and_its_middleware_see_every_path(self) -> None:
        log: list[Entry] = []
        rs = Routes()
        rs.on(Ask, lambda _: 5)

        def body(outside: Cap[Ask[int]], c: Cap[Among[int]]) -> Data[int]:
            return c.perform(Among((1, 2))).bind(lambda x: outside.perform(Ask[int]()).map(lambda y: x + y))

        handed = start(lambda h: all_paths(Among, options, lambda c: body(h.outside(Ask), c)))
        self.assertEqual(drive(handed, with_(rs, record(log))), [6, 7])
        self.assertEqual(len(log), 2)  # the host answered once on each path

    def test_async_on_every_path(self) -> None:
        async def fetch(x: int) -> int:
            await asyncio.sleep(0)
            return x * 10

        def main(io: Async) -> Data[list[int]]:
            # the awaitable is made inside the bind, once for each path: Python awaits one once
            return all_paths(Among, options, lambda c: c.perform(Among((1, 2))).bind(lambda x: wait(io, fetch(x))))

        self.assertEqual(asyncio.run(run_async(main)), [10, 20])


if __name__ == "__main__":
    unittest.main()
