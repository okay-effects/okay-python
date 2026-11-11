"""Answers given in place — ``answering`` and ``stateful`` — answered without taking the
rest. These pin that they mean what the general clause means, where it is subtle."""

import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from okay_effects import (Abort, Cap, Data, Eff, Entry, Op, Raise, Routes, all_paths, answering, drive, handle, pure,
                          record, resuming, run, start, stateful, with_)

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class Ask(Op[T], Generic[T]):
    pass


@dataclass(frozen=True)
class Tell(Op[None]):
    word: str


@dataclass(frozen=True)
class Among(Op[T], Generic[T]):
    options: tuple[T, ...]


class Cell(Op[R], Generic[R]):
    pass


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


def clause(f: Callable[[Any], Any]) -> Callable[[Any], Data[Any]]:
    """The same answer as a general clause: a program, the rest taken."""
    return lambda op: pure(f(op))


class TestInPlace(unittest.TestCase):
    def test_in_place_handlers_under_a_choice_each_path_its_own_state(self) -> None:
        told: list[str] = []

        def tell(op: Tell) -> None:
            told.append(op.word)

        def p(c: Cap[Among[int]], s: Cap[Cell[Any]], t: Cap[Tell], a: Cap[Ask[int]]) -> Data[tuple[int, int]]:
            step = c.perform(Among((1, 2))).bind(
                lambda x: a.perform(Ask[int]()).bind(
                    lambda base: s.perform(Get()).bind(
                        lambda n: s.perform(Put(n + x + base)).then(t.perform(Tell(f"+{x}"))))))
            # two choices: four paths; each sees the state as it was where its rest was taken
            return step.then(step).then(s.perform(Get())).bind(lambda n: a.perform(Ask[int]()).map(lambda b: (n, b)))

        # the choice outermost; the in-place handlers inside it, in its rest
        got = run(all_paths(Among, options, lambda c: stateful(
            Cell, 0, cell, lambda s: answering(Tell, tell, lambda t: answering(Ask, lambda _: 100, lambda a: p(
                c, s, t, a))))))
        self.assertEqual(got, [(202, (202, 100)), (203, (203, 100)), (203, (203, 100)), (204, (204, 100))])
        self.assertEqual(told, ["+1", "+1", "+2", "+2", "+1", "+2"])

    def test_a_rest_resumed_again_later_sees_the_state_as_it_was_taken(self) -> None:
        # the first resumption goes on in the rest's own frames (tail position, nothing copied) and
        # changes the state there; the rest, kept and resumed after its handler returned, must not see it
        kept: list[Callable[[Any], Data[Any]]] = []

        def on(op: Among[int], k: Callable[[Any], Data[Any]]) -> Data[Any]:
            kept.append(k)
            return k(0)

        def body(c: Cap[Among[int]]) -> Data[tuple[int, int]]:
            def inside(s: Cap[Cell[Any]]) -> Data[int]:
                return c.perform(Among((0, 1))).bind(
                    lambda x: s.perform(Get()).bind(lambda n: s.perform(Put(n + x + 1)))).then(s.perform(Get()))

            together: Data[tuple[int, int]] = stateful(Cell, 10, cell, inside)
            return together

        got = run(resuming(Among, on, pure, body).bind(lambda first: kept[0](1).map(lambda again: (first, again))))
        self.assertEqual(got, ((11, 11), (12, 12)))

    def test_one_state_through_every_path_when_around_the_choice(self) -> None:
        # the state handler around the choice, an in-place handler of another effect between them
        def p(c: Cap[Among[int]], s: Cap[Cell[Any]], a: Cap[Ask[int]]) -> Data[int]:
            return c.perform(Among((1, 2, 3))).bind(
                lambda x: a.perform(Ask[int]()).bind(
                    lambda k: s.perform(Get()).bind(lambda n: s.perform(Put(n + x * k)).then(pure(n + x * k)))))

        got = run(stateful(Cell, 0, cell, lambda s: answering(Ask, lambda _: 10, lambda a: all_paths(
            Among, options, lambda c: p(c, s, a)))))
        self.assertEqual(got, (60, [10, 30, 60]))

    def test_an_operation_passes_in_place_handlers_of_other_effects_to_its_own(self) -> None:
        # Among is answered by the resuming handler outermost: its rest holds the in-place handlers
        # it passed, and each resumption goes on under them
        def p(c: Cap[Among[int]], a: Cap[Ask[int]], s: Cap[Cell[Any]]) -> Data[int]:
            return a.perform(Ask[int]()).bind(lambda base: c.perform(Among((1, 2))).bind(
                lambda x: s.perform(Get()).bind(lambda n: s.perform(Put(n + x)).then(
                    a.perform(Ask[int]()).map(lambda b: base + b + n + x)))))

        def on(op: Among[int], k: Callable[[Any], Data[list[int]]]) -> Data[list[int]]:
            return k(op.options[0]).bind(lambda xs: k(op.options[1]).map(lambda ys: xs + ys))

        got = run(resuming(Among, on, lambda v: pure([v]), lambda c: answering(Ask, lambda _: 7, lambda a: stateful(
            Cell, 5, cell, lambda s: p(c, a, s).map(lambda v: v)))))
        self.assertEqual(got, [(6, 20), (7, 21)])  # the state beside each

    def test_in_place_in_a_generator_a_one_shot_handler_holds(self) -> None:
        # the general clause holds the body's rest while it runs; the clause's own operations,
        # and the body's after it, are answered in place
        log: list[str] = []

        def tell(op: Tell) -> None:
            log.append(op.word)

        def on_ask(t: Cap[Tell], s: Cap[Cell[Any]]) -> Callable[[Ask[int]], Eff[int]]:
            def answer(_: Ask[int]) -> Eff[int]:
                yield from t.perform(Tell("clause"))
                n = yield from s.perform(Get())
                yield from s.perform(Put(n + 1))
                return n * 10

            return answer

        def body(a: Cap[Ask[int]], t: Cap[Tell], s: Cap[Cell[Any]]) -> Eff[int]:
            total = 0
            for i in range(3):
                yield from t.perform(Tell(f"body {i}"))
                total += yield from a.perform(Ask[int]())
                n = yield from s.perform(Get())
                total += n
            return total

        got = run(answering(Tell, tell, lambda t: stateful(Cell, 1, cell, lambda s: handle(
            Ask, on_ask(t, s), lambda a: body(a, t, s)))))
        self.assertEqual(got, (4, 10 + 2 + 20 + 3 + 30 + 4))
        self.assertEqual(log, ["body 0", "clause", "body 1", "clause", "body 2", "clause"])

    def test_abort_raise_and_failure_from_answering_mean_what_the_clause_means(self) -> None:
        def body(a: Cap[Ask[Any]], seen: list[str]) -> Eff[str]:
            try:
                try:
                    yield from a.perform(Ask[Any]())
                    return "on"
                except Exception as e:
                    seen.append(f"except {e}")
                    return "caught"
            finally:
                seen.append("finally")

        boom = ValueError("boom")

        def fails(_: Any) -> Any:
            raise boom

        for f in (lambda _: Abort("left"), lambda _: Raise(boom), fails):
            in_place: list[str] = []
            general: list[str] = []

            def go(h: Any, seen: list[str]) -> Any:
                try:
                    return run(h(Ask, f, lambda a: body(a, seen)))
                except ValueError as e:
                    return f"out {e}"

            self.assertEqual(go(answering, in_place), go(lambda k, g, b: handle(k, clause(g), b), general))
            self.assertEqual(in_place, general)
        # and what that is: Abort leaves the body, Raise is raised in it, a failure leaves it and is raised outside
        self.assertEqual(general, ["finally"])

    def test_a_host_answers_past_in_place_handlers(self) -> None:
        log: list[Entry] = []
        rs = Routes()
        rs.on(Get, lambda _: 40)

        def body(host: Cap[Get], a: Cap[Ask[int]]) -> Eff[int]:
            x = yield from a.perform(Ask[int]())
            y = yield from host.perform(Get())
            z = yield from a.perform(Ask[int]())
            return x + y + z

        handed = start(lambda h: answering(Ask, lambda _: 1, lambda a: body(h.outside(Get), a)))
        self.assertEqual(drive(handed, with_(rs, record(log))), 42)
        self.assertEqual(log, [Entry(Get(), 40)])

    def test_long_loops_in_constant_stack_at_the_default_limit(self) -> None:
        self.assertLessEqual(sys.getrecursionlimit(), 1000)
        n = 200_000

        def gen(a: Cap[Ask[int]], s: Cap[Cell[Any]]) -> Eff[int]:
            for _ in range(n):
                x = yield from a.perform(Ask[int]())
                v = yield from s.perform(Get())
                yield from s.perform(Put(v + x))
            return (yield from s.perform(Get()))

        def data(a: Cap[Ask[int]], s: Cap[Cell[Any]], i: int) -> Data[int]:
            if i == n:
                return s.perform(Get())
            return a.perform(Ask[int]()).bind(lambda x: s.perform(Get()).bind(
                lambda v: s.perform(Put(v + x)))).bind(lambda _: data(a, s, i + 1))

        self.assertEqual(run(answering(Ask, lambda _: 1, lambda a: stateful(Cell, 0, cell, lambda s: gen(a, s)))),
                         (n, n))
        self.assertEqual(run(answering(Ask, lambda _: 1, lambda a: stateful(Cell, 0, cell, lambda s: data(a, s, 0)))),
                         (n, n))


if __name__ == "__main__":
    unittest.main()
