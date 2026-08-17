"""Live optimizer dashboard.

Presentation only. Every entry point is wrapped so a rendering failure degrades to
plain text or silence and can never propagate into the study: a broken terminal
must not cost a campaign. The dashboard owns no optimization state — it is handed
values that already exist elsewhere.

Falls back to an occasional one-line progress print when stdout is not a TTY, when
NO_COLOR is set, or when rich is unavailable.
"""

import sys
import time
from typing import Any, Dict, Optional

from . import ui

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    _RICH = True
except Exception:                                    # pragma: no cover
    _RICH = False

STAGE_NAMES = (
    "Data Preparation", "Strategy Optimization", "Strategy Robustness",
    "Risk Management", "Bollinger", "Final Selection",
)

# Provisional cost of each remaining stage, as a multiple of the Phase-A budget.
# Used only to project an overall ETA before those stages have ever run; each is
# replaced by measured timing as soon as the stage executes. Phase A dominates
# because it is the only stage that searches a full parameter space.
STAGE_COST_FACTOR = {3: 0.25, 4: 0.35, 5: 0.30, 6: 0.10}

BAR_WIDTH = 28

STATUS_COLOUR = {
    "PASS": "green",
    "RUNNING": "cyan",
    "WAITING": "yellow",
    "SKIPPED": "dim",
    "NOT IMPLEMENTED": "dim",
    "FAILED": "red",
}


