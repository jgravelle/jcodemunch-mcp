import os
from typing import Optional


def add(a: int, b: int) -> int:
    return a + b


def bad_call() -> int:
    return add("x", 2)


class Widget:
    def size(self) -> int:
        return "big"

    def helper(self):
        def inner() -> str:
            return 42
        return inner()


def unused_var() -> None:
    contact = "alice@example.com"
    x: Optional[int] = None
    return x + 1
