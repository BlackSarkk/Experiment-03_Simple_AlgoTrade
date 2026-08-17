"""Fixed in-place dashboard for the V3 campaign.

Presentation only. Every entry point is wrapped so that a rendering failure degrades
to plain text or silence and can never propagate into a trial: a broken terminal must
not cost a campaign, and must never change a score.

Two output modes:

  TTY      one Rich `Live` region, redrawn in place. No line per trial, no scrolling
           progress bars.
  non-TTY  concise stage-start / stage-complete lines plus a compact progress line at
           a bounded interval. Never one line per trial.

The console is bound to the REAL stdout captured at construction, so it keeps drawing
while engine output is redirected to devnull around it (see `v3_stages._quiet`).

ETA is a pure function of (completed, total, elapsed) and takes its clock from an
injected `time_fn`, so it is deterministic under test.
"""

import sys
import time
from typing import Any, Callable, Dict, Optional

from . import ui

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    _RICH = True
except Exception:                                    # pragma: no cover
    _RICH = False

# Enough completed trials before an ETA is honest. Below this the sample is too
# small and a projection would be noise presented as fact.
MIN_TRIALS_FOR_ETA = 5
CALCULATING = "calculating…"

# Non-TTY: at most one progress line per this many seconds, and never more than one
# per this share of the total budget. Both bounds apply, so a fast campaign cannot
# spam and a slow one still reports.
PLAIN_MIN_SECONDS = 15.0
PLAIN_MIN_FRACTION = 0.05

REFRESH_PER_SECOND = 4


def fmt_secs(seconds: Optional[float]) -> str:
    if seconds is None:
        return CALCULATING
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def eta_seconds(completed: int, total: int, elapsed: float) -> Optional[float]:
    """Remaining seconds, or None while the sample is too small to be honest.

    Pure: no clock, no state. `elapsed` is supplied by the caller.
    """
    if completed < MIN_TRIALS_FOR_ETA or completed <= 0 or elapsed <= 0:
        return None
    if completed >= total:
        return 0.0
    return (elapsed / completed) * (total - completed)


def trial_rate(completed: int, elapsed: float) -> float:
    return (completed / elapsed) if elapsed > 0 else 0.0


