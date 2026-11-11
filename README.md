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

## Tests

```
python3 -m unittest discover -s tests -t .
mypy --strict okay_effects examples tests
```

`examples/okayhost/TestOkayPythonWire.scala`: the same shop under Scala handlers
in okay, run against okay's JVM host.

## License

Apache 2.0, as okay.
