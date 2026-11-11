# okay-python

Algebraic effects for Python, **typed**, on generators: the design of
[okay](https://github.com/okay-effects/okay) (Scala 3) and
[okay-go](https://github.com/okay-effects/okay-go), in Python.

- **A handler makes a capability and hands it to its body.** The body performs
  through it: `ok = yield from inventory.reserve(item)` — plain Python, `if`,
  loops, `try`/`finally`, typed by what the operation answers (`mypy --strict`
  clean; a `bool` used as a `Receipt` is a type error).
- **Programs are generators**: performing suspends the program, the handler that
  owns the capability answers, the program resumes.
- **Or data.** The same program as a value — `pure`, `cap.perform(op)`, `bind` —
  whose rest after an operation is a value too, so a handler may resume it any
  number of times. One machine runs both forms, in constant stack.
- **By identity, not by type.** A handler answers only its own capability's
  operations; two instances of one effect coexist; an operation of another passes
  outward. A capability that escaped its handler is refused by name.
- **Components on components.** A handler's answer may perform through other
  capabilities: the inventory as a statement on a `Db` effect, payments over HTTP.
- **The kernel's protocol.** A program handed out and stepped by a host that sees
  only operations and answers: `step`, `resume`, `fail` (raised at the
  operation), `cancel` (its `finally` blocks run).
- **Middleware.** Every operation answered in one place, through audit, metrics,
  security, `record` and `replay` — all alike, or one kind its own way.
- **Async without coloured functions.** The business code stays a generator;
  `run_async` at the top awaits; `all_of` and `race` on asyncio.
- **Every path.** A handler resuming the rest with each possible answer: every
  outcome of a checkout in one run. Nothing is run again to get there: no replay.
- **Over the wire.** An operation crosses as a name and a record — a Scala case
  class's shape; with okay's Python worker, the shop runs in Python while Scala
  handlers answer it.

No dependencies; Python 3.11+.

**Documentation:** [the guide](docs/guide.md).

## The shop

```python
@dataclass(frozen=True)
class Shop:
    inventory: Inventory
    payments: Payments
    mail: Notify

    def checkout(self, user: str, item: str, cents: int) -> Eff[Receipt]:
        r = Receipt(False, f"{item}: out of stock")
        if (yield from self.inventory.reserve(item)):
            r = yield from self.payments.charge(user, cents)
        if r.paid:
            yield from self.mail.email(user, f"paid, receipt {r.detail}")
        else:
            yield from self.mail.email(user, f"not paid: {r.detail}")
        return r
```

A handler, the inventory over a database effect:

```python
RESERVE_SQL = "update stock set qty = qty - 1 where sku = ? and qty > 0"

def stock(db: Db, body: Callable[[Inventory], Eff[A]]) -> Eff[A]:
    def answer(op: Reserve) -> Eff[bool]:
        return (yield from db.exec(RESERVE_SQL, op.item)) == 1

    return (yield from handle(Reserve, answer, lambda c: body(Inventory(c))))
```

The system, assembled once, then used with any data:

```python
production = lambda use: run(
    mailbox(sent, lambda mail:
    gateway(calls, lambda http:
    stripe(http, lambda pay:
    sql(conn, lambda db:
    stock(db, lambda inv: use(Shop(inv, pay, mail))))))))

receipt = production(lambda shop: shop.checkout("bob", "book", 2000))
```

`sql` runs on `sqlite3` (any DB-API driver); a test assembly swaps `table` and
`card` in. `examples/shop`.

## A program as data

A generator cannot be copied, so its rest after an operation goes on once. A
handler that resumes the rest many times needs it as a value: write that program
as data — what follows each operation is a function of its answer:

```python
def checkout_data(self, user: str, item: str, cents: int) -> Data[Receipt]:
    def charge(reserved: bool) -> Data[Receipt]:
        if not reserved:
            return pure(Receipt(False, f"{item}: out of stock"))
        return self.payments.charge(user, cents)

    def tell(r: Receipt) -> Data[Receipt]:
        text = f"paid, receipt {r.detail}" if r.paid else f"not paid: {r.detail}"
        return self.mail.email(user, text).then(pure(r))

    return self.inventory.reserve(item).bind(charge).bind(tell)
```

The same capabilities and handlers serve it. `all_paths` is a handler that resumes
the rest with each answer in turn — every outcome of the checkout, the part before
each choice run once:

```python
def answers(op: Op[Any]) -> list[Any]:
    if isinstance(op, Charge):
        return [Receipt(True, "ch"), Receipt(False, "declined")]
    return [True, False] if isinstance(op, Reserve) else [None]

def body(cap: Cap[Op[Any]]) -> Data[Receipt]:
    return Shop(Inventory(cap), Payments(cap), Notify(cap)).checkout_data("bob", "book", 2000)

run(all_paths(Op, answers, body))
# [Receipt(True, 'ch'), Receipt(False, 'declined'), Receipt(False, 'book: out of stock')]
```

A generator given to such a handler is a type error (`mypy --strict`), and an
error at run time (`OneShot`) if forced. [The guide, §7](docs/guide.md#7-programs-as-data-and-every-path).

## Tests

```
python3 -m unittest discover -s tests -t .
mypy --strict okay_effects examples tests
```

`examples/okayhost/TestOkayPythonWire.scala`: the same shop under Scala handlers
in okay, run against okay's JVM host.

## License

Apache 2.0, as okay.