class V3Dashboard:
    """Live campaign view. `stage_budgets` is the resolved {stage_key: trials} map."""

    def __init__(self, stage_budgets: Dict[str, int], stage_labels: Dict[str, str],
                 symbol: str, timeframe: str, direction: str,
                 enabled: bool = True, time_fn: Callable[[], float] = time.monotonic,
                 stream=None):
        self.budgets = dict(stage_budgets)
        self.labels = dict(stage_labels)
        self.symbol, self.timeframe, self.direction = symbol, timeframe, direction
        self.time_fn = time_fn
        self.total = sum(self.budgets.values())

        self.completed = 0
        self.stage_key: Optional[str] = None
        self.stage_completed = 0
        self.stage_started: Optional[float] = None
        self.started = self.time_fn()
        self.best: Optional[Dict[str, Any]] = None
        self.stage_status: Dict[str, str] = {k: "waiting" for k in self.budgets}
        self.notes: list = []

        # Bind to the real stdout NOW, before any redirection wraps it.
        self._stream = stream if stream is not None else sys.stdout
        self._tty = bool(getattr(self._stream, "isatty", lambda: False)())
        self.live_enabled = bool(enabled and _RICH and self._tty
                                 and ui.colour_enabled(self._stream))
        self._live = None
        self._console = None
        self._last_plain_t = 0.0
        self._last_plain_n = 0
        self.render_failures = 0

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self):
        if self.live_enabled:
            try:
                self._console = Console(file=self._stream, force_terminal=True)
                self._live = Live(self._render(), console=self._console,
                                  refresh_per_second=REFRESH_PER_SECOND,
                                  transient=False)
                self._live.__enter__()
            except Exception:
                self.live_enabled = False
                self._live = None
        return self

    def __exit__(self, exc_type, exc, tb):
        # Always tear the region down, even on error, so a traceback is not printed
        # into a live-updating area where it would be overwritten.
        if self._live is not None:
            try:
                self._live.__exit__(exc_type, exc, tb)
            except Exception:
                pass
            self._live = None
        return False                                  # never swallow an exception

    # -- state (each call is total-failure-tolerant) -------------------------

    def _safe(self, fn):
        try:
            fn()
        except Exception:
            self.render_failures += 1                 # presentation only; never raise

    def start_stage(self, key: str):
        self.stage_key = key
        self.stage_completed = 0
        self.stage_started = self.time_fn()
        self.stage_status[key] = "running"
        self._say(f"  {self.labels.get(key, key)}  start  "
                  f"({self.budgets.get(key, 0)} trials)")
        self._safe(self._refresh)

    def finish_stage(self, key: str, status: str = "done", detail: str = ""):
        self.stage_status[key] = status
        elapsed = self.stage_elapsed()
        self._say(f"  {self.labels.get(key, key)}  {status}  "
                  f"({self.stage_completed}/{self.budgets.get(key, 0)} trials, "
                  f"{fmt_secs(elapsed)}){(' — ' + detail) if detail else ''}")
        self._safe(self._refresh)

    def skip_stage(self, key: str, reason: str = ""):
        self.stage_status[key] = "skipped"
        self._say(f"  {self.labels.get(key, key)}  skipped"
                  f"{(' — ' + reason) if reason else ''}")
        self._safe(self._refresh)

    def trial(self, score: float, metrics: Optional[Dict[str, Any]] = None):
        """One completed trial. Never raises, never alters the caller's values."""
        self.completed += 1
        self.stage_completed += 1
        try:
            if score is not None and (self.best is None
                                      or float(score) > float(self.best["score"])):
                self.best = {"score": float(score), "stage": self.stage_key,
                             "metrics": dict(metrics) if metrics else None}
        except Exception:
            self.render_failures += 1
        self._safe(self._refresh)
        self._safe(self._maybe_plain)

    def note(self, text: str):
        self.notes.append(text)
        self._say("  " + text)
        self._safe(self._refresh)

    # -- derived numbers -----------------------------------------------------

    def elapsed(self) -> float:
        return self.time_fn() - self.started

    def stage_elapsed(self) -> float:
        # `is not None`, not truthiness: a stage starting at t=0.0 is not "unset".
        if self.stage_started is None:
            return 0.0
        return self.time_fn() - self.stage_started

    def stage_eta(self) -> Optional[float]:
        if self.stage_key is None:
            return None
        return eta_seconds(self.stage_completed, self.budgets.get(self.stage_key, 0),
                           self.stage_elapsed())

    def overall_eta(self) -> Optional[float]:
        return eta_seconds(self.completed, self.total, self.elapsed())

    def rate(self) -> float:
        return trial_rate(self.completed, self.elapsed())

    def snapshot(self) -> Dict[str, Any]:
        """Everything the view shows, as plain data. Used by the tests."""
        return {
            "completed": self.completed, "total": self.total,
            "stage": self.stage_key,
            "stage_completed": self.stage_completed,
            "stage_total": self.budgets.get(self.stage_key, 0),
            "rate": self.rate(),
            "stage_eta": self.stage_eta(), "overall_eta": self.overall_eta(),
            "elapsed": self.elapsed(), "best": self.best,
        }

    # -- rendering -----------------------------------------------------------

    def _best_line(self) -> str:
        if not self.best:
            return "—"
        parts = [f"score {self.best['score']:.4f}"]
        m = self.best.get("metrics")
        if m:
            parts += [f"ret {m.get('return_pct', 0):+.2f}%",
                      f"PF {m.get('pf', 0):.3f}",
                      f"DD {m.get('max_dd', 0):.2f}%",
                      f"{int(m.get('trades', 0))} trades"]
        return "  ·  ".join(parts)

    def _render(self):
        head = Table.grid(padding=(0, 1))
        head.add_column(style="cyan", no_wrap=True)
        head.add_column()
        stage_label = self.labels.get(self.stage_key, "—")
        stage_total = self.budgets.get(self.stage_key, 0)
        head.add_row("Overall:", f"{self.completed} / {self.total}")
        head.add_row("Current stage:", f"{stage_label} | {self.stage_completed} / {stage_total}")
        head.add_row("Trial rate:", f"{self.rate():.2f} trials/sec")
        head.add_row("Stage ETA:", fmt_secs(self.stage_eta()))
        head.add_row("Overall ETA:", fmt_secs(self.overall_eta()))
        head.add_row("Elapsed:", fmt_secs(self.elapsed()))
        head.add_row("Current best:", self._best_line())

        budget = Table.grid(padding=(0, 2))
        budget.add_column(style="dim", no_wrap=True)
        budget.add_column(justify="right", style="dim")
        budget.add_column(style="dim")
        for key, trials in self.budgets.items():
            budget.add_row(self.labels.get(key, key), str(trials),
                           self.stage_status.get(key, "waiting"))

        title = Text(f"[3/4] V3 optimization   {self.symbol} {self.timeframe} "
                     f"{self.direction}", style="bold cyan")
        return Group(title, head, Text(""), budget)

    def _refresh(self):
        if self._live is not None:
            self._live.update(self._render())

    # -- non-TTY fallback ----------------------------------------------------

    def _say(self, text: str):
        """Stage-level line. Always emitted; there is one per stage, not per trial."""
        if self.live_enabled:
            return
        print(text, file=self._stream, flush=True)

    def _maybe_plain(self):
        """Bounded compact progress for a non-interactive terminal."""
        if self.live_enabled:
            return
        now = self.time_fn()
        min_step = max(1, int(self.total * PLAIN_MIN_FRACTION))
        if (now - self._last_plain_t) < PLAIN_MIN_SECONDS:
            return
        if (self.completed - self._last_plain_n) < min_step:
            return
        self._last_plain_t = now
        self._last_plain_n = self.completed
        print(f"    {self.completed}/{self.total} trials  "
              f"({self.rate():.2f}/s, ETA {fmt_secs(self.overall_eta())})  "
              f"best {self._best_line()}", file=self._stream, flush=True)