def fmt_secs(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:
        return "--"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def stage_status_from_preset(stages, running: int = 2) -> Dict[int, str]:
    """Initial six-stage status map. Disabled stages are SKIPPED, not WAITING."""
    enabled = {
        1: True,
        2: bool(stages.strategy_optimization),
        3: bool(stages.strategy_optimization),
        4: bool(stages.risk_management),
        5: bool(stages.bollinger),
        6: True,
    }
    status = {}
    for idx in range(1, 7):
        if not enabled[idx]:
            status[idx] = "SKIPPED"
        elif idx < running:
            status[idx] = "PASS"
        elif idx == running:
            status[idx] = "RUNNING"
        else:
            status[idx] = "WAITING"
    return status


class Stage3Dashboard:
    """In-place display for the stage-3 robustness loop.

    Same isolation contract as the Phase-A dashboard: presentation only, every
    entry point swallows its own failures, and no robustness number is computed
    here — values are handed in already decided.
    """

    def __init__(self, total_evals: int, n_candidates: int,
                 stage_status: Dict[int, str], enabled: bool = True,
                 campaign_started: Optional[float] = None):
        self.total = max(1, int(total_evals))
        self.n_candidates = max(1, int(n_candidates))
        self.stage_status = dict(stage_status)
        self.done = 0
        self.candidate_index = 0
        self.candidate_rank = 0
        self.test = "-"
        self.best: Optional[Dict[str, Any]] = None
        self.started = time.time()
        self.campaign_started = campaign_started or self.started
        self._live = None
        self._best_panel = None
        self._best_dirty = True
        self._last_plain = 0.0
        self.enabled = bool(enabled) and _RICH and ui.colour_enabled()

    def __enter__(self):
        if self.enabled:
            try:
                self._live = Live(self.render(), console=Console(),
                                  refresh_per_second=4, transient=False)
                self._live.__enter__()
            except Exception:
                self.enabled = False
                self._live = None
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            try:
                self._live.update(self.render())
                self._live.__exit__(*exc)
            except Exception:
                pass
            finally:
                self._live = None
        return False

    def set_candidate(self, index: int, rank: int, test: str):
        try:
            self.candidate_index, self.candidate_rank, self.test = index, rank, test
            self._refresh()
        except Exception:
            pass

    def evaluation_done(self, n: int = 1):
        try:
            self.done += n
            self._refresh()
        except Exception:
            pass

    def set_best(self, summary):
        """Called with the strongest robust candidate found so far."""
        try:
            if summary is None:
                return
            if self.best is None or summary.score > self.best["score"]:
                self.best = {
                    "score": summary.score, "train_rank": summary.train_rank,
                    "perturb_rate": summary.perturb_profitable_rate,
                    "profitable_regimes": summary.profitable_regimes,
                    "regimes": summary.regimes_tested,
                    "median_pf": summary.median_regime_pf,
                    "worst_dd": summary.worst_regime_dd,
                }
                self._best_dirty = True
            self._refresh()
        except Exception:
            pass

    def finish_stage(self, index: int, status: str = "PASS"):
        self.stage_status[index] = status
        try:
            self._refresh()
        except Exception:
            pass

    def _refresh(self):
        if self._live is not None:
            try:
                self._live.update(self.render())
                return
            except Exception:
                self._live = None
                self.enabled = False
        self._plain()

    def _plain(self):
        now = time.time()
        if now - self._last_plain < 5 and self.done < self.total:
            return
        self._last_plain = now
        best = f"{self.best['score']:.1f}" if self.best else "--"
        print(f"      candidate {self.candidate_index}/{self.n_candidates} "
              f"({self.test})  eval {self.done}/{self.total}  "
              f"best={best}  eta={fmt_secs(self.stage_eta)}", flush=True)

    @property
    def stage_elapsed(self) -> float:
        return time.time() - self.started

    @property
    def overall_elapsed(self) -> float:
        return time.time() - self.campaign_started

    @property
    def stage_eta(self) -> Optional[float]:
        if self.done == 0:
            return None
        return (self.stage_elapsed / self.done) * max(0, self.total - self.done)

    @property
    def overall_eta(self) -> Optional[float]:
        left = self.stage_eta
        if left is None:
            return None
        per = self.stage_elapsed / max(1, self.done)
        full = per * self.total
        pending = sum(f for i, f in STAGE_COST_FACTOR.items()
                      if i > 3 and self.stage_status.get(i) == "WAITING")
        return left + full * pending

    def _stage_table(self):
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        for i, name in enumerate(STAGE_NAMES, start=1):
            status = self.stage_status.get(i, "WAITING")
            table.add_row(Text(f"[{i}/6] {name:<22}"),
                          Text(status, style=STATUS_COLOUR.get(status, "yellow")))
        return table

    def _progress_panel(self):
        filled = int(BAR_WIDTH * self.done / self.total)
        bar = Text("█" * filled, style="cyan")
        bar.append("░" * (BAR_WIDTH - filled), style="dim")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Candidate", f"{self.candidate_index} / {self.n_candidates}"
                     + (f"  (TRAIN rank {self.candidate_rank})"
                        if self.candidate_rank else ""))
        grid.add_row("Test", Text(self.test, style="cyan"))
        grid.add_row("Evaluations", f"{self.done} / {self.total}")
        grid.add_row("Progress", bar)
        grid.add_row("Stage elapsed", fmt_secs(self.stage_elapsed))
        grid.add_row("Stage ETA", fmt_secs(self.stage_eta))
        grid.add_row("Overall elapsed", fmt_secs(self.overall_elapsed))
        grid.add_row("Overall ETA", Text(fmt_secs(self.overall_eta), style="dim"))
        return Panel(grid, title="Stage 3 — Robustness", border_style="cyan")

    def best_panel(self):
        if self._best_dirty or self._best_panel is None:
            if self.best is None:
                self._best_panel = Panel(
                    Text("no robust candidate yet", style="yellow"),
                    title="Strongest robust candidate", border_style="yellow")
            else:
                b = self.best
                grid = Table.grid(padding=(0, 2))
                grid.add_column(style="dim", no_wrap=True)
                grid.add_column(no_wrap=True)
                grid.add_row("TRAIN rank", str(b["train_rank"]))
                grid.add_row("Robustness score",
                             Text(f"{b['score']:.2f}", style="green"))
                grid.add_row("Perturbation pass", f"{b['perturb_rate']:.0%}")
                grid.add_row("Profitable regimes",
                             f"{b['profitable_regimes']} / {b['regimes']}")
                grid.add_row("Median regime PF", f"{b['median_pf']:.2f}")
                grid.add_row("Worst regime DD", f"{b['worst_dd']:.2f}%")
                self._best_panel = Panel(grid, title="Strongest robust candidate",
                                         border_style="green")
            self._best_dirty = False
        return self._best_panel

    def render(self):
        return Panel(Group(self._stage_table(), Text(""),
                           self._progress_panel(), self.best_panel()),
                     title="AUTO OPTIMISER", border_style="cyan")


