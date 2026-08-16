"""
Paper Forward Trading Redesigned Rich Terminal Dashboard.
Compact, clean, 2-column boxed layout for live forward testing.

Features:
1. Top Section: 2 Realtime Terminal Chart Panels (Chart A & Chart B) side-by-side using terminal ASCII/Unicode candles.
2. Left Column: "Market + Trade" (without Previous Trade PnL) + New "Recent Trade History" panel (last 3 completed trades).
3. Right Column: Separate "Account" panel + Separate "Performance" panel.
4. Bottom Status Row: Feed speed, last update IST, reconnects, CPU/RAM/Disk %, state save status.

All timestamps rendered in IST (UTC+5:30).
"""

import io
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.segment import Segment, Segments
from rich.table import Table
from rich.text import Text
from common.utils import resolution_to_seconds

IST = timezone(timedelta(hours=5, minutes=30))


class PaperDashboard:
    """Redesigned compact, clean Rich terminal dashboard with top charts, trade history, and split account/performance panels."""

    # Vertical budget: total rendered lines = FIXED_CHROME + large-chart plot_h.
    # Measured empirically (see tests); keep in sync if panels are added/removed.
    # Total rendered height = FIXED_CHROME + large-chart plot_h. The chart is the only
    # elastic element, so it absorbs all spare vertical space and the dashboard fills
    # exactly one screen. Below MIN_CHART_H the terminal is too short and Rich's
    # alternate-screen mode crops rather than scrolls.
    FIXED_CHROME = 36
    MIN_CHART_H = 4
    MAX_CHART_H = 30

    def __init__(self):
        self.console = Console()
        self._chart_h_override = None      # rows granted to the large chart this frame
        self._chrome_rows = self.FIXED_CHROME
        self._chrome_shape = None          # layout shape the cached chrome measurement is valid for

    @staticmethod
    def _usd_size(t: Dict[str, Any]) -> float:
        """USD notional of a trade for DISPLAY only: quantity x entry/fill price.

        Stored quantities are never modified — this is a presentation-layer derivation.
        Falls back to the engine's recorded notional (itself entry_price * quantity) if
        the multiplication is unavailable, so the column degrades to the same number
        rather than to a misleading zero.
        """
        qty = float(t.get("size", 0.0) or 0.0)
        entry = float(t.get("entry_price", 0.0) or 0.0)
        usd = qty * entry
        if usd <= 0.0:
            usd = float(t.get("notional", 0.0) or 0.0)
        return usd

    @staticmethod
    def _pct_bar(pct: float, width: int = 15) -> str:
        """Horizontal 0-100% bar with sub-cell resolution.

        A plain `int(pct/100 * 15)` bar has only 15 steps (6.67% each) AND truncates, so
        49% and 53% both floor to 7 cells and render identically. Eighth-width block
        characters give 15*8 = 120 steps (~0.83% each), so neighbouring values are
        visibly different and the bar length matches the printed number.
        """
        pct = max(0.0, min(100.0, float(pct)))
        eighths = int(round((pct / 100.0) * width * 8))
        full, rem = divmod(eighths, 8)
        bar = "█" * full
        if rem:
            bar += "▏▎▍▌▋▊▉"[rem - 1]
        return bar.ljust(width, "░")

    @staticmethod
    def _fmt_volume(v: float) -> str:
        """Compact volume label (1.23M / 456.7K / 89.1)."""
        av = abs(v)
        if av >= 1_000_000_000:
            return f"{v / 1_000_000_000:.2f}B"
        if av >= 1_000_000:
            return f"{v / 1_000_000:.2f}M"
        if av >= 1_000:
            return f"{v / 1_000:.1f}K"
        return f"{v:.1f}"

    def _render_volume_panel(self, candles: List[Dict[str, Any]], title: str) -> Panel:
        """Render a terminal-native volume histogram inside a Panel.

        Bars are coloured by the direction of their own candle (green = close >= open),
        which is the standard volume-histogram convention. Height uses eighth-block
        characters for sub-cell resolution. Purely presentational.
        """
        if not candles:
            txt = Text("Waiting for market volume data...", style="dim white")
            return Panel(txt, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan")

        # Same window as the previous candle chart so the panel keeps its proportions
        c_sub = candles[-35:]
        n_c = len(c_sub)
        vols = [float(c.get("volume", 0.0) or 0.0) for c in c_sub]

        max_v = max(vols) if vols else 0.0
        latest_v = vols[-1] if vols else 0.0
        avg_v = (sum(vols) / len(vols)) if vols else 0.0

        # Header summary line
        header = Text()
        header.append(
            f"Max: {self._fmt_volume(max_v)}  │  Avg: {self._fmt_volume(avg_v)}  │  Now: ",
            style="dim white",
        )
        header.append(f"{self._fmt_volume(latest_v)}", style="bold cyan")
        if avg_v > 0:
            rel = (latest_v / avg_v) * 100.0
            header.append(f" ({rel:.0f}% of avg)", style="bold green" if rel >= 100.0 else "dim white")
        header.append("\n", style="dim white")

        # Two-row histogram, eighth-block sub-cell resolution (matches old panel height)
        plot_h = 2
        levels = " ▁▂▃▄▅▆▇█"
        grid = [[" " for _ in range(n_c)] for _ in range(plot_h)]
        styles = [["dim white" for _ in range(n_c)] for _ in range(plot_h)]

        for x, c in enumerate(c_sub):
            v = vols[x]
            frac = (v / max_v) if max_v > 0 else 0.0
            eighths = int(round(frac * plot_h * 8))
            is_green = float(c.get("close", 0.0)) >= float(c.get("open", 0.0))
            bar_style = "bold green" if is_green else "bold red"
            for y in range(plot_h):
                cell_from_bottom = plot_h - 1 - y
                filled = max(0, min(8, eighths - cell_from_bottom * 8))
                grid[y][x] = levels[filled]
                if filled > 0:
                    styles[y][x] = bar_style

        content = Text()
        content.append(header)

        for y in range(plot_h):
            label = self._fmt_volume(max_v) if y == 0 else ""
            content.append(f"{label:>8} ┤ ", style="dim white")
            for x in range(n_c):
                content.append(grid[y][x], style=styles[y][x])
            content.append("\n")

        # No trailing newline: it would render an extra blank row inside the panel and
        # the top strip is part of the fixed vertical budget.
        content.append(f"{'0':>8} ┴" + ("─" * n_c), style="dim white")

        return Panel(content, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan", padding=(0, 1))

    def _render_chart_panel(self, candles: List[Dict[str, Any]], title: str) -> Panel:
        """Render a terminal-native ASCII/Unicode mini candlestick chart inside a Panel."""
        if not candles:
            txt = Text("Waiting for market candle data...", style="dim white")
            return Panel(txt, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan")

        # Take last ~35 candles for readable width
        c_sub = candles[-35:]
        n_c = len(c_sub)
        closes = [c["close"] for c in c_sub]
        highs = [c["high"] for c in c_sub]
        lows = [c["low"] for c in c_sub]

        min_p = min(lows)
        max_p = max(highs)
        p_range = max_p - min_p if max_p > min_p else 1.0
        latest_c = closes[-1]
        first_c = closes[0]
        chg_pct = ((latest_c - first_c) / first_c * 100.0) if first_c > 0 else 0.0
        chg_style = "bold green" if chg_pct >= 0 else "bold red"

        # Chart Header Summary line
        header = Text()
        header.append(f"H: ${max_p:,.2f}  │  L: ${min_p:,.2f}  │  C: ${latest_c:,.2f} (", style="dim white")
        header.append(f"{chg_pct:+.2f}%", style=chg_style)
        header.append(")\n", style="dim white")

        # Render 4-row height mini candle matrix
        plot_h = 2
        def p_to_y(p: float) -> int:
            y = int(((max_p - p) / p_range) * (plot_h - 1))
            return max(0, min(plot_h - 1, y))

        grid = [[" " for _ in range(n_c)] for _ in range(plot_h)]
        styles = [["dim white" for _ in range(n_c)] for _ in range(plot_h)]

        for x, c in enumerate(c_sub):
            h_y = p_to_y(c["high"])
            l_y = p_to_y(c["low"])
            o_y = p_to_y(c["open"])
            c_y = p_to_y(c["close"])

            is_green = c["close"] >= c["open"]
            c_style = "bold green" if is_green else "bold red"
            top_y = min(o_y, c_y)
            bot_y = max(o_y, c_y)

            # Wicks
            for y in range(h_y, l_y + 1):
                grid[y][x] = "│"
                styles[y][x] = c_style

            # Bodies
            for y in range(top_y, bot_y + 1):
                grid[y][x] = "█"
                styles[y][x] = c_style

            if top_y == bot_y:
                grid[top_y][x] = "▄" if is_green else "▀"
                styles[top_y][x] = c_style

        content = Text()
        content.append(header)

        for y in range(plot_h):
            p_val = max_p - (y * p_range / (plot_h - 1))
            if y == 0:
                content.append(f"${p_val:7,.2f} ┤ ", style="dim white")
            else:
                content.append("          │ ", style="dim white")

            for x in range(n_c):
                content.append(grid[y][x], style=styles[y][x])
            content.append("\n")

        content.append(f"${min_p:7,.2f} ┴" + ("─" * n_c) + "\n", style="dim white")

        return Panel(content, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan", padding=(0, 1))

    def render(self, state: Dict[str, Any]) -> Panel:
        """Render the dashboard sized to fill exactly one screen (never scrolls).

        Everything except the large chart is fixed-height, but that fixed height depends
        on terminal WIDTH (long lines wrap), so it cannot be a constant. We measure it
        once per width by laying the dashboard out at the minimum chart height, then give
        the chart all remaining rows. The measurement is cached, so the common path is a
        single layout pass.
        """
        term_h = self.console.height if (self.console and self.console.height) else 50
        term_w = self.console.width if (self.console and self.console.width) else 200

        # The chrome height depends on more than terminal width: several panels change
        # line count depending on what the engine is doing. Caching on width alone meant
        # the very first frame (no candles yet -> a 1-line "Waiting for data..." chart,
        # FLAT position, no progress bar) calibrated the budget far too low, and once
        # real candles arrived the dashboard overflowed and the bottom status row —
        # CPU/RAM/Disk/State — was cropped off screen. Re-measure whenever the SHAPE
        # changes, not just the width.
        shape = (
            term_w,
            term_h,
            bool(state.get("chart_candles")),                       # chart vs placeholder
            bool(state.get("progress_task")),                       # extra progress panel
            bool((state.get("market_trade") or {}).get("active_position")),  # 4-line vs 1-line
        )

        if self._chrome_shape != shape:
            try:
                self._chart_h_override = self.MIN_CHART_H
                probe_console = Console(width=term_w, height=term_h, file=io.StringIO())
                probe_console.print(self._build(state))
                lines = len(probe_console.file.getvalue().rstrip("\n").split("\n"))
                # +1 safety row: Live's viewport accounting is one line tighter than a
                # plain capture, and overshooting by one costs the whole bottom border.
                self._chrome_rows = max(0, lines - self.MIN_CHART_H) + 1
                self._chrome_shape = shape
            except Exception:
                # Measurement is an optimisation; fall back to the static estimate.
                self._chrome_rows = self.FIXED_CHROME

        # The chart is the only elastic element, so it must be the one that yields when
        # the terminal is short. Clamping at MIN_CHART_H would push the total past the
        # viewport and Rich would crop the bottom — losing the status footer, which is
        # exactly the failure we are preventing. Prefer MIN_CHART_H, but shrink below it
        # (down to a single row) rather than let the footer fall off screen.
        available = term_h - self._chrome_rows
        if available >= self.MIN_CHART_H:
            self._chart_h_override = min(self.MAX_CHART_H, available)
        else:
            # 2 is the floor: the chart's price axis interpolates over (plot_h - 1).
            self._chart_h_override = max(2, available)
        return self._build(state)

    def render_viewport(self, state: Dict[str, Any], offset: int = 0):
        """Render the dashboard and return the slice of it that fits the terminal.

        Returns (renderable, total_lines, max_offset, clamped_offset).

        This is what makes a too-small window usable WITHOUT flooding the scrollback.
        Letting the terminal scroll means every refresh appends another full copy of the
        dashboard, so scrolling up just walks back through thousands of duplicates. Here
        the dashboard is rendered once to lines, and only a window into it is handed to
        Live — so the display stays pinned and one screen tall, and scrolling moves the
        window rather than emitting more output.
        """
        panel = self.render(state)
        view_h = self.console.height or 50
        width = self.console.width or 200
        options = self.console.options.update(height=None)
        lines = self.console.render_lines(panel, options, pad=True)

        total = len(lines)
        if total <= view_h:
            segments: List[Segment] = []
            for i, line in enumerate(lines):
                segments.extend(line)
                if i != len(lines) - 1:
                    segments.append(Segment.line())
            return Segments(segments), total, 0, 0

        # Reserve the bottom row for a pinned scroll bar. Putting the indicator in the
        # dashboard footer would only reveal it once already scrolled to the bottom —
        # useless for telling the user that scrolling is possible in the first place.
        avail = max(1, view_h - 1)
        max_offset = max(0, total - avail)
        off = max(0, min(int(offset), max_offset))

        segments = []
        for line in lines[off:off + avail]:
            segments.extend(line)
            segments.append(Segment.line())

        shown_to = min(total, off + avail)
        at = "TOP" if off == 0 else ("END" if off >= max_offset else f"{off + 1}")
        label = f" ↕ rows {off + 1}-{shown_to} of {total} [{at}]   ↑↓ PgUp/PgDn Home/End "
        label = label[:width].ljust(width)
        segments.append(Segment(label, self.console.get_style("bold white on dark_blue")))
        return Segments(segments), total, max_offset, off

    def measure(self, state: Dict[str, Any]) -> int:
        """Rendered height in lines for the current terminal width, without printing."""
        term_w = self.console.width if (self.console and self.console.width) else 200
        term_h = self.console.height if (self.console and self.console.height) else 50
        probe = Console(width=term_w, height=term_h, file=io.StringIO())
        probe.print(self.render(state))
        return len(probe.file.getvalue().rstrip("\n").split("\n"))

    def fits_terminal(self, state: Dict[str, Any], margin: int = 0) -> bool:
        """True when the fully rendered dashboard fits the terminal viewport.

        The caller uses this to decide between a pinned full-screen display and a
        normal-buffer display that the terminal can scroll. The chart already shrinks to
        absorb spare rows, so a False here means the fixed panels alone exceed the
        window and something WOULD be cropped.
        """
        term_h = self.console.height if (self.console and self.console.height) else 50
        try:
            return self.measure(state) + margin <= term_h
        except Exception:
            return True     # measurement is advisory; never block the session on it

    def _build(self, state: Dict[str, Any]) -> Panel:
        top = state.get("top_bar", {})
        candles = state.get("chart_candles", [])
        mkt_trd = state.get("market_trade", {})
        recent_trades = state.get("recent_trades", [])
        acc = state.get("account", {})
        perf = state.get("performance", {})
        bot = state.get("bottom_status", {})
        prog_task = state.get("progress_task")

        # -------------------------------------------------------------
        # 1. Top Bar Header
        # -------------------------------------------------------------
        raw_conn = top.get("connection", "CONNECTED")
        eng_stat = top.get("engine_state", "LIVE")

        if raw_conn == "CONNECTED" and eng_stat == "LIVE":
            conn_str = "● CONNECTED | Engine: LIVE"
            conn_style = "bold green"
        elif raw_conn in ["RECONNECTING", "PAUSED", "BOOT"]:
            conn_str = f"● {raw_conn} | Engine: {eng_stat}"
            conn_style = "bold yellow"
        else:
            conn_str = f"● {raw_conn} | Engine: {eng_stat}"
            conn_style = "bold red"

        top_text = Text()
        top_text.append(f"{top.get('ist_now', '2026-08-13 22:35:00 IST')}  │  ", style="bold cyan")
        top_text.append(f"{top.get('symbol', 'SYMBOL')}  │  {top.get('timeframe', 'TF')}  │  {top.get('mode', 'PAPER')}  │  ", style="bold white")
        top_text.append(f"{conn_str}", style=conn_style)
        
        lat = float(top.get('latency_ms', 12.0))
        if lat < 100:
            lat_style = "bold green"
        elif lat <= 300:
            lat_style = "bold yellow"
        elif lat < 999:
            lat_style = "bold red"
        else:
            lat_style = "bold bright_red"
            
        top_text.append("  │  Latency: ", style="dim white")
        top_text.append(f"{lat:.1f} ms", style=lat_style)

        # -------------------------------------------------------------
        # 2. Top Section: 2 Side-by-Side Realtime Charts
        # -------------------------------------------------------------
        chart_grid = Table.grid(expand=True, padding=(0, 1))
        chart_grid.add_column(ratio=1)
        chart_grid.add_column(ratio=1)

        tf = top.get("timeframe", "3h")
        readiness_dict = state.get("readiness", {})
        chart_a = self._render_readiness_panel(readiness_dict)
        chart_b = self._render_volume_panel(candles, f"Chart B: {tf} Volume")

        chart_grid.add_row(chart_a, chart_b)

        # -------------------------------------------------------------
        # 3. Main Body: 2-Column Split Grid
        # -------------------------------------------------------------
        main_grid = Table.grid(expand=True, padding=(0, 1))
        main_grid.add_column(ratio=1, justify="left")
        main_grid.add_column(ratio=1, justify="left")

        # ================= LEFT COLUMN =================
        left_box_grid = Table.grid(expand=True, padding=(0, 0))
        left_box_grid.add_column(ratio=1)

        # --- Left Box 1: Market + Trade ---
        left_table = Table.grid(padding=(0, 1))
        left_table.add_column(style="dim white", justify="left", min_width=18)
        left_table.add_column(style="bold white", justify="left")

        c_price = mkt_trd.get("current_price", 0.0)
        bid = mkt_trd.get("bid_price", c_price * 0.9999)
        ask = mkt_trd.get("ask_price", c_price * 1.0001)
        chg_24h = mkt_trd.get("price_change_pct_24h", 0.0)
        chg_style = "bold green" if chg_24h >= 0 else "bold red"

        vol_stat = mkt_trd.get("volume_status_24h", "NORMAL")
        vol_style = "bold green" if vol_stat == "HIGH" else ("bold yellow" if vol_stat == "LOW" else "white")

        sig = mkt_trd.get("signal", "WAIT")
        sig_style = "bold green" if sig == "BUY" else ("b# line ~60old red" if sig == "SELL" else "white")

        pos = mkt_trd.get("active_position")
        if pos and isinstance(pos, dict):
            side = pos.get("side", "LONG")
            side_style = "bold green" if side == "LONG" else "bold red"
            entry_p = pos.get("entry_price", c_price)
            curr_p = pos.get("current_price", c_price)
            qty = pos.get("quantity", 0.0)
            notional = pos.get("notional", 0.0)
            lev = pos.get("leverage", 3.5)
            exp_pct = pos.get("exposure_pct", 0.0)
            sl_p = pos.get("sl_price", 0.0)
            tp_p = pos.get("tp_price", 0.0)
            pnl_val = pos.get("pnl", 0.0)
            pnl_pct_val = pos.get("pnl_pct", 0.0)
            pnl_style = "bold green" if pnl_val >= 0 else "bold red"

            pos_text = Text()
            pos_text.append(f"{side}", style=side_style)
            pos_text.append(f" | Notional: ${notional:,.2f} | {qty:.4f} Units | {lev:.1f}x | {exp_pct:.1f}% exp\n")
            pos_text.append(f"Entry: ${entry_p:,.2f} | Current: ${curr_p:,.2f}\n", style="white")
            pos_text.append(f"SL: ${sl_p:,.2f} | TP: ${tp_p:,.2f} | PnL: ", style="white")
            pos_text.append(f"${pnl_val:+,.2f} ({pnl_pct_val:+.2f}%)\n", style=pnl_style)
            pos_text.append(f"Duration: {pos.get('duration_bars', 0)} bars ({pos.get('duration_time', '0m')})", style="dim white")
        else:
            pos_text = Text(f"FLAT (Monitoring {tf} candle closures)", style="white")

        curr_pnl = mkt_trd.get("current_pnl", 0.0)
        curr_pnl_pct = mkt_trd.get("current_pnl_pct", 0.0)
        curr_pnl_style = "bold green" if curr_pnl >= 0 else "bold red"

        left_table.add_row("Current Price", f"${c_price:,.2f}")
        left_table.add_row("Bid / Ask", f"${bid:,.2f} / ${ask:,.2f}")
        left_table.add_row("24h Change", Text(f"{chg_24h:+.2f}%", style=chg_style))
        left_table.add_row("24h Vol Status", Text(f"{vol_stat}", style=vol_style))
        left_table.add_row("Signal", Text(f"{sig}", style=sig_style))
        left_table.add_row("Active Position", pos_text)
        left_table.add_row("Current Trade PnL", Text(f"${curr_pnl:+,.2f} ({curr_pnl_pct:+.2f}%)", style=curr_pnl_style))

        mkt_panel = Panel(left_table, title="[bold cyan]Market + Trade[/bold cyan]", border_style="cyan", expand=True)

        # --- Left Box 2: Recent Trade History (3 Most Recent Completed Trades) ---
        # SIMPLE_HEAD drops the table's own outer box (the panel already draws a border),
        # saving two rows of the fixed vertical budget without losing any data.
        history_table = Table(show_header=True, header_style="bold magenta", expand=True,
                              padding=(0, 1), box=box.SIMPLE_HEAD)
        history_table.add_column("#", justify="center", width=3)
        history_table.add_column("Side", justify="center", width=6)
        history_table.add_column("Entry IST", justify="center")
        history_table.add_column("Exit IST", justify="center")
        history_table.add_column("Entry Px", justify="right")
        history_table.add_column("Exit Px", justify="right")
        history_table.add_column("Size", justify="right")
        history_table.add_column("Net PnL", justify="right")

        max_hist = 3
        displayed_trades = recent_trades[-max_hist:] if recent_trades else []
        for t in displayed_trades:
            side_color = "bold green" if t["side"] == "LONG" else "bold red"
            pnl_color = "bold green" if t["net_pnl"] >= 0 else "bold red"
            history_table.add_row(
                f"#{t['trade_id']}",
                f"[{side_color}]{t['side']}[/{side_color}]",
                t["entry_time_ist"],
                t["exit_time_ist"],
                f"${t['entry_price']:,.2f}",
                f"${t['exit_price']:,.2f}",
                f"${self._usd_size(t):,.2f}",
                f"[{pnl_color}]${t['net_pnl']:+,.2f}[/{pnl_color}]"
            )

        # Pad table to a stable row count for consistent panel proportions
        padded_rows_needed = max_hist - len(displayed_trades)
        for i in range(padded_rows_needed):
            if i == 0 and not displayed_trades:
                history_table.add_row("-", "FLAT", "N/A", "N/A", "$0.00", "$0.00", "$0.00", "$0.00")
            else:
                history_table.add_row("-", "-", "-", "-", "-", "-", "-", "-")

        history_panel = Panel(history_table, title="[bold magenta]Recent Trade History[/bold magenta]", border_style="magenta", expand=True, padding=(0, 1))

        left_box_grid.add_row(mkt_panel)
        left_box_grid.add_row(history_panel)

        # ================= RIGHT COLUMN =================
        right_box_grid = Table.grid(expand=True, padding=(0, 0))
        right_box_grid.add_column(ratio=1)

        # --- Right Box 1: Account ---
        acc_table = Table.grid(padding=(0, 1))
        acc_table.add_column(style="dim white", justify="left", min_width=20)
        acc_table.add_column(style="bold white", justify="left")

        bal = acc.get("balance", 10000.0)
        eq = acc.get("equity", 10000.0)
        net_pnl = acc.get("net_pnl", 0.0)
        net_pnl_pct = acc.get("net_pnl_pct", 0.0)
        net_pnl_style = "bold green" if net_pnl >= 0 else "bold red"

        acc_table.add_row("Balance / Equity", f"${bal:,.2f} / ${eq:,.2f}")
        acc_table.add_row("Overall Net PnL", Text(f"${net_pnl:+,.2f} ({net_pnl_pct:+.2f}%)", style=net_pnl_style))
        acc_table.add_row("Session Trades", f"{acc.get('session_trades', 0)}")
        acc_table.add_row("Experiment Trades", f"{acc.get('total_trades', 0)}")
        acc_table.add_row("Wins / Losses", f"{acc.get('wins', 0)} / {acc.get('losses', 0)}")
        acc_table.add_row("Fees", f"${acc.get('fees', 0.0):,.2f}")
        acc_table.add_row("Uptime", f"{acc.get('uptime', '0d 00h 00m')}")
        acc_table.add_row("App Start IST", f"{acc.get('app_start_ist', 'N/A')}")

        acc_panel = Panel(acc_table, title="[bold green]Account[/bold green]", border_style="green", expand=True)

        # --- Right Box 2: Performance ---
        perf_table = Table.grid(padding=(0, 1))
        perf_table.add_column(style="dim white", justify="left", min_width=20)
        perf_table.add_column(style="bold white", justify="left")

        perf_table.add_row("Win Rate", f"{perf.get('win_rate_pct', 0.0):.2f}%")
        perf_table.add_row("Profit Factor", f"{perf.get('profit_factor', 0.0):.2f}")
        sharpe_val = perf.get('sharpe_ratio')
        perf_table.add_row("Sharpe Ratio", f"{sharpe_val:.2f}" if sharpe_val is not None else "N/A")
        perf_table.add_row("Max Drawdown", f"{perf.get('max_drawdown_pct', 0.0):.2f}%")
        perf_table.add_row("Current Leverage", f"{perf.get('leverage', 1.0):.1f}x")
        perf_table.add_row("Current Exposure", f"{perf.get('exposure_pct', 0.0):.1f}%")

        perf_panel = Panel(perf_table, title="[bold blue]Performance[/bold blue]", border_style="blue", expand=True)

        right_box_grid.add_row(acc_panel)
        right_box_grid.add_row(perf_panel)

        main_grid.add_row(left_box_grid, right_box_grid)

        # -------------------------------------------------------------
        # 4. Master Layout Assembly
        # -------------------------------------------------------------
        content_table = Table.grid(expand=True)
        content_table.add_column(justify="left")
        content_table.add_row(chart_grid)
        content_table.add_row(main_grid)

        # --- 4b. Full-Width Large Candlestick Chart ---
        large_chart = self._render_large_candlestick_chart(candles, f"Live Chart • {tf}")
        content_table.add_row(large_chart)

        if prog_task:
            curr = prog_task.get("current", 0)
            tot = prog_task.get("total", 100)
            pct = prog_task.get("pct", 0.0)
            speed = prog_task.get("speed", "0/s")
            eta = prog_task.get("eta", "0s")
            prog_text = Text()
            prog_text.append(f"Downloading {curr}/{tot}  │  {pct:.0f}%  │  {speed}  │  ETA {eta}", style="bold yellow")
            prog_panel = Panel(prog_text, border_style="yellow", expand=True, padding=(0, 1))
            content_table.add_row(prog_panel)

        # -------------------------------------------------------------
        # 5. Bottom Status Row Footer
        # -------------------------------------------------------------
        # The footer is a Panel subtitle, which Rich truncates rather than wraps. A narrow
        # window therefore used to silently amputate the tail of the line — Temp and State
        # vanished entirely at 100 columns. Build it from prioritised segments instead and
        # drop the least important ones until it fits, so the fields that matter survive.
        cpu_str = bot.get('cpu_usage_str') or "n/a"
        ram_str = bot.get('ram_usage_str') or "n/a"
        disk_str = bot.get('disk_usage_str') or "n/a"

        # CPU temperature, colour-graded so a thermal problem is visible at a glance.
        temp_c = bot.get('cpu_temp_c')
        temp_str = bot.get('cpu_temp_str') or "n/a"
        if temp_c is None:
            temp_style = "dim white"
        elif temp_c >= 85.0:
            temp_style = "bold red"
        elif temp_c >= 70.0:
            temp_style = "bold yellow"
        else:
            temp_style = "bold green"

        # (priority, long form, short form, style). Lower priority = dropped last.
        segments = [
            (7, f"Feed: {bot.get('feed_speed', '0 B/s')}", None, "dim white"),
            (6, f"Last Update: {bot.get('last_market_update', 'N/A')}",
                f"Upd: {bot.get('last_market_update', 'N/A')}", "dim white"),
            (5, f"Reconnects: {bot.get('reconnect_count', 0)}",
                f"RC: {bot.get('reconnect_count', 0)}", "dim white"),
            (2, f"CPU: {cpu_str}", f"C {cpu_str}", "dim white"),
            (3, f"RAM: {ram_str}", f"R {ram_str}", "dim white"),
            (4, f"Disk: {disk_str}", f"D {disk_str}", "dim white"),
            (1, f"Temp: {temp_str}", f"T {temp_str}", temp_style),
            (0, f"State: {bot.get('state_save_status', 'SAVED')}",
                f"{bot.get('state_save_status', 'SAVED')}", "bold green"),
        ]


        sep = "  │  "
        term_w = self.console.width if (self.console and self.console.width) else 200
        budget = max(20, term_w - 10)      # panel border + corner padding

        def _width(items, short):
            return sum(len(s[2] if (short and s[2]) else s[1]) for s in items) + len(sep) * max(0, len(items) - 1)

        chosen, use_short = list(segments), False
        if _width(chosen, False) > budget:
            use_short = True                                   # 1) abbreviate labels
            while len(chosen) > 1 and _width(chosen, True) > budget:
                worst = max(chosen, key=lambda s: s[0])         # 2) drop lowest priority
                chosen.remove(worst)

        bot_text = Text()
        for i, seg in enumerate(chosen):
            if i:
                bot_text.append(sep, style="dim white")
            bot_text.append(seg[2] if (use_short and seg[2]) else seg[1], style=seg[3])

        return Panel(
            content_table,
            title=top_text,
            subtitle=bot_text,
            border_style="magenta",
            expand=True
        )

    def _render_large_candlestick_chart(self, candles: List[Dict[str, Any]], title: str) -> Panel:
        """Render a large full-width terminal candlestick chart with wicks, EMA overlays and Y/X axes.

        Candlesticks only — volume is shown separately in Chart B.
        """
        if not candles:
            txt = Text("Waiting for market candle data...", style="dim white")
            return Panel(txt, title=f"[bold magenta]{title}[/bold magenta]", border_style="magenta")

        # Dynamically calculate chart canvas width based on actual terminal width
        term_width = self.console.width if (self.console and self.console.width) else 120
        # Account for Y-axis label (~12 chars), right price badges (~14 chars), and padding/borders (4 chars)
        target_w = max(40, term_width - 30)
        canvas_w = min(len(candles), target_w)
        c_draw = candles[-canvas_w:]

        latest_c = c_draw[-1]
        open_val = latest_c["open"]
        high_val = latest_c["high"]
        low_val = latest_c["low"]
        close_val = latest_c["close"]
        vol_val = latest_c["volume"]
        first_c = c_draw[0]["close"]
        chg_pct = ((close_val - first_c) / first_c * 100.0) if first_c > 0 else 0.0
        chg_style = "bold green" if chg_pct >= 0 else "bold red"

        # Header Summary line
        header = Text()
        header.append(f"O: ${open_val:,.2f}  H: ${high_val:,.2f}  L: ${low_val:,.2f}  C: ${close_val:,.2f} (", style="dim white")
        header.append(f"{chg_pct:+.2f}%", style=chg_style)
        header.append(f")  Vol: {vol_val/1000:.2f}K\n", style="dim white")

        # Scale Price Range
        all_highs = [c["high"] for c in c_draw]
        all_lows = [c["low"] for c in c_draw]
        all_ema51 = [c["ema_51"] for c in c_draw if c.get("ema_51")]
        all_ema200 = [c["ema_200"] for c in c_draw if c.get("ema_200")]

        max_p = max(all_highs + all_ema51 + all_ema200) if (all_highs + all_ema51 + all_ema200) else close_val * 1.05
        min_p = min(all_lows + all_ema51 + all_ema200) if (all_lows + all_ema51 + all_ema200) else close_val * 0.95
        if max_p <= min_p:
            max_p = min_p + 1.0
        p_range = max_p - min_p

        # Main plot height adapts to the terminal so the whole dashboard fits on one
        # screen without scrolling. Everything above/below this chart is fixed-height
        # (FIXED_CHROME lines); the chart absorbs whatever vertical space is left.
        if self._chart_h_override is not None:
            plot_h = self._chart_h_override
        else:
            term_height = self.console.height if (self.console and self.console.height) else 50
            plot_h = max(self.MIN_CHART_H, min(self.MAX_CHART_H, term_height - self.FIXED_CHROME))
        # The price axis interpolates over (plot_h - 1); a 1-row chart would divide by zero.
        plot_h = max(2, plot_h)

        def price_to_y(p: float) -> int:
            y = int(((max_p - p) / p_range) * (plot_h - 1))
            return max(0, min(plot_h - 1, y))

        # Build 2D character matrix spanning full canvas_w width
        grid = [[("─" if y in (0, plot_h // 4, plot_h // 2, (3 * plot_h) // 4, plot_h - 1) else " ", "dim gray") for _ in range(canvas_w)] for y in range(plot_h)]

        # EMA 51 Layer
        for x, c in enumerate(c_draw):
            e51 = c.get("ema_51")
            if e51 and e51 > 0:
                ey = price_to_y(e51)
                grid[ey][x] = ("─", "bold cyan")

        # EMA 200 Layer
        for x, c in enumerate(c_draw):
            e200 = c.get("ema_200")
            if e200 and e200 > 0:
                ey = price_to_y(e200)
                grid[ey][x] = ("─", "bold yellow")

        # Candlestick Layer (Wicks + Bodies)
        for x, c in enumerate(c_draw):
            h_y = price_to_y(c["high"])
            l_y = price_to_y(c["low"])
            o_y = price_to_y(c["open"])
            c_y = price_to_y(c["close"])

            is_green = c["close"] >= c["open"]
            c_style = "bold green" if is_green else "bold red"
            top_y = min(o_y, c_y)
            bot_y = max(o_y, c_y)

            # Wick
            for y in range(h_y, l_y + 1):
                cur_ch, cur_st = grid[y][x]
                if cur_ch in (" ", "─"):
                    grid[y][x] = ("│", c_style)

            # Body
            for y in range(top_y, bot_y + 1):
                grid[y][x] = ("█", c_style)

            if top_y == bot_y:
                grid[top_y][x] = ("▄" if is_green else "▀", c_style)

        # Assemble Output
        content = Text()
        content.append(header)

        # EMA Legend
        curr_ema51 = latest_c.get("ema_51", close_val)
        curr_ema200 = latest_c.get("ema_200", close_val)
        leg = Text()
        leg.append("EMA 51 : ", style="dim white")
        leg.append(f"${curr_ema51:,.2f}  ", style="bold cyan")
        leg.append("EMA 200: ", style="dim white")
        leg.append(f"${curr_ema200:,.2f}\n", style="bold yellow")
        content.append(leg)

        # Exchange-aligned candle closure countdown calculation
        tf = title.split("•")[-1].strip() if "•" in title else "3h"
        interval_sec = resolution_to_seconds(tf)
        now_ts = int(time.time())
        rem_sec = max(0, interval_sec - (now_ts % interval_sec))

        if interval_sec >= 3600:
            hrs = rem_sec // 3600
            mins = (rem_sec % 3600) // 60
            secs = rem_sec % 60
            cd_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"
        else:
            mins = rem_sec // 60
            secs = rem_sec % 60
            cd_str = f"{mins:02d}:{secs:02d}"

        close_y = price_to_y(close_val)
        timer_y = close_y + 1 if close_y < plot_h - 1 else close_y

        y_prices = [max_p - (i * p_range / (plot_h - 1)) for i in range(plot_h)]

        for y in range(plot_h):
            p_val = y_prices[y]
            # Left Y-Axis
            if y == 0:
                lbl = f"${p_val:7,.2f} ┤ "
            elif y == plot_h // 2:
                lbl = f"${p_val:7,.2f} ┼ "
            elif y == plot_h - 1:
                lbl = f"${p_val:7,.2f} ┴ "
            else:
                lbl = "          │ "
            content.append(lbl, style="dim white")

            # Grid Columns across FULL canvas_w
            for x in range(canvas_w):
                ch, st = grid[y][x]
                content.append(ch, style=st)

            # Right Y-Axis Badges & Candle Closure Countdown Timer
            right_badge = ""
            b_style = ""
            if y == 0:
                right_badge = f" [{max_p:,.2f}]"
                b_style = "bold green"
            elif y == close_y:
                if timer_y == close_y:
                    right_badge = f" [{close_val:,.2f}] {cd_str}"
                else:
                    right_badge = f" [{close_val:,.2f}]"
                b_style = "bold white"
            elif y == timer_y:
                right_badge = f"   {cd_str}"
                b_style = "bold yellow"
            elif y == price_to_y(curr_ema51):
                right_badge = f" [{curr_ema51:,.2f}]"
                b_style = "bold cyan"
            elif y == price_to_y(curr_ema200):
                right_badge = f" [{curr_ema200:,.2f}]"
                b_style = "bold yellow"
            elif y == plot_h - 1:
                right_badge = f" [{min_p:,.2f}]"
                b_style = "bold red"

            if right_badge:
                content.append(right_badge, style=b_style)

            content.append("\n")

        # Bottom Axis Line across FULL canvas_w
        content.append("          └" + ("─" * canvas_w) + "\n", style="dim white")

        # X-Axis Time Labels across FULL canvas_w
        time_row = Text("            ", style="dim white")
        step = max(1, canvas_w // 6)
        for x in range(0, canvas_w, step):
            dt_raw = c_draw[x].get("datetime", "")
            if " " in dt_raw:
                dt_p = dt_raw.split(" ")
                lbl_str = f"{dt_p[0][-5:]} {dt_p[1][:5]}"
            else:
                lbl_str = f"T{x}"
            time_row.append(lbl_str.ljust(step), style="dim white")
        time_row.append("\n")
        content.append(time_row)

        # NOTE: the volume sub-chart that used to live here was removed deliberately.
        # Volume now has its own dedicated panel (Chart B), so repeating it under the
        # price chart cost two rows of vertical budget for no extra information.
        # This panel is candlesticks + EMAs only.

        return Panel(content, title=f"[bold magenta]{title}[/bold magenta]", border_style="magenta", expand=True, padding=(0, 1))

    def _render_readiness_panel(self, readiness: Dict[str, Any]) -> Panel:
        """Render the Setup Readiness progress bar panel."""
        if not readiness:
            txt = Text("Waiting for market data...", style="dim white")
            return Panel(txt, title="[bold cyan]Setup Readiness[/bold cyan]", border_style="cyan")
            
        buy_pct = readiness.get("buy_pct", 0)
        sell_pct = readiness.get("sell_pct", 0)
        bias = readiness.get("bias", "NEUTRAL")
        status = readiness.get("status", "WEAK")
        
        buy_bar = self._pct_bar(buy_pct)
        sell_bar = self._pct_bar(sell_pct)
        
        table = Table.grid(padding=(0, 2))
        table.add_column("Side", justify="left", style="bold")
        table.add_column("Bar", justify="left")
        table.add_column("Pct", justify="right", style="bold")
        
        buy_style = "bold green" if buy_pct > 0 else "dim white"
        sell_style = "bold red" if sell_pct > 0 else "dim white"
        
        if "ACTIVE" in status or status == "READY":
            buy_style = "bold bright_green" if bias == "BUY" else "dim white"
            sell_style = "bold bright_red" if bias == "SELL" else "dim white"
            
        table.add_row("BUY ", Text(buy_bar, style=buy_style), Text(f"{buy_pct}%", style=buy_style))
        table.add_row("SELL", Text(sell_bar, style=sell_style), Text(f"{sell_pct}%", style=sell_style))
        
        bias_style = "bold green" if bias == "BUY" else ("bold red" if bias == "SELL" else "dim white")
        if "ACTIVE" in status:
            status_style = "bold bright_green" if bias == "BUY" else "bold bright_red"
        elif status == "READY":
            status_style = bias_style
        elif status in ["DEVELOPING", "STRONG"]:
            status_style = "bold yellow"
        else:
            status_style = "bold white"
        
        footer = Text()
        footer.append(f"Bias: ", style="white")
        footer.append(f"{bias}\n", style=bias_style)
        footer.append(f"Status: ", style="white")
        footer.append(f"{status}", style=status_style)
        
        layout = Table.grid()
        layout.add_row(table)
        layout.add_row(footer)
        
        return Panel(layout, title="[bold cyan]Setup Readiness[/bold cyan]", border_style="cyan")
