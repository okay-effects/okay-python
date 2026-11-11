"""okay-python: algebraic effects for Python, typed, on generators.

A handler makes a capability and hands it to its body; the body performs
operations through it with ``yield from cap.perform(op)``, typed by what the
operation answers. A program is a generator: performing suspends it, the
handler that owns the capability answers, the program resumes. Or it is data —
``pure``, ``cap.perform(op)``, ``bind`` — whose rest after an operation is a value,
resumed any number of times; one machine runs both. A handler
answers only ITS capability's operations — by identity, not by type — so two
instances of one effect coexist; an operation of another passes outward. A
capability used after its handler returned is refused by name.

The design of okay (Scala 3) and okay-go, in Python.
"""

from okay_effects.core import (
    Abort,
    Cap,
    Data,
    Eff,
    Escaped,
    OneShot,
    Op,
    Prog,
    Raise,
    Unhandled,
    answering,
    handle,
    pure,
    resuming,
    returning,
    stateful,
)
from okay_effects.machine import run
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
    "Abort", "Answerer", "Async", "Await", "Cap", "Computation", "Data", "Eff", "Entry", "Escaped", "Host",
    "Middleware", "OneShot", "Op", "Prog", "Raise", "Routes", "Step", "Unhandled", "all_of", "answering",
    "drive", "handle", "only", "pure", "race", "record", "replay", "resuming", "returning",
    "run", "run_async", "sleep", "start", "stateful", "wait", "wire", "with_",
]
