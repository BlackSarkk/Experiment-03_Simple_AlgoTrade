"""Output-config name guard.

The optimizer emits one runnable strategy config into configs/config/. That name
is mandatory and is never auto-generated, never reused and never overwritten: an
existing strategy config is a recorded experiment, and silently replacing one
would invalidate every result that cites it.
"""

import os
import re
from dataclasses import dataclass

CONFIG_DIR = os.path.join("configs", "config")

# Plain file name only: letters, digits, dot, dash, underscore.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class OutputNameError(ValueError):
    """Raised when the requested output config name is unusable."""


@dataclass(frozen=True)
class OutputTarget:
    name: str
    path: str


def validate(arg) -> OutputTarget:
    if arg is None:
        raise OutputNameError(
            "no output config name given.\n"
            "       The optimizer never invents one. Pass it explicitly, e.g.\n"
            "         ./pipeline.sh --optimize --odefault.json --mywinner.json"
        )

    if not isinstance(arg, str) or not arg.strip():
        raise OutputNameError("output config name is empty")

    name = arg.strip()

    # Accept the canonical directory prefix, but nothing else path-like.
    prefix = CONFIG_DIR + os.sep
    if name.startswith(prefix):
        name = name[len(prefix):]

    if os.path.isabs(name) or name.startswith("~"):
        raise OutputNameError(
            f"output config must be a plain file name inside {CONFIG_DIR}/, "
            f"not an absolute path: {arg!r}"
        )

    if "/" in name or "\\" in name or name in (".", ".."):
        raise OutputNameError(
            f"output config must be a plain file name inside {CONFIG_DIR}/, "
            f"got {arg!r}"
        )

    if not _SAFE_NAME.match(name):
        raise OutputNameError(
            f"output config name may only contain letters, digits, '.', '-' and '_', "
            f"got {arg!r}"
        )

    if not name.endswith(".json"):
        raise OutputNameError(
            f"output config must end in .json, got {arg!r}"
        )

    if not name[: -len(".json")]:
        raise OutputNameError("output config name is just '.json'")

    path = os.path.join(CONFIG_DIR, name)

    # Final containment check against any residual traversal.
    root = os.path.realpath(CONFIG_DIR)
    target = os.path.realpath(path)
    if os.path.dirname(target) != root:
        raise OutputNameError(
            f"output path escapes {CONFIG_DIR}/: {arg!r}"
        )

    if os.path.exists(path):
        raise OutputNameError(
            f"output config already exists: {path}\n"
            "       Choose a different output name. Existing strategy configs are "
            "never overwritten."
        )

    if not os.path.isdir(CONFIG_DIR):
        raise OutputNameError(f"config directory is missing: {CONFIG_DIR}/")

    return OutputTarget(name=name, path=path)