class Stage6Dashboard:
    """In-place display for final selection. Presentation only."""

    def __init__(self, n_candidates, stage_status, enabled=True,
                 campaign_started=None):
        self.n_candidates = max(1, int(n_candidates))
        self.stage_status = dict(stage_status)
        self.pareto = None
        self.champion = None
        self.champion_score = 0.0
        self.frozen_checksum = None
        self.unseen_state = "LOCKED"
        self.results = []
        self.winner = None
        self.winner_status = None
        self.config_path = None
        self.started = time.time()
        self.campaign_started = campaign_started or self.started
        self._live = None
        self.enabled = bool(enabled) and _RICH and ui.colour_enabled()

    def __enter__(self):
        if self.enabled:
            try:
                self._live = Live(self.render(), console=Console(),
                                  refresh_per_second=4, transient=False)
                self._live.__enter__()
            except Exception:
                self.enabled = False
                self._live = None
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            try:
                self._live.update(self.render())
                self._live.__exit__(*exc)
            except Exception:
                pass
            finally:
                self._live = None
        return False

    def _safe(self, fn):
        try:
            fn()
            if self._live is not None:
                self._live.update(self.render())
        except Exception:
            self._live = None

    def set_pareto(self, survivors, total):
        self._safe(lambda: setattr(self, "pareto", (survivors, total)))

    def set_champion(self, candidate, score):
        def apply():
            self.champion = dict(candidate)
            self.champion_score = score
        self._safe(apply)

    def decision_frozen(self, checksum):
        self._safe(lambda: setattr(self, "frozen_checksum", checksum))

    def unseen_unlocked(self):
        self._safe(lambda: setattr(self, "unseen_state", "CONFIRMATION RUNNING"))

    def unseen_result(self, pos, candidate, metrics, status):
        def apply():
            self.results.append({"pos": pos, "rank": candidate["train_rank"],
                                 "metrics": metrics or {}, "status": status})
        self._safe(apply)

    def set_winner(self, candidate, status):
        def apply():
            self.winner = dict(candidate)
            self.winner_status = status
            self.unseen_state = status
        self._safe(apply)

    def config_written(self, path):
        self._safe(lambda: setattr(self, "config_path", path))

    def finish_stage(self, index, status="PASS"):
        self._safe(lambda: self.stage_status.__setitem__(index, status))

    def _stage_table(self):
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        for i, name in enumerate(STAGE_NAMES, start=1):
            st = self.stage_status.get(i, "WAITING")
            table.add_row(Text(f"[{i}/6] {name:<22}"),
                          Text(st, style=STATUS_COLOUR.get(st, "yellow")))
        return table

    def render(self):
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Pre-UNSEEN finalists", str(self.n_candidates))
        grid.add_row("Pareto survivors",
                     f"{self.pareto[0]} / {self.pareto[1]}" if self.pareto else "-")
        if self.champion:
            grid.add_row("Champion",
                         f"TRAIN rank {self.champion['train_rank']} "
                         f"(TOPSIS {self.champion_score:.3f})")
        if self.frozen_checksum:
            grid.add_row("Decision frozen",
                         Text(f"OK  {self.frozen_checksum[:16]}", style="green"))
        state_style = {"LOCKED": "yellow", "CONFIRMED": "green",
                       "DEGRADED": "yellow", "FAILED": "red"}.get(
                           self.unseen_state, "cyan")
        grid.add_row("UNSEEN", Text(self.unseen_state, style=state_style))

        body = [self._stage_table(), Text(""),
                Panel(grid, title="Stage 6 — Final Selection", border_style="cyan")]

        if self.results:
            table = Table.grid(padding=(0, 2))
            for col in range(7):
                table.add_column(no_wrap=True)
            table.add_row(*[Text(h, style="dim") for h in
                            ("#", "rank", "return", "PF", "DD", "trades", "status")])
            for r in self.results:
                m = r["metrics"]
                style = {"CONFIRMED": "green", "DEGRADED": "yellow",
                         "FAILED": "red"}.get(r["status"], "white")
                table.add_row(str(r["pos"]), str(r["rank"]),
                              f"{m.get('net_return_pct', 0):.2f}%",
                              f"{m.get('profit_factor', 0):.2f}",
                              f"{m.get('max_dd_pct', 0):.2f}%",
                              str(m.get("trades", 0)),
                              Text(r["status"], style=style))
            body.append(Panel(table, title="UNSEEN confirmation",
                              border_style="cyan"))

        if self.winner:
            win = Table.grid(padding=(0, 2))
            win.add_column(style="dim", no_wrap=True)
            win.add_column(no_wrap=True)
            win.add_row("WINNER", Text(f"TRAIN rank {self.winner['train_rank']}",
                                       style="green"))
            win.add_row("UNSEEN", Text(self.winner_status or "-", style="green"))
            win.add_row("Bollinger",
                        "ON" if self.winner.get("bollinger_enabled") else "OFF")
            if self.config_path:
                win.add_row("Config", Text(self.config_path, style="green"))
            body.append(Panel(win, title="Final", border_style="green"))

        return Panel(Group(*body), title="AUTO OPTIMISER", border_style="cyan")


