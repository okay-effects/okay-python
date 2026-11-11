"""okay-python: algebraic effects for Python, typed, on generators.

A handler makes a capability and hands it to its body; the body performs
operations through it with ``yield from cap.perform(op)``, typed by what the
operation answers. A program is a generator: performing suspends it, the
handler that owns the capability answers, the program resumes. A handler
answers only ITS capability's operations — by identity, not by type — so two
instances of one effect coexist; an operation of another passes outward. A
capability used after its handler returned is refused by name.

The design of okay (Scala 3) and okay-go, in Python.
"""

from okay_effects.core import (
    Abort,
    Cap,
    Eff,
    Escaped,
    Op,
    Raise,
    Unhandled,
    answering,
    handle,
    pure,
    returning,
    run,
)
from okay_effects.kernel import Computation, Host, Step, start
from okay_effects.middleware import (
    Answerer,
    Entry,
    Middleware,
    Routes,
    drive,
    only,
    record,
    replay,
    with_,
)
from okay_effects import wire
from okay_effects.par import Async, Await, all_of, race, run_async, sleep, wait

__all__ = [
    "Abort", "Answerer", "Async", "Await", "Cap", "Computation", "Eff", "Entry", "Escaped", "Host",
    "Middleware", "Op", "Raise", "Routes", "Step", "Unhandled", "all_of", "answering",
    "drive", "handle", "only", "pure", "race", "record", "replay", "returning",
    "run", "run_async", "sleep", "start", "wait", "wire", "with_",
]
