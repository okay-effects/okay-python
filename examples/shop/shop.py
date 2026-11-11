"""An application as capabilities — the same shop as in okay's Scala and Java examples and okay-go."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from okay_effects import Abort, Cap, Data, Eff, Host, Op, Raise, answering, handle, pure, run

A = TypeVar("A")

# ---- the services: operations, and their typed faces over a capability. A face gives the
# operation as it is: a generator binds it with ``yield from``, a program of data with ``bind``


@dataclass(frozen=True)
class Receipt:
    paid: bool
    detail: str


@dataclass(frozen=True)
class Reserve(Op[bool]):
    item: str


@dataclass(frozen=True)
class Charge(Op[Receipt]):
    user: str
    cents: int


@dataclass(frozen=True)
class Email(Op[None]):
    to: str
    text: str


@dataclass(frozen=True)
class Inventory:
    cap: Cap[Reserve]

    def reserve(self, item: str) -> Data[bool]:
        return self.cap.perform(Reserve(item))


@dataclass(frozen=True)
class Payments:
    cap: Cap[Charge]

    def charge(self, user: str, cents: int) -> Data[Receipt]:
        return self.cap.perform(Charge(user, cents))


@dataclass(frozen=True)
class Notify:
    cap: Cap[Email]

    def email(self, to: str, text: str) -> Data[None]:
        return self.cap.perform(Email(to, text))


# ---- the business code: a plain class, its dependencies capabilities, plain Python


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

    def checkout_data(self, user: str, item: str, cents: int) -> Data[Receipt]:
        """The same checkout as data: what follows each operation is a function of its answer,
        so a handler may go on from any of them more than once — every outcome in one run."""

        def charge(reserved: bool) -> Data[Receipt]:
            if not reserved:
                return pure(Receipt(False, f"{item}: out of stock"))
            return self.payments.charge(user, cents)

        def tell(r: Receipt) -> Data[Receipt]:
            text = f"paid, receipt {r.detail}" if r.paid else f"not paid: {r.detail}"
            return self.mail.email(user, text).then(pure(r))

        return self.inventory.reserve(item).bind(charge).bind(tell)


# ---- a database, as an effect: a statement, its rows affected


@dataclass(frozen=True)
class Exec(Op[int]):
    sql: str
    args: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Db:
    cap: Cap[Exec]

    def exec(self, sql: str, *args: Any) -> Data[int]:
        return self.cap.perform(Exec(sql, args))


def sql(conn: sqlite3.Connection, body: Callable[[Db], Eff[A]]) -> Eff[A]:
    """production: sqlite3 (any DB-API driver); a failed statement is raised where it was performed"""

    def answer(op: Exec) -> Eff[Any]:
        try:
            return conn.execute(op.sql, op.args).rowcount
        except sqlite3.Error as e:
            return Raise(e)
        yield  # noqa: unreachable

    return (yield from handle(Exec, answer, lambda c: body(Db(c))))


def table(stock: dict[str, int], body: Callable[[Db], Eff[A]]) -> Eff[A]:
    """for tests: the stock table in memory, answering the statements the shop makes"""

    def answer(op: Exec) -> Eff[Any]:
        if op.sql != RESERVE_SQL:
            return Raise(LookupError(f"table: no such statement: {op.sql}"))
        if stock.get(op.args[0], 0) == 0:
            return 0
        stock[op.args[0]] -= 1
        return 1
        yield  # noqa: unreachable

    return (yield from handle(Exec, answer, lambda c: body(Db(c))))


# ---- handlers: each makes a capability and hands it to its body

RESERVE_SQL = "update stock set qty = qty - 1 where sku = ? and qty > 0"


def stock(db: Db, body: Callable[[Inventory], Eff[A]]) -> Eff[A]:
    """the inventory over a database: Reserve becomes one statement, answered by whatever Db is"""

    def answer(op: Reserve) -> Eff[bool]:
        return (yield from db.exec(RESERVE_SQL, op.item)) == 1  # a row updated: it was there, now it is ours

    return (yield from handle(Reserve, answer, lambda c: body(Inventory(c))))


def mailbox(sent: list[str], body: Callable[[Notify], Eff[A]]) -> Eff[A]:
    return (yield from answering(Email, lambda op: sent.append(f"{op.to}: {op.text}"), lambda c: body(Notify(c))))


def card(approve: bool, body: Callable[[Payments], Eff[A]]) -> Eff[A]:
    """for tests: payments without any HTTP"""
    return (yield from answering(
        Charge, lambda op: Receipt(True, f"test-{op.user}") if approve else Receipt(False, "declined"),
        lambda c: body(Payments(c))))


def limit(cents: int, pay: Payments, body: Callable[[Payments], Eff[A]], stop: Callable[[Charge], A]) -> Eff[A]:
    """a business rule as a handler: over the limit, the whole body ends (no mail, its finally blocks run)"""

    def answer(op: Charge) -> Eff[Any]:
        if op.cents > cents:
            return Abort(stop(op))
        return (yield from pay.charge(op.user, op.cents))

    return (yield from handle(Charge, answer, lambda c: body(Payments(c))))


# a component built on another: payments over HTTP


@dataclass(frozen=True)
class Response:
    status: int
    body: str


@dataclass(frozen=True)
class Post(Op[Response]):
    url: str
    body: str


@dataclass(frozen=True)
class Http:
    cap: Cap[Post]

    def post(self, url: str, body: str) -> Data[Response]:
        return self.cap.perform(Post(url, body))


def gateway(calls: list[str], body: Callable[[Http], Eff[A]]) -> Eff[A]:
    def answer(op: Post) -> Response:
        calls.append(f"POST {op.url} {op.body}")
        return Response(200, "ch_42")

    return (yield from answering(Post, answer, lambda c: body(Http(c))))


def stripe(http: Http, body: Callable[[Payments], Eff[A]]) -> Eff[A]:
    def answer(op: Charge) -> Eff[Receipt]:
        res = yield from http.post("https://api.stripe.com/v1/charges", f"customer={op.user}&amount={op.cents}")
        if res.status == 200:
            return Receipt(True, res.body)
        return Receipt(False, f"card declined ({res.status}): {res.body}")

    return (yield from handle(Charge, answer, lambda c: body(Payments(c))))


# ---- the system: every capability made, a Shop handed to what uses it

App = Callable[[Callable[[Shop], Eff[Receipt]]], Receipt]


def production(conn: sqlite3.Connection, calls: list[str], sent: list[str]) -> App:
    return lambda use: run(
        mailbox(sent, lambda mail:
        gateway(calls, lambda http:
        stripe(http, lambda pay:
        sql(conn, lambda db:
        stock(db, lambda inv: use(Shop(inv, pay, mail))))))))


def test(items: dict[str, int], approve: bool, sent: list[str]) -> App:
    return lambda use: run(
        mailbox(sent, lambda mail:
        card(approve, lambda pay:
        table(items, lambda db:
        stock(db, lambda inv: use(Shop(inv, pay, mail)))))))


# ---- the shop handed out: its capabilities answered by whoever steps it (okay.start)


def remote(h: Host) -> Shop:
    return Shop(Inventory(h.outside(Reserve)), Payments(h.outside(Charge)), Notify(h.outside(Email)))