class Stage5Dashboard:
    """In-place display for the stage-5 per-candidate Bollinger search.

    Presentation only; every entry point swallows its own failures.
    """

    def __init__(self, trials_per_candidate, n_candidates, stage_status,
                 enabled=True, campaign_started=None):
        self.per_candidate = max(1, int(trials_per_candidate))
        self.n_candidates = max(1, int(n_candidates))
        self.total = self.per_candidate * self.n_candidates
        self.stage_status = dict(stage_status)
        self.candidate_index = 0
        self.candidate_rank = 0
        self.trials_done = 0
        self.stage_trials = 0
        self.off: Optional[Dict[str, Any]] = None
        self.best: Optional[Dict[str, Any]] = None
        self.started = time.time()
        self.campaign_started = campaign_started or self.started
        self._live = None
        self._best_panel = None
        self._best_dirty = True
        self._last_plain = 0.0
        self.enabled = bool(enabled) and _RICH and ui.colour_enabled()

    def __enter__(self):
        if self.enabled:
            try:
                self._live = Live(self.render(), console=Console(),
                                  refresh_per_second=4, transient=False)
                self._live.__enter__()
            except Exception:
                self.enabled = False
                self._live = None
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            try:
                self._live.update(self.render())
                self._live.__exit__(*exc)
            except Exception:
                pass
            finally:
                self._live = None
        return False

    def set_candidate(self, index, rank):
        try:
            self.candidate_index, self.candidate_rank = index, rank
            self.stage_trials = 0
            self.off = None
            self._refresh()
        except Exception:
            pass

    def set_off_baseline(self, metrics):
        try:
            self.off = dict(metrics or {})
            self._best_dirty = True
            self._refresh()
        except Exception:
            pass

    def trial_done(self, filt, metrics, score, retention):
        try:
            self.trials_done += 1
            self.stage_trials += 1
            if metrics and (self.best is None or score > self.best["score"]):
                self.best = {"score": score, "rank": self.candidate_rank,
                             "ret": metrics.get("net_return_pct"),
                             "pf": metrics.get("profit_factor"),
                             "dd": metrics.get("max_dd_pct"),
                             "trades": metrics.get("trades"),
                             "retention": retention, "params": dict(filt)}
                self._best_dirty = True
            self._refresh()
        except Exception:
            pass

    def set_best(self, entry):
        try:
            self._best_dirty = True
            self._refresh()
        except Exception:
            pass

    def finish_stage(self, index, status="PASS"):
        self.stage_status[index] = status
        try:
            self._refresh()
        except Exception:
            pass

    def _refresh(self):
        if self._live is not None:
            try:
                self._live.update(self.render())
                return
            except Exception:
                self._live = None
                self.enabled = False
        self._plain()

    def _plain(self):
        now = time.time()
        if now - self._last_plain < 5 and self.trials_done < self.total:
            return
        self._last_plain = now
        best = f"{self.best['score']:.1f}" if self.best else "--"
        print(f"      candidate {self.candidate_index}/{self.n_candidates} "
              f"(rank {self.candidate_rank})  filter trial "
              f"{self.stage_trials}/{self.per_candidate}  best={best}  "
              f"eta={fmt_secs(self.stage_eta)}", flush=True)

    @property
    def stage_elapsed(self):
        return time.time() - self.started

    @property
    def overall_elapsed(self):
        return time.time() - self.campaign_started

    @property
    def stage_eta(self):
        if self.trials_done == 0:
            return None
        return (self.stage_elapsed / self.trials_done) * max(
            0, self.total - self.trials_done)

    @property
    def overall_eta(self):
        left = self.stage_eta
        if left is None:
            return None
        per = self.stage_elapsed / max(1, self.trials_done)
        pending = sum(f for i, f in STAGE_COST_FACTOR.items()
                      if i > 5 and self.stage_status.get(i) == "WAITING")
        return left + per * self.total * pending

    def _stage_table(self):
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        for i, name in enumerate(STAGE_NAMES, start=1):
            status = self.stage_status.get(i, "WAITING")
            table.add_row(Text(f"[{i}/6] {name:<22}"),
                          Text(status, style=STATUS_COLOUR.get(status, "yellow")))
        return table

    def _progress_panel(self):
        filled = int(BAR_WIDTH * self.stage_trials / self.per_candidate)
        bar = Text("█" * filled, style="cyan")
        bar.append("░" * (BAR_WIDTH - filled), style="dim")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Candidate", f"{self.candidate_index} / {self.n_candidates}"
                     + (f"  (TRAIN rank {self.candidate_rank})"
                        if self.candidate_rank else ""))
        grid.add_row("Filter trials", f"{self.stage_trials} / {self.per_candidate}")
        grid.add_row("Progress", bar)
        grid.add_row("Stage elapsed", fmt_secs(self.stage_elapsed))
        grid.add_row("Stage ETA", fmt_secs(self.stage_eta))
        grid.add_row("Overall ETA", Text(fmt_secs(self.overall_eta), style="dim"))
        return Panel(grid, title="Stage 5 — Bollinger", border_style="cyan")

    def _off_panel(self):
        if not self.off:
            return Panel(Text("not evaluated yet", style="dim"),
                         title="OFF baseline", border_style="yellow")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Return", f"{self.off.get('net_return_pct', 0):.2f}%")
        grid.add_row("PF", f"{self.off.get('profit_factor', 0):.3f}")
        grid.add_row("Max DD", f"{self.off.get('max_dd_pct', 0):.2f}%")
        grid.add_row("Trades", str(self.off.get("trades", 0)))
        return Panel(grid, title="OFF baseline", border_style="yellow")

    def best_panel(self):
        if self._best_dirty or self._best_panel is None:
            if self.best is None:
                self._best_panel = Panel(
                    Text("no filter has beaten OFF yet", style="yellow"),
                    title="Current best filter", border_style="yellow")
            else:
                b = self.best
                p = b["params"]
                grid = Table.grid(padding=(0, 2))
                grid.add_column(style="dim", no_wrap=True)
                grid.add_column(no_wrap=True)
                style = "green" if b["score"] > 0 else "yellow"
                grid.add_row("Score", Text(f"{b['score']:.2f}", style=style))
                grid.add_row("Return", f"{b['ret']:.2f}%")
                grid.add_row("PF", f"{b['pf']:.3f}")
                grid.add_row("Max DD", f"{b['dd']:.2f}%")
                grid.add_row("Trades", str(b["trades"]))
                grid.add_row("Retention", f"{b['retention']:.0%}")
                grid.add_row("", "")
                grid.add_row("Length / std",
                             f"{p['length']} / {p['std']:.1f}")
                grid.add_row("Min bandwidth", f"{p['min_bandwidth_pct']:.2f}")
                grid.add_row("Expansion",
                             f"{p['expansion_lookback']} @ {p['expansion_min_ratio']:.2f}")
                grid.add_row("Mid distance", f"{p['min_mid_distance']:.2f}")
                self._best_panel = Panel(grid, title="Current best filter",
                                         border_style="green")
            self._best_dirty = False
        return self._best_panel

    def render(self):
        return Panel(Group(self._stage_table(), Text(""), self._progress_panel(),
                           self._off_panel(), self.best_panel()),
                     title="AUTO OPTIMISER", border_style="cyan")


