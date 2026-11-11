# okay-python guide

Every snippet is from a test (`python3 -m unittest discover -s tests -t .`).

1. [Operations, capabilities, handlers](#1-operations-capabilities-handlers)
2. [Handler shapes: answer, translate, abort, raise, return](#2-handler-shapes)
3. [The shop](#3-the-shop)
4. [The kernel's protocol](#4-the-kernels-protocol)
5. [Middleware: monitor, log, authorize, record, replay](#5-middleware)
6. [Async and parallel branches](#6-async-and-parallel-branches)
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

## 2. Handler shapes

`handle(kind, answer, body)`: `answer` is itself a program (`Eff`), so it may

- **answer** — return a value (`answering(kind, f, body)` for a plain function);
- **translate** — perform through outer capabilities (payments over HTTP);
- **abort** — return `Abort(v)`: the body is closed where it performed, its
  `finally` blocks run, the handler's value is `v`;
- **raise** — return `Raise(e)`: `e` is raised in the body at the operation, an
  `except` around it sees it.

`returning(kind, answer, ret, body)` maps the body's value. A business rule as a
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
  `Cap[Reserve]` is wanted.
- Not checked statically: which operations a capability takes (checked at
  runtime by `perform`), and which effects a program needs — Python has no
  rows; a program's effects are its capabilities, its parameters.
- Continuations are one-shot.
