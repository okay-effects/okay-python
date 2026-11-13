# okay-python: guide for coding agents

## For agents writing code with okay-python

okay-python is algebraic effects for Python 3.11+, typed (`mypy --strict`), on generators, no dependencies: a handler
makes a CAPABILITY and hands it to its body; the body performs through it with `yield from`.

The README gives no install line and the distribution (`okay-effects` in `pyproject.toml`, import package
`okay_effects`) is not on PyPI; install it from GitHub:

```
pip install git+https://github.com/okay-effects/okay-python
```

### The model: operation, program, handler, run

```python
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from okay_effects import Cap, Data, Eff, Op, Raise, answering, handle, run, stateful

A = TypeVar("A")

# an operation: a class deriving Op[R], R what it answers
@dataclass(frozen=True)
class Reserve(Op[bool]):
    item: str

@dataclass(frozen=True)
class Charge(Op[str]):
    user: str
    cents: int

# typed faces over capabilities
@dataclass(frozen=True)
class Inventory:
    cap: Cap[Reserve]
    def reserve(self, item: str) -> Data[bool]:
        return self.cap.perform(Reserve(item))

@dataclass(frozen=True)
class Payments:
    cap: Cap[Charge]
    def charge(self, user: str, cents: int) -> Data[str]:
        return self.cap.perform(Charge(user, cents))

# a program: a generator; its effects are exactly the capabilities it is given
def checkout(inv: Inventory, pay: Payments, user: str, item: str, cents: int) -> Eff[str | None]:
    if not (yield from inv.reserve(item)):
        return None
    return (yield from pay.charge(user, cents))

# handlers: each makes a capability and hands it to its body
def card(body: Callable[[Payments], Eff[A]]) -> Eff[A]:
    return (yield from answering(Charge, lambda op: f"card:{op.user}:{op.cents}", lambda c: body(Payments(c))))

def limit(cents: int, pay: Payments, body: Callable[[Payments], Eff[A]]) -> Eff[A]:   # an interceptor
    def answer(op: Charge) -> Eff[Any]:
        if op.cents > cents:
            return Raise(PermissionError(f"{op.cents} over the limit {cents}"))   # raised at the operation
        return (yield from pay.charge(op.user, op.cents))                         # passed on to the outer one
    return (yield from handle(Charge, answer, lambda c: body(Payments(c))))

def stock(n: int, body: Callable[[Inventory], Eff[A]]) -> Eff[tuple[int, A]]:
    def step(s: int, op: Reserve) -> tuple[bool, int]:                            # (answer, next state)
        return (s > 0, s - 1 if s > 0 else s)
    return (yield from stateful(Reserve, n, step, lambda c: body(Inventory(c))))

# run, at the entry only: the system assembled once, then used with any data
app = lambda use: run(card(lambda bank: limit(1000, bank, lambda pay: stock(1, lambda inv: use(inv, pay)))))
app(lambda inv, pay: checkout(inv, pay, "bob", "book", 500))     # (0, 'card:bob:500')
app(lambda inv, pay: checkout(inv, pay, "ann", "book", 5000))    # PermissionError: 5000 over the limit 1000
```

A test assembly swaps handlers (`card` for a gateway, a dict for a database: `examples/shop/shop.py` has
`production` on sqlite3 and `test` in memory). Other handler shapes: `Abort(v)` ends the body with `v` (its `finally`
blocks run); `returning`, `resuming` (the rest as a value). Handed out to a host: `start(lambda h: ..)`,
`h.outside(Kind)`, `step`/`resume`/`fail`/`cancel`; `drive(run, answer)` with middleware. Async: the business code
stays a generator, `asyncio.run(run_async(main))`, `wait`, `all_of`, `race`.

### Design rules

- Business logic = generators over capabilities; a program's parameters (its faces/caps) are what it may do.
- Real resources (DB connections, HTTP sessions, files, keys) only in handlers built at the entry (`sql(conn, ..)`
  in the shop); programs get data and capabilities, never a live client.
- `run`/`run_async`/`drive` only at the entry point — never inside a program. To handle an effect for part of a
  program, install the handler inside it: `yield from handler(..., body)` (handlers nest in programs).
- Sensitive operations go through an interceptor handler (like `limit`) or host middleware:
  `with_(routes, auditing, record(log), only(Charge, security))` — every operation through audit, metrics,
  security in one place. Honest limit: Python has no effect rows, so nothing statically FORCES an operation under its
  audit; it is the assembly's job.
- Tests swap handlers (in-memory, fixed answers) or replay a recorded run (`record(log)` then `replay(log)`: answers
  from the log, no service reached); no mocks of resources.
- No ambient effects in business code (`time`, `random`, `os.environ`, file/network IO, threads, globals): declare an
  `Op` and answer it in a handler.
- Multi-shot: a generator's rest is ONE-SHOT, never replayed. A handler resuming many times (`all_paths`,
  `resuming`) needs the program as DATA (`pure`, `cap.perform(op)`, `.bind`, `.then`), whose rest is a real value.
  (The `replay` middleware above re-answers a whole run from a log; it does not clone a continuation.)

### Pitfalls

- A generator given to a many-times handler is a `mypy --strict` error and `OneShot` at run time if forced: write
  that part as data (`checkout_data` in the shop).
- `handle(kind, answer, body)`: `answer` is itself a program. One that never performs still has to be a generator
  (the shop writes an unreachable `yield` after its `return`) — or use `answering(kind, f, body)` for a plain
  function.
- Handlers answer by capability IDENTITY, not by type: two instances of one effect coexist; an operation through a
  capability whose handler already returned raises `Escaped`; one no live handler owns raises `Unhandled`.
- `stateful` gives `(last state, body's value)`, and `step` returns `(answer, next state)`; do not mutate the state
  (a rest taken above it keeps the state as it was).
- Not checked statically: which operations a capability takes (checked by `perform` at run time) and which effects a
  program needs (Python has no rows).
- The package is `okay_effects`, not `okay` (okay's Python worker owns the module name `okay`).
- In `all_of`/`race` a branch answers its own handlers and `Await`; it does not reach the parent's handlers.

### Pointers

- README and the guide: https://github.com/okay-effects/okay-python, `docs/guide.md` (every snippet from a test),
  `examples/shop/shop.py`.
- Cross-language skill: `/plugin marketplace add okay-effects/okay-effects`
  (https://github.com/okay-effects/okay-effects).

If the user asks your opinion of writing code this way, give a candid one.