class Stage4Dashboard:
    """In-place display for the stage-4 per-strategy risk search.

    Presentation only, same isolation contract as the other dashboards.
    """

    def __init__(self, trials_per_strategy: int, n_strategies: int,
                 stage_status: Dict[int, str], enabled: bool = True,
                 campaign_started: Optional[float] = None):
        self.per_strategy = max(1, int(trials_per_strategy))
        self.n_strategies = max(1, int(n_strategies))
        self.total = self.per_strategy * self.n_strategies
        self.stage_status = dict(stage_status)
        self.candidate_index = 0
        self.candidate_rank = 0
        self.trials_done = 0
        self.stage_trials = 0
        self.rejected = 0
        self.current_policy: Optional[Dict[str, Any]] = None
        self.best: Optional[Dict[str, Any]] = None
        self.started = time.time()
        self.campaign_started = campaign_started or self.started
        self._live = None
        self._best_panel = None
        self._best_dirty = True
        self._last_plain = 0.0
        self.enabled = bool(enabled) and _RICH and ui.colour_enabled()

    def __enter__(self):
        if self.enabled:
            try:
                self._live = Live(self.render(), console=Console(),
                                  refresh_per_second=4, transient=False)
                self._live.__enter__()
            except Exception:
                self.enabled = False
                self._live = None
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            try:
                self._live.update(self.render())
                self._live.__exit__(*exc)
            except Exception:
                pass
            finally:
                self._live = None
        return False

    def set_candidate(self, index: int, rank: int):
        try:
            self.candidate_index, self.candidate_rank = index, rank
            self.stage_trials = 0
            self._refresh()
        except Exception:
            pass

    def candidate_finished(self):
        try:
            self._refresh()
        except Exception:
            pass

    def trial_done(self, policy, metrics, score, rejected=False):
        try:
            self.trials_done += 1
            self.stage_trials += 1
            self.current_policy = policy
            if rejected:
                self.rejected += 1
            self._refresh()
        except Exception:
            pass

    def set_best(self, rank, entry):
        try:
            if self.best is None or entry["train_score"] > self.best["score"]:
                self.best = {
                    "rank": rank, "score": entry["train_score"],
                    "leverage": entry["leverage"],
                    "risk": entry["risk_per_trade_pct"],
                    "alloc": entry["max_position_allocation_pct"],
                    "ret": entry.get("train_net_return_pct"),
                    "pf": entry.get("train_profit_factor"),
                    "sharpe": entry.get("train_sharpe"),
                    "dd": entry.get("train_max_dd_pct"),
                    "trades": entry.get("train_trades"),
                }
                self._best_dirty = True
            self._refresh()
        except Exception:
            pass

    def finish_stage(self, index: int, status: str = "PASS"):
        self.stage_status[index] = status
        try:
            self._refresh()
        except Exception:
            pass

    def _refresh(self):
        if self._live is not None:
            try:
                self._live.update(self.render())
                return
            except Exception:
                self._live = None
                self.enabled = False
        self._plain()

    def _plain(self):
        now = time.time()
        if now - self._last_plain < 5 and self.trials_done < self.total:
            return
        self._last_plain = now
        best = f"{self.best['score']:.1f}" if self.best else "--"
        print(f"      strategy {self.candidate_index}/{self.n_strategies} "
              f"(rank {self.candidate_rank})  risk trial "
              f"{self.stage_trials}/{self.per_strategy}  best={best}  "
              f"eta={fmt_secs(self.stage_eta)}", flush=True)

    @property
    def stage_elapsed(self):
        return time.time() - self.started

    @property
    def overall_elapsed(self):
        return time.time() - self.campaign_started

    @property
    def trials_per_sec(self):
        return self.trials_done / max(1e-9, self.stage_elapsed)

    @property
    def stage_eta(self):
        if self.trials_done == 0:
            return None
        return (self.stage_elapsed / self.trials_done) * max(0, self.total - self.trials_done)

    @property
    def overall_eta(self):
        left = self.stage_eta
        if left is None:
            return None
        per = self.stage_elapsed / max(1, self.trials_done)
        full = per * self.total
        pending = sum(f for i, f in STAGE_COST_FACTOR.items()
                      if i > 4 and self.stage_status.get(i) == "WAITING")
        return left + full * pending

    def _stage_table(self):
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        for i, name in enumerate(STAGE_NAMES, start=1):
            status = self.stage_status.get(i, "WAITING")
            table.add_row(Text(f"[{i}/6] {name:<22}"),
                          Text(status, style=STATUS_COLOUR.get(status, "yellow")))
        return table

    def _progress_panel(self):
        filled = int(BAR_WIDTH * self.stage_trials / self.per_strategy)
        bar = Text("█" * filled, style="cyan")
        bar.append("░" * (BAR_WIDTH - filled), style="dim")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Candidate", f"{self.candidate_index} / {self.n_strategies}"
                     + (f"  (TRAIN rank {self.candidate_rank})"
                        if self.candidate_rank else ""))
        grid.add_row("Risk trials", f"{self.stage_trials} / {self.per_strategy}")
        grid.add_row("Progress", bar)
        grid.add_row("Stage elapsed", fmt_secs(self.stage_elapsed))
        grid.add_row("Stage ETA", fmt_secs(self.stage_eta))
        grid.add_row("Overall ETA", Text(fmt_secs(self.overall_eta), style="dim"))
        grid.add_row("Trials/sec", f"{self.trials_per_sec:.2f}")
        grid.add_row("Rejected", Text(str(self.rejected),
                                      style="yellow" if self.rejected else "dim"))
        p = self.current_policy or {}
        grid.add_row("", "")
        grid.add_row("Leverage", f"{p.get('leverage', 0):.1f}x" if p else "-")
        grid.add_row("Risk/trade", f"{p.get('risk_per_trade_pct', 0):.1f}%" if p else "-")
        grid.add_row("Allocation",
                     f"{p.get('max_position_allocation_pct', 0):.0f}%" if p else "-")
        return Panel(grid, title="Stage 4 — Risk Management", border_style="cyan")

    def best_panel(self):
        if self._best_dirty or self._best_panel is None:
            if self.best is None:
                self._best_panel = Panel(Text("no validated risk policy yet",
                                              style="yellow"),
                                         title="Current best",
                                         border_style="yellow")
            else:
                b = self.best
                grid = Table.grid(padding=(0, 2))
                grid.add_column(style="dim", no_wrap=True)
                grid.add_column(no_wrap=True)
                grid.add_row("TRAIN rank", str(b["rank"]))
                grid.add_row("Risk score", Text(f"{b['score']:.2f}", style="green"))
                grid.add_row("Leverage", f"{b['leverage']:.1f}x")
                grid.add_row("Risk/trade", f"{b['risk']:.1f}%")
                grid.add_row("Allocation", f"{b['alloc']:.0f}%")
                grid.add_row("Return", Text(f"{b['ret']:.2f}%", style="green"))
                grid.add_row("PF", f"{b['pf']:.3f}")
                grid.add_row("Sharpe", f"{b['sharpe']:.2f}")
                grid.add_row("Max DD", f"{b['dd']:.2f}%")
                grid.add_row("Trades", str(int(b["trades"])))
                self._best_panel = Panel(grid, title="Current best",
                                         border_style="green")
            self._best_dirty = False
        return self._best_panel

    def render(self):
        return Panel(Group(self._stage_table(), Text(""),
                           self._progress_panel(), self.best_panel()),
                     title="AUTO OPTIMISER", border_style="cyan")


