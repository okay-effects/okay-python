import sqlite3
import unittest

from examples.shop.shop import (Receipt, Shop, card, limit, mailbox, production, stock, table,
                                test as test_app)
from okay_effects import run


def database(qty: int) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("create table stock (sku text primary key, qty int)")
    c.execute("insert into stock values ('book', ?)", (qty,))
    return c


class TestShop(unittest.TestCase):
    def test_test_assembly(self) -> None:
        sent: list[str] = []
        items = {"book": 1}
        got = test_app(items, False, sent)(lambda s: s.checkout("ann", "book", 2000))
        self.assertEqual(got, Receipt(False, "declined"))
        self.assertEqual((sent, items), (["ann: not paid: declined"], {"book": 0}))

    def test_production_assembly_on_sqlite(self) -> None:
        calls: list[str] = []
        sent: list[str] = []
        db = database(1)
        app = production(db, calls, sent)
        self.assertEqual(app(lambda s: s.checkout("bob", "book", 2000)), Receipt(True, "ch_42"))
        self.assertEqual(calls, ["POST https://api.stripe.com/v1/charges customer=bob&amount=2000"])
        self.assertEqual(sent, ["bob: paid, receipt ch_42"])
        self.assertEqual(db.execute("select qty from stock").fetchone(), (0,))
        # out of stock now: never charged
        self.assertFalse(app(lambda s: s.checkout("eve", "book", 2000)).paid)
        self.assertEqual(len(calls), 1)

    def test_a_database_failure_is_raised_at_the_operation(self) -> None:
        sent: list[str] = []
        broken = sqlite3.connect(":memory:")  # no stock table
        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            production(broken, [], sent)(lambda s: s.checkout("bob", "book", 2000))
        self.assertEqual(sent, [])  # stopped where Stock performed: no mail

    def test_a_limit_ends_the_whole_checkout(self) -> None:
        sent: list[str] = []
        got = run(mailbox(sent, lambda mail:
                  card(True, lambda c:
                  limit(1000, c, lambda pay:
                        table({"book": 1}, lambda db:
                        stock(db, lambda inv: Shop(inv, pay, mail).checkout("bob", "book", 2000))),
                        lambda op: Receipt(False, "needs approval")))))
        self.assertEqual((got, sent), (Receipt(False, "needs approval"), []))


if __name__ == "__main__":
    unittest.main()
