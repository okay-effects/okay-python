# okay-python guide

Every snippet is from a test (`python3 -m unittest discover -s tests -t .`).

1. [Operations, capabilities, handlers](#1-operations-capabilities-handlers)
2. [Handler shapes: answer, translate, abort, raise, return](#2-handler-shapes)
3. [The shop](#3-the-shop)
4. [The kernel's protocol](#4-the-kernels-protocol)
5. [Middleware: monitor, log, authorize, record, replay](#5-middleware)
6. [Async and parallel branches](#6-async-and-parallel-branches)
7. [Programs as data, and every path](#7-programs-as-data-and-every-path)
8. [Over the wire, with okay](#8-over-the-wire-with-okay)
9. [Typing, and what Python cannot do](#9-typing-and-limits)

## 1. Operations, capabilities, handlers

An operation is a class deriving `Op[R]`, `R` being what it answers:

```python
@dataclass(frozen=True)
class Reserve(Op[bool]):
    item: str
```

A program answering `A` is a generator, `Eff[A]`. It performs through a
capability with `yield from`, and the result is typed `R`:

```python
def reserve(self, item: str) -> Eff[bool]:
    return (yield from self.cap.perform(Reserve(item)))
```

A handler makes a `Cap`, hands it to its body, answers its operations:

```python
def reader(v: T, body: Callable[[Cap[Ask[T]]], Eff[A]]) -> Eff[A]:
    return (yield from answering(Ask, lambda _: v, body))

def prog(outer: Cap[Ask[int]]) -> Eff[int]:
    def inner_body(inner: Cap[Ask[int]]) -> Eff[int]:
        a = yield from outer.perform(Ask[int]())
        b = yield from inner.perform(Ask[int]())
        return a * 10 + b
    return (yield from reader(2, inner_body))

run(reader(1, prog))  # 12: two instances of one effect, told apart by capability
```

`yield from` is to effects what `await` is to coroutines: it marks where the
program may be suspended. `run` runs a program whose every operation is handled
inside it; an unhandled one, or one through an escaped capability, is refused
by name.

`cap.perform(op)` is itself a value, a program of one operation (`Data[R]`): a
generator binds it with `yield from`; §7 binds it with `bind`.

## 2. Handler shapes

`handle(kind, answer, body)`: `answer` is itself a program (`Eff`), so it may

- **answer** — return a value (`answering(kind, f, body)` for a plain function);
- **translate** — perform through outer capabilities (payments over HTTP);
- **abort** — return `Abort(v)`: the body is closed where it performed, its
  `finally` blocks run, the handler's value is `v`;
- **raise** — return `Raise(e)`: `e` is raised in the body at the operation, an
  `except` around it sees it.

`returning(kind, answer, ret, body)` maps the body's value.
`stateful(kind, state, step, body)` answers from a state: `step(state, op)` gives
the answer and the next state, and the value is `(last state, body's value)`.
`resuming(kind, on, ret, body)` gives its clause the rest of the body (§7).

A business rule as a
handler — over the limit, the whole checkout ends and no mail is sent:

```python
def limit(cents, pay, body, stop):
    def answer(op: Charge) -> Eff[Any]:
        if op.cents > cents:
            return Abort(stop(op))
        return (yield from pay.charge(op.user, op.cents))
    return (yield from handle(Charge, answer, lambda c: body(Payments(c))))
```

## 3. The shop

`examples/shop/shop.py`: `Shop.checkout` over `Inventory`, `Payments`, `Notify`;
`stock` over a `Db` effect answered by `sql` (sqlite3; a failed statement raised
where it was performed) or `table` (in memory); `stripe` over `Http`; `card` for
tests; `production` and `test` assemblies. See the README.

## 4. The kernel's protocol

```python
run = start(lambda h: remote(h).checkout("bob", "book", 2000))
s = run.step()                                # s.op == Reserve("book")
s = run.resume(True)                          # s.op == Charge("bob", 2000)
s = run.resume(Receipt(True, "approved by ann"))  # later, from outside
```

`h.outside(kind)` is a capability the host answers. `step`/`resume` return a
`Step` — `yielded` with `op`, `done` with `value`, `failed` with `failure`.
`fail(e)` raises `e` at the operation; `cancel()` closes the program, its
`finally` blocks run.

## 5. Middleware

```python
rs = Routes()
rs.on(Reserve, reserve)
rs.on(Charge, lambda op: Receipt(True, f"ch_{op.user}"))
rs.on(Email, lambda op: sent.append(f"{op.to}: {op.text}"))

def security(op: Charge, nxt: Answerer) -> Any:
    if op.user not in allowed or op.cents > 5000:
        raise PermissionError(f"forbidden: charge {op.user}")
    return nxt(op)

answer = with_(rs, auditing, metrics, record(log), only(Charge, security))
receipt = drive(start(lambda h: remote(h).checkout("bob", "book", 2000)), answer)
```

- a `Middleware` is `(op, next) -> answer`; `with_` wraps, the first outermost;
- `only(kind, m)`: for one kind of operation; the others pass by;
- an exception is raised in the program at the operation — a refused `Charge`
  stops the checkout there, no mail goes out;
- `record(log)` keeps every `Entry(op, answer, error)`; `replay(log)` answers
  a run again from it, reaching no service, refusing the first operation that
  differs;
- the same middleware wraps `wire.remote(call)`: Scala handlers included.

## 6. Async and parallel branches

The business code stays a generator; only the runner is async:

```python
def main(io: Async) -> Eff[int]:
    xs = yield from all_of(io, branch(1), branch(2), branch(40))  # at once
    return sum(xs)

asyncio.run(run_async(main))
```

`wait(io, awaitable)` awaits anything; `sleep(io, s)`; `race` — the first wins,
the others are cancelled, their `finally` blocks run; in `all_of` a failure
cancels the others and is raised as the branch's own exception. A branch
answers its own handlers and `Await`; it does not reach the parent's handlers.

## 7. Programs as data, and every path

The rest of a generator after an operation is the generator itself: it cannot be
copied, so it goes on once. That is enough for every handler above. A handler
that resumes the rest more than once — every path of a choice — needs the rest
as a value, so a program has a second form, data (`Data[A]`):

- `pure(a)` — a value;
- `cap.perform(op)` — an operation;
- `p.bind(f)` — a program and what follows it, `f` of its value (`p.map(f)` for a
  plain function, `p.then(q)` when the value is not needed).

The same program in both forms, under the same handler:

```python
def as_generator(c: Cap[Ask[int]]) -> Eff[int]:
    a = yield from c.perform(Ask[int]())
    b = yield from c.perform(Ask[int]())
    return a * 10 + b

def as_data(c: Cap[Ask[int]]) -> Data[int]:
    return c.perform(Ask[int]()).bind(lambda a: c.perform(Ask[int]()).map(lambda b: a * 10 + b))

run(answering(Ask, lambda _: 4, as_generator))  # 44
run(answering(Ask, lambda _: 4, as_data))       # 44
```

One machine runs both (`okay_effects/machine.py`): one loop over the program and
a stack of frames — what waits for a value. The stack is a linked list never
written after it is made, so the rest of a program after an operation, the frames
from the operation down to its handler, is a value: kept, and laid again each
time it is resumed. A long program runs in constant stack: a hundred thousand
binds, nested to the left or to the right.

`resuming(kind, on, ret, body)` is the handler that is given the rest: `on(op, k)`
returns the handler's value as a program and may call `k(answer)` never, once or
many times; `ret` makes the handler's value of the body's:

```python
def body(c: Cap[Ask[int]]) -> Data[int]:
    return c.perform(Ask[int]()).map(lambda a: a + 1)

def under(on):
    return run(resuming(Ask, on, lambda a: pure(str(a)), body))

under(lambda op, k: pure("left"))                                         # "left"
under(lambda op, k: k(1))                                                 # "2"
under(lambda op, k: k(1).bind(lambda x: k(10).map(lambda y: x + y)))      # "211"
```

`all_paths(kind, answers, body)` is such a handler: the body's value for every
combination of answers, depth first. Nothing is run again to come back to a
choice — each part of the body runs once for each path through it:

```python
def body(c: Cap[Among[bool]]) -> Data[tuple[bool, bool]]:
    flip = c.perform(Among((True, False)))
    return (pure(None).map(lambda _: count("before", None)).then(flip)
            .bind(lambda a: pure(count("between", a)).then(flip)
                  .map(lambda b: count("after", (a, b)))))

run(all_paths(Among, options, body))
# [(True, True), (True, False), (False, True), (False, False)]
# runs == {"before": 1, "between": 2, "after": 4}
```

The shop's `checkout_data` under it gives every outcome of a checkout (README).

**The order of handlers is the meaning.** A `stateful` handler keeps its state
where it stands in the program. Inside the choice it is part of the rest the
choice resumes, as it was there: each path has its own state. Around the choice
it is not in the rest: one state goes through all the paths.

```python
def p(choice: Cap[Among[int]], state: Cap[Cell[Any]]) -> Data[int]:
    return choice.perform(Among((1, 2))).bind(
        lambda x: state.perform(Get()).bind(
            lambda s: state.perform(Put(s + x)).then(pure(s + x))))

run(all_paths(Among, options, lambda c: stateful(Cell, 0, cell, lambda s: p(c, s))))  # [(1, 1), (2, 2)]
run(stateful(Cell, 0, cell, lambda s: all_paths(Among, options, lambda c: p(c, s))))  # (3, [1, 3])
```

**A generator is never resumed twice, and never replayed.** `resuming` and
`all_paths` take a body of `Data` only:

- by the types: a generator (`Eff[A]`) is not `Data[A]`, and a handler over a
  generator body is a `Prog[A]`, not `Data[A]` — `mypy --strict` refuses both;
- at run time, if the types were forced (`Any`, an ignore): a generator as the
  body is refused before it runs, and an operation performed from a generator
  anywhere between it and that handler is refused at the operation — `OneShot`,
  before the clause runs.

The forms mix where no rest crosses a generator: a generator may `yield from` a
data program whose choice is handled inside it.

```python
def main(a: Cap[Ask[int]]) -> Eff[int]:
    base = yield from a.perform(Ask[int]())
    paths = yield from all_paths(Among, options, body)  # the rest taken ends at the handler: data
    return base + sum(paths)
```

The kernel's protocol, the middleware and asyncio take either form: `start`,
`drive` and `run_async` run a data program as they run a generator; a host and
its middleware see the operations of every path, in order. An awaitable is
awaited once (Python's rule): make it inside the bind, once for each path —
`c.perform(Among((1, 2))).bind(lambda x: wait(io, fetch(x)))`.

## 8. Over the wire, with okay

`wire.remote(call)` answers every operation over any transport: an operation
crosses as its name (snake_case of its class, or `op_name`) and one record of
its fields; the answer is decoded into `R` (a dict into a dataclass).
`wire.serve(kind, answer)` is the other way.

With okay's Python worker, `okay.okay_call` is the transport:

```python
def checkout(user, item, cents):
    run = start(lambda h: remote(h).checkout(user, item, cents))
    r = drive(run, wire.remote(lambda name, args: okay.okay_call(name, *args)))
    return f"{r.paid} {r.detail}"

_, charge = wire.serve(Charge, lambda op: Receipt(op.cents <= 5000, f"py-{op.user}-{op.cents}"))
```

and in Scala the operations are case classes, the handlers callbacks under
Scala's own effects:

```scala
final case class Charge(user: String, cents: Long) derives Schema
val charge = Py.callback[Charge, Receipt]("charge")(c => Reader.ask[Store].map(...))
shop.fn[String]("checkout").calling(Py.callbacks(reserve, charge, email))("bob", "book", 2000L)
```

`examples/okayhost/TestOkayPythonWire.scala`, run against okay's JVM host.
(The package is `okay_effects` because okay's worker owns the module name `okay`.)

## 9. Typing and limits

- `mypy --strict` passes on the library, the shop and the tests; the result of
  `yield from cap.perform(op)` is typed by `op`'s `R`.
- `Cap` is contravariant: a capability for any `Op` serves where a
  `Cap[Reserve]` is wanted (the shop under `all_paths`).
- Not checked statically: which operations a capability takes (checked at
  runtime by `perform`), and which effects a program needs — Python has no
  rows; a program's effects are its capabilities, its parameters.
- A generator's continuation is one-shot. A handler that resumes many times takes
  a program of data, whose continuation is a value (§7); no program is replayed.