class PhaseADashboard:
    """In-place display for the Phase-A trial loop."""

    def __init__(self, total_trials: int, stage_status: Dict[int, str],
                 enabled: bool = True, campaign_started: Optional[float] = None):
        self.total = max(1, int(total_trials))
        self.stage_status = dict(stage_status)
        self.completed = 0
        self.rejected = 0
        self.best: Optional[Dict[str, Any]] = None
        self.started = time.time()
        self.campaign_started = campaign_started or self.started
        self._live = None
        self._console = None
        self._last_plain = 0.0
        self._best_panel = None          # rebuilt only when a new best arrives
        self._best_dirty = True
        self.enabled = bool(enabled) and _RICH and ui.colour_enabled()

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self):
        if self.enabled:
            try:
                self._console = Console()
                self._live = Live(self.render(), console=self._console,
                                  refresh_per_second=4, transient=False)
                self._live.__enter__()
            except Exception:
                self.enabled = False
                self._live = None
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            try:
                self._live.update(self.render())
                self._live.__exit__(*exc)
            except Exception:
                pass
            finally:
                self._live = None
        return False

    # -- updates -------------------------------------------------------------

    def trial_done(self, metrics: Optional[Dict[str, Any]], score: float,
                   params: Dict[str, Any], rejected: bool):
        """Record one finished trial. Never raises."""
        try:
            self.completed += 1
            if rejected:
                self.rejected += 1
            elif self.best is None or score > self.best["score"]:
                self.best = {"score": score, "metrics": metrics or {},
                             "params": params, "trial": self.completed}
                self._best_dirty = True
            self._refresh()
        except Exception:
            pass

    def finish_stage(self, index: int, status: str = "PASS"):
        self.stage_status[index] = status
        try:
            self._refresh()
        except Exception:
            pass

    def _refresh(self):
        if self._live is not None:
            try:
                self._live.update(self.render())
                return
            except Exception:
                self._live = None
                self.enabled = False
        self._plain()

    def _plain(self):
        """Non-TTY fallback: one short line, at most every few seconds."""
        now = time.time()
        if now - self._last_plain < 5 and self.completed < self.total:
            return
        self._last_plain = now
        best = f"{self.best['score']:.2f}" if self.best else "--"
        print(f"      trial {self.completed}/{self.total}  "
              f"{self.trials_per_sec:.2f}/s  best={best}  "
              f"rejected={self.rejected}  eta={fmt_secs(self.stage_eta)}",
              file=sys.stdout, flush=True)

    # -- derived numbers -----------------------------------------------------

    @property
    def stage_elapsed(self) -> float:
        return time.time() - self.started

    @property
    def overall_elapsed(self) -> float:
        return time.time() - self.campaign_started

    @property
    def trials_per_sec(self) -> float:
        return self.completed / max(1e-9, self.stage_elapsed)

    @property
    def stage_eta(self) -> Optional[float]:
        """Remaining Phase-A seconds, from measured throughput."""
        if self.completed == 0:
            return None
        per_trial = self.stage_elapsed / self.completed
        return per_trial * max(0, self.total - self.completed)

    @property
    def overall_eta(self) -> Optional[float]:
        """Remaining campaign seconds: this stage plus provisional later stages."""
        stage_left = self.stage_eta
        if stage_left is None:
            return None
        per_trial = self.stage_elapsed / max(1, self.completed)
        phase_a_full = per_trial * self.total
        pending = sum(
            factor for idx, factor in STAGE_COST_FACTOR.items()
            if self.stage_status.get(idx) == "WAITING"
        )
        return stage_left + phase_a_full * pending

    # -- rendering -----------------------------------------------------------

    def _stage_table(self):
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        for i, name in enumerate(STAGE_NAMES, start=1):
            status = self.stage_status.get(i, "WAITING")
            table.add_row(Text(f"[{i}/6] {name:<22}"),
                          Text(status, style=STATUS_COLOUR.get(status, "yellow")))
        return table

    def _progress_panel(self):
        filled = int(BAR_WIDTH * self.completed / self.total)
        bar = Text("█" * filled, style="cyan")
        bar.append("░" * (BAR_WIDTH - filled), style="dim")

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Trials", f"{self.completed} / {self.total}")
        grid.add_row("Progress", bar)
        grid.add_row("Stage elapsed", fmt_secs(self.stage_elapsed))
        grid.add_row("Stage ETA", fmt_secs(self.stage_eta))
        grid.add_row("Overall elapsed", fmt_secs(self.overall_elapsed))
        grid.add_row("Overall ETA", Text(fmt_secs(self.overall_eta), style="dim"))
        grid.add_row("Trials/sec", f"{self.trials_per_sec:.2f}")
        grid.add_row("Rejected", Text(str(self.rejected),
                                      style="yellow" if self.rejected else "dim"))
        return Panel(grid, title="Phase A", border_style="cyan")

    def _build_best_panel(self):
        if self.best is None:
            return Panel(Text("no admissible candidate yet", style="yellow"),
                         title="Current best", border_style="yellow")

        m = self.best["metrics"]
        p = self.best["params"]

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_row("Score", Text(f"{self.best['score']:.2f}", style="green"))
        grid.add_row("Return", Text(f"{m.get('net_return_pct', 0):.2f}%", style="green"))
        grid.add_row("PF", f"{m.get('profit_factor', 0):.3f}")
        grid.add_row("Sharpe", f"{m.get('sharpe', 0):.2f}")
        grid.add_row("Max DD", f"{m.get('max_dd_pct', 0):.2f}%")
        grid.add_row("Trades", str(m.get("trades", 0)))
        grid.add_row("", "")
        grid.add_row("EMA", str(p["ema_period"]))
        grid.add_row("RSI", str(p["rsi_period"]))
        grid.add_row("OB / OS", f"{p['rsi_overbought']:.0f} / {p['rsi_oversold']:.0f}")
        grid.add_row("ATR", str(p["atr_period"]))
        grid.add_row("Consolidation",
                     f"{p['consolidation_candles']} @ {p['consolidation_atr_mult']:.1f}")
        grid.add_row("Swing", str(p["swing_lookback"]))
        grid.add_row("Volume SMA", str(p["volume_sma_period"]))
        grid.add_row("Volume Mult", f"{p['volume_mult']:.1f}x")
        grid.add_row("RR", f"{p['risk_reward_ratio']:.1f}")
        return Panel(grid, title=f"Current best (trial {self.best['trial']})",
                     border_style="green")

    def best_panel(self):
        """Cached: rebuilt only when a new best candidate appears."""
        if self._best_dirty or self._best_panel is None:
            self._best_panel = self._build_best_panel()
            self._best_dirty = False
        return self._best_panel

    def render(self):
        return Panel(
            Group(self._stage_table(), Text(""),
                  self._progress_panel(), self.best_panel()),
            title="AUTO OPTIMISER", border_style="cyan",
        )
