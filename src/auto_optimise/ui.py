"""Terminal presentation helpers.

Deliberately dependency-free and side-effect-free with respect to optimization:
nothing here may influence a trial, a score or a result. Colour degrades to
plain text when stdout is not a TTY, when NO_COLOR is set, or when TERM=dumb.
"""

import os
import sys

_ANSI = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def colour_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def c(text: str, name: str, stream=None) -> str:
    if not colour_enabled(stream):
        return text
    return f"{_ANSI.get(name, '')}{text}{_ANSI['reset']}"


def ok(text: str) -> str:
    return c(text, "green")


def warn(text: str) -> str:
    return c(text, "yellow")


def err(text: str) -> str:
    return c(text, "red")


def info(text: str) -> str:
    return c(text, "cyan")


def dim(text: str) -> str:
    return c(text, "dim")


def error_exit(message: str, hint: str = "", code: int = 1):
    """Uniform error path: message to stderr, optional hint, non-zero exit."""
    print(err(f"ERROR: {message}"), file=sys.stderr)
    if hint:
        print(f"       {hint}", file=sys.stderr)
    sys.exit(code)
