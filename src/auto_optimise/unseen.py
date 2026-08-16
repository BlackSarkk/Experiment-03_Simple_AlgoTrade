"""Structural UNSEEN barrier.

The UNSEEN partition must be unreachable during search, robustness, risk tuning,
Bollinger tuning and ranking. This is enforced by construction, not by comment:
the frame is captured in a closure and there is no attribute on the object that
holds it, so no amount of attribute access, `vars()`, `__dict__` inspection or
pickling reaches the data before `unlock()` is called.

Only a final-selection stage may unlock, and only after the finalists are frozen.
The unlock is one-way, recorded, and requires a stated reason.
"""

from typing import Callable, Optional


class UnseenLockedError(RuntimeError):
    """Raised when UNSEEN data is requested before the barrier is released."""


class UnseenVault:
    """Holds the UNSEEN partition behind a one-way lock.

    Metadata safe to expose while locked (row count, date bounds) is copied out at
    construction time; the frame itself is not reachable until `unlock()`.
    """

    __slots__ = ("_take", "_unlocked", "_reason", "n_candles", "start", "end")

    def __init__(self, frame, start, end):
        # The frame lives only in this closure's cell, never as an attribute.
        def _take():
            return frame

        object.__setattr__(self, "_take", _take)
        object.__setattr__(self, "_unlocked", False)
        object.__setattr__(self, "_reason", None)
        object.__setattr__(self, "n_candles", int(len(frame)))
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    # -- state ---------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        return not object.__getattribute__(self, "_unlocked")

    @property
    def unlock_reason(self) -> Optional[str]:
        return object.__getattribute__(self, "_reason")

    # -- the barrier ---------------------------------------------------------

    def get(self):
        """Return the UNSEEN frame. Raises unless the vault has been unlocked."""
        if self.is_locked:
            raise UnseenLockedError(
                "UNSEEN partition is locked and cannot be evaluated.\n"
                "It may only be read by the final-selection stage, after the "
                "Top-10 finalists are frozen, via unlock('<reason>'). "
                "Ranking, search, robustness, risk and filter stages must use "
                "TRAIN and VALIDATION only."
            )
        return object.__getattribute__(self, "_take")()

    def unlock(self, reason: str):
        """One-way release. Intended for the final-selection stage only."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("unlock() requires a non-empty reason")
        if not self.is_locked:
            raise UnseenLockedError(
                f"UNSEEN is already unlocked (reason: {self.unlock_reason!r}); "
                "it cannot be unlocked twice"
            )
        object.__setattr__(self, "_unlocked", True)
        object.__setattr__(self, "_reason", reason.strip())
        return self.get()

    # -- keep the data out of every incidental exposure path -----------------

    def __setattr__(self, name, value):
        raise AttributeError("UnseenVault is immutable")

    def __repr__(self) -> str:
        state = "LOCKED" if self.is_locked else f"UNLOCKED({self.unlock_reason!r})"
        return (f"<UnseenVault {state} candles={self.n_candles} "
                f"{self.start} -> {self.end}>")

    def __reduce__(self):
        raise UnseenLockedError("UnseenVault cannot be pickled or copied")

    def __iter__(self):
        raise UnseenLockedError("UNSEEN is locked; iterate TRAIN or VALIDATION instead")

    def __len__(self):
        # Row count is metadata, not data, and is needed for the run plan.
        return int(object.__getattribute__(self, "n_candles"))
