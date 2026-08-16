"""Generate one Pine v5 strategy per OP-BB config.

Ports the user's base pine.pine to CURRENT validated Python behaviour:
  * Bollinger filter replaced with the Stage-1 Python filter (min bandwidth / expansion
    ratio vs bandwidth[lookback] / middle-band distance)
  * margin-based allocation cap (max_margin = equity*alloc; max_notional = margin*leverage)
  * risk budget from PLAIN equity, not leveraged equity
  * SL/TP recomputed from the REALIZED entry fill, matching BaselineRiskManager
  * quantity floored to quantity_step; SL/TP rounded to tick_size
  * invalid/inverted SL rejects the trade (no 1% substitute)
  * ADX retained as an inert toggle (default off) — not present in the Python strategy
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "pine")

MAP = [
    ("config1-ETHUSDTP15m.json", "config1-ETHUSDTP15m.pine",
     "ETHUSDT.P 15m — Candidate 158 OP-BB", "ETHUSDT.P 15m C158"),
]

TEMPLATE = r'''//@version=5
strategy("{title}", shorttitle="{short}", overlay=true, initial_capital={capital}, default_qty_type=strategy.cash, commission_type=strategy.commission.percent, commission_value={commission}, slippage={slippage}, process_orders_on_close=false, pyramiding=0, margin_long=0, margin_short=0)

// =============================================================================
// {title} — generated from configs/{cfgfile}
// =============================================================================
// Source        : {source}
// Development   : {dev_start} -> {dev_end}
// Unseen month  : {uns_start} -> {uns_end}
// Optimizer     : {arch}
// EMA / RSI     = {ema} / {rsi}          OB / OS = {ob} / {os}
// ATR           = {atr}
// Consolidation = {cons} candles / {cmult} ATR
// Swing         = {swing}      Volume SMA = {vsma}   Volume Mult = {vmult}
// RR            = {rr}
// Long ON / Short OFF
// Leverage {lev}x | Risk {risk}% | Allocation {alloc}% | Qty step {qstep} | Tick {tick}
// Bollinger     = len {bb_len} / std {bb_std} / minBW {bb_minbw}% / exp {bb_explb}-{bb_expratio} / midDist {bb_middist}
//
// Reference (Python, current engine):
//   Development  : {ref_dev_ret}% return, PF {ref_dev_pf}, DD {ref_dev_dd}%, {ref_dev_n} trades
//   Unseen OFF   : {ref_uoff_ret}% return, PF {ref_uoff_pf}, DD {ref_uoff_dd}%, {ref_uoff_n} trades
//   Unseen ON    : {ref_uon_ret}% return, PF {ref_uon_pf}, DD {ref_uon_dd}%, {ref_uon_n} trades
//
// IMPORTANT: run on BINANCE:ETHUSDT.P, 15m.
// =============================================================================

// =============================================================================
// 1. TECHNICAL INDICATORS
// =============================================================================
grp_ind = "1. Technical Indicators"
ema_len = input.int({ema}, "EMA Period", minval=5, group=grp_ind)
rsi_len = input.int({rsi}, "RSI Period", minval=2, group=grp_ind)
rsi_ob  = input.float({ob}, "RSI Overbought Threshold", minval=50.0, maxval=100.0, group=grp_ind)
rsi_os  = input.float({os}, "RSI Oversold Threshold", minval=0.0, maxval=50.0, group=grp_ind)
atr_len = input.int({atr}, "ATR Period", minval=1, group=grp_ind)

// =============================================================================
// 2. CONSOLIDATION & VOLUME FILTERS
// =============================================================================
grp_cons = "2. Consolidation & Volume Filters"
cons_len    = input.int({cons}, "Consolidation Lookback (Candles)", minval=3, group=grp_cons)
cons_mult   = input.float({cmult}, "Consolidation ATR Multiplier", minval=0.5, step=0.1, group=grp_cons)
use_vol     = input.bool(true, "Require Volume Breakout Filter", group=grp_cons)
vol_sma_len = input.int({vsma}, "Volume SMA Period", minval=1, group=grp_cons)
vol_mult    = input.float({vmult}, "Volume Multiplier vs SMA", minval=0.1, step=0.05, group=grp_cons)

// =============================================================================
// 3. RISK MANAGEMENT & POSITION SIZING  (mirrors BaselineRiskManager)
// =============================================================================
grp_risk = "3. Risk Management & Position Sizing"
enable_long  = input.bool(true,  "Enable Long Trades",  group=grp_risk)
enable_short = input.bool(false, "Enable Short Trades", group=grp_risk)
leverage  = input.float({lev}, "Leverage (x)", minval=1.0, maxval=100.0, step=0.5, group=grp_risk)
risk_pct  = input.float({risk}, "Account Risk % Per Trade (price risk, gross of fees)", minval=0.1, maxval=10.0, step=0.1, group=grp_risk)
max_alloc = input.float({alloc}, "Max Equity Committed As MARGIN %", minval=5.0, maxval=100.0, step=5.0, group=grp_risk)
rr_ratio  = input.float({rr}, "Risk-to-Reward Target (1 : X)", minval=0.5, step=0.1, group=grp_risk)
swing_len = input.int({swing}, "Swing High/Low Lookback", minval=2, group=grp_risk)
qty_step  = input.float({qstep}, "Instrument Quantity Step", minval=0.000001, step=0.001, group=grp_risk)
tick_size = input.float({tick}, "Instrument Tick Size", minval=0.000001, step=0.01, group=grp_risk)
min_qty   = input.float(0.001, "Minimum Position Size", minval=0.000001, step=0.001, group=grp_risk)

// =============================================================================
// 4. VISUAL DISPLAY
// =============================================================================
grp_ui = "4. Visual Display & Dashboard"
show_cons  = input.bool(true, "Highlight Consolidation Zones", group=grp_ui)
show_sl_tp = input.bool(true, "Plot Active SL & TP Lines", group=grp_ui)
show_hud   = input.bool(true, "Show On-Chart Performance HUD Table", group=grp_ui)

// =============================================================================
// 5. BOLLINGER CHOP FILTER  (Phase 4 Stage 1 — matches src/filters/stage_1_bollinger)
// =============================================================================
// Signal gate only: never creates a signal, only blocks existing ones.
// Each threshold set to 0 disables that sub-condition.
grp_bb = "5. Bollinger Chop Filter"
bb_enabled  = input.bool({bb_enabled}, "Enable Bollinger Filter", group=grp_bb)
bb_len      = input.int({bb_len}, "Bollinger Length", minval=1, group=grp_bb)
bb_std      = input.float({bb_std}, "Bollinger StdDev", minval=0.1, step=0.1, group=grp_bb)
bb_min_bw   = input.float({bb_minbw}, "Min Bandwidth % (0 = off)", minval=0.0, step=0.1, group=grp_bb)
bb_exp_lb   = input.int({bb_explb}, "Expansion Lookback", minval=1, group=grp_bb)
bb_exp_ratio= input.float({bb_expratio}, "Expansion Min Ratio (0 = off)", minval=0.0, step=0.05, group=grp_bb)
bb_mid_dist = input.float({bb_middist}, "Min Middle-Band Distance (0 = off)", minval=0.0, step=0.01, group=grp_bb)

// =============================================================================
// 6. ADX FILTER  (not part of the Python strategy — inert unless enabled)
// =============================================================================
grp_adx = "6. ADX Filter (not in Python strategy)"
adx_enabled   = input.bool(false, "Enable ADX Filter", group=grp_adx)
adx_len       = input.int(12, "ADX Length", minval=1, group=grp_adx)
adx_threshold = input.float(15.0, "ADX Threshold", minval=1.0, step=1.0, group=grp_adx)
adx_rising    = input.bool(false, "Require Rising ADX", group=grp_adx)

// =============================================================================
// INDICATOR CALCULATIONS
// =============================================================================
ema_v   = ta.ema(close, ema_len)
rsi_v   = ta.rsi(close, rsi_len)
atr_v   = ta.atr(atr_len)
vol_sma = ta.sma(volume, vol_sma_len)

// --- Bollinger (population stddev, matching pandas .std(ddof=0)) ---
bb_basis = ta.sma(close, bb_len)
bb_dev   = ta.stdev(close, bb_len)
bb_upper = bb_basis + bb_std * bb_dev
bb_lower = bb_basis - bb_std * bb_dev
bb_wid   = bb_upper - bb_lower
bb_bandwidth = bb_basis == 0 ? na : (bb_wid / bb_basis) * 100.0
bb_middist_v = bb_wid == 0 ? na : math.abs(close - bb_basis) / bb_wid

// Each condition blocks only when its threshold > 0 AND the value is defined.
// Undefined (warmup) values never block — identical to the Python allow_mask().
bb_block_bw  = bb_min_bw    > 0.0 and not na(bb_bandwidth) and bb_bandwidth < bb_min_bw
bb_prev      = bb_bandwidth[bb_exp_lb]
bb_ratio     = (not na(bb_prev) and bb_prev > 0 and not na(bb_bandwidth)) ? bb_bandwidth / bb_prev : na
bb_block_exp = bb_exp_ratio > 0.0 and not na(bb_ratio) and bb_ratio < bb_exp_ratio
bb_block_mid = bb_mid_dist  > 0.0 and not na(bb_middist_v) and bb_middist_v < bb_mid_dist
bb_ok = not bb_enabled or not (bb_block_bw or bb_block_exp or bb_block_mid)

// --- ADX (inert by default) ---
[_dip, _dim, adx_val] = ta.dmi(adx_len, adx_len)
adx_ok = not adx_enabled or (adx_val >= adx_threshold and (not adx_rising or adx_val > adx_val[1]))

// =============================================================================
// CONSOLIDATION / SWING / RSI CONTEXT / VOLUME
// =============================================================================
cons_high = ta.highest(high, cons_len)
cons_low  = ta.lowest(low, cons_len)
is_consolidating = (cons_high - cons_low) <= (atr_v * cons_mult)
prior_consolidation = math.max(is_consolidating[1] ? 1 : 0, is_consolidating[2] ? 1 : 0, is_consolidating[3] ? 1 : 0) == 1

swing_low  = ta.lowest(low[1], swing_len)
swing_high = ta.highest(high[1], swing_len)

rsi_was_oversold   = ta.lowest(rsi_v, 6)  <= rsi_os
rsi_was_overbought = ta.highest(rsi_v, 6) >= rsi_ob
vol_confirmed = not use_vol or (volume >= vol_sma * vol_mult)

// =============================================================================
// ENTRY SIGNAL LOGIC  (unchanged from the frozen Python strategy)
// =============================================================================
ema_cross_up   = (close[1] <= ema_v[1] and close > ema_v) or (close > ema_v and close[1] > ema_v[1] and open < ema_v)
ema_cross_down = (close[1] >= ema_v[1] and close < ema_v) or (close < ema_v and close[1] < ema_v[1] and open > ema_v)
rsi_long_valid  = (rsi_v < rsi_ob) and (rsi_v >= 40.0 or rsi_was_oversold)
rsi_short_valid = (rsi_v > rsi_os) and (rsi_v <= 60.0 or rsi_was_overbought)
cons_valid = prior_consolidation or is_consolidating

long_signal  = enable_long  and ema_cross_up   and rsi_long_valid  and cons_valid and vol_confirmed and bb_ok and adx_ok
short_signal = enable_short and ema_cross_down and rsi_short_valid and cons_valid and vol_confirmed and bb_ok and adx_ok

// =============================================================================
// POSITION SIZING — mirrors BaselineRiskManager.calculate_position()
//   risk budget uses PLAIN equity (not leveraged)
//   max_margin   = equity * alloc%          max_notional = max_margin * leverage
//   qty floored to qty_step; invalid/inverted SL rejects the trade
// =============================================================================
floor_step(v, s) => s <= 0 ? v : math.floor(v / s + 1e-9) * s
round_tick(p, t) => t <= 0 ? p : math.round(p / t) * t

var float entry_sl  = na
var float entry_tp  = na
var float pend_sl   = na
var int   pend_side = 0
var int   pend_bar  = na

// --- BAR N (signal bar) -------------------------------------------------------
// Validate the stop and SUBMIT the entry here. With process_orders_on_close=false
// an order submitted during bar N is filled at the OPEN OF BAR N+1, which is the
// fill convention the Python engine uses. `pend_bar` records the submitting bar so
// the fill-dependent maths below cannot run until the fill actually exists.
//
// The SL is taken from the signal bar (swing/low/ATR of bar N) — unchanged, this is
// exactly what BaselineStrategy does. Only fill-dependent values move to bar N+1.
//
// Quantity must be decided here because the order is submitted here; Pine cannot see
// open(N+1) before submitting. `close` of bar N is used as the fill proxy for sizing.
if strategy.position_size == 0 and pend_side == 0
    if long_signal
        raw_sl = math.min(swing_low, low)
        if (close - raw_sl) < (0.4 * atr_v)
            raw_sl := close - (0.85 * atr_v)
        if (close - raw_sl) > 0
            sl_q  = round_tick(raw_sl, tick_size)
            rd_q  = close - sl_q
            eq    = strategy.equity
            qty_q = floor_step(math.min((eq * (risk_pct / 100.0)) / rd_q, (eq * (max_alloc / 100.0) * leverage) / close), qty_step)
            if qty_q >= min_qty and ((qty_q * close) / leverage) <= math.min(eq, eq * (max_alloc / 100.0))
                pend_sl   := sl_q
                pend_side := 1
                pend_bar  := bar_index
                strategy.entry("Long", strategy.long, qty=qty_q)
    else if short_signal
        raw_sl = math.max(swing_high, high)
        if (raw_sl - close) < (0.4 * atr_v)
            raw_sl := close + (0.85 * atr_v)
        if (raw_sl - close) > 0
            sl_q  = round_tick(raw_sl, tick_size)
            rd_q  = sl_q - close
            eq    = strategy.equity
            qty_q = floor_step(math.min((eq * (risk_pct / 100.0)) / rd_q, (eq * (max_alloc / 100.0) * leverage) / close), qty_step)
            if qty_q >= min_qty and ((qty_q * close) / leverage) <= math.min(eq, eq * (max_alloc / 100.0))
                pend_sl   := sl_q
                pend_side := -1
                pend_bar  := bar_index
                strategy.entry("Short", strategy.short, qty=qty_q)

// --- BAR N+1 (entry bar) ------------------------------------------------------
// Gated on bar_index > pend_bar so this never runs on the signal bar. The position
// is now filled, so strategy.position_avg_price IS the realized fill (open of this
// bar plus the broker's 1-tick slippage) — the same value the Python risk manager
// receives as `entry_price`. risk_dist and TP are computed from it.
if pend_side != 0 and not na(pend_bar) and bar_index > pend_bar
    if strategy.position_size != 0
        fill = strategy.position_avg_price
        risk_dist = pend_side == 1 ? (fill - pend_sl) : (pend_sl - fill)
        if risk_dist > 0
            entry_sl := pend_sl
            entry_tp := round_tick(pend_side == 1 ? fill + rr_ratio * risk_dist : fill - rr_ratio * risk_dist, tick_size)
            if pend_side == 1
                strategy.exit("Exit Long", from_entry="Long", stop=entry_sl, limit=entry_tp)
            else
                strategy.exit("Exit Short", from_entry="Short", stop=entry_sl, limit=entry_tp)
        else
            // Fill gapped through the stop — no valid risk distance; flatten immediately.
            strategy.close_all("Invalid Risk")
    pend_side := 0
    pend_sl   := na
    pend_bar  := na

if strategy.position_size == 0 and pend_side == 0
    entry_sl := na
    entry_tp := na

// =============================================================================
// CHART VISUALIZATION
// =============================================================================
ema_color = close >= ema_v ? color.new(#10b981, 0) : color.new(#ef4444, 0)
plot(ema_v, "EMA", color=ema_color, linewidth=2)
plot(bb_enabled ? bb_upper : na, "BB Upper", color=color.new(#64748b, 40))
plot(bb_enabled ? bb_basis : na, "BB Basis", color=color.new(#64748b, 20))
plot(bb_enabled ? bb_lower : na, "BB Lower", color=color.new(#64748b, 40))
bgcolor(show_cons and is_consolidating ? color.new(#3b82f6, 92) : na, title="Consolidation Zone")
bgcolor(bb_enabled and not bb_ok ? color.new(#f59e0b, 90) : na, title="Bollinger Chop Block")
plot(show_sl_tp and strategy.position_size != 0 ? entry_sl : na, "Stop Loss",   color=color.new(#ef4444, 0), style=plot.style_linebr, linewidth=2)
plot(show_sl_tp and strategy.position_size != 0 ? entry_tp : na, "Take Profit", color=color.new(#10b981, 0), style=plot.style_linebr, linewidth=2)
plotshape(long_signal  and strategy.position_size == 0, title="Long Signal",  location=location.belowbar, color=color.new(#10b981, 0), style=shape.triangleup,   size=size.small, text="LONG")
plotshape(short_signal and strategy.position_size == 0, title="Short Signal", location=location.abovebar, color=color.new(#ef4444, 0), style=shape.triangledown, size=size.small, text="SHORT")

// =============================================================================
// PERFORMANCE HUD
// =============================================================================
var table hud = table.new(position.top_right, 2, 8, bgcolor=color.new(#0f172a, 10), border_color=color.new(#334155, 0), border_width=1)
if show_hud and barstate.islast
    win_rate = strategy.closedtrades > 0 ? (strategy.wintrades / strategy.closedtrades) * 100.0 : 0.0
    net_pnl = strategy.netprofit
    pnl_color = net_pnl >= 0 ? #10b981 : #ef4444
    pos_str = strategy.position_size > 0 ? "LONG (" + str.tostring(strategy.position_size, "#.###") + " ETH)" : strategy.position_size < 0 ? "SHORT (" + str.tostring(math.abs(strategy.position_size), "#.###") + " ETH)" : "FLAT"
    table.cell(hud, 0, 0, "Config", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 0, "{short}", text_color=#38bdf8, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 1, "Net Profit ($)", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 1, "$" + str.tostring(net_pnl, "#,###.00"), text_color=pnl_color, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 2, "Win Rate (%)", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 2, str.tostring(win_rate, "#.1") + "%", text_color=#f8fafc, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 3, "Total Trades", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 3, str.tostring(strategy.closedtrades), text_color=#f8fafc, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 4, "Profit Factor", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 4, str.tostring(strategy.grossprofit / math.max(strategy.grossloss, 1e-6), "#.##"), text_color=#f8fafc, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 5, "Max Drawdown", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 5, "$" + str.tostring(strategy.max_drawdown, "#,###.00"), text_color=#f59e0b, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 6, "Bollinger Filter", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 6, bb_enabled ? "ON" : "OFF", text_color=bb_enabled ? #10b981 : #94a3b8, text_size=size.small, text_halign=text.align_right)
    table.cell(hud, 0, 7, "Active State", text_color=#94a3b8, text_size=size.small, text_halign=text.align_left)
    table.cell(hud, 1, 7, pos_str, text_color=#38bdf8, text_size=size.small, text_halign=text.align_right)
'''


def main():
    os.makedirs(OUT, exist_ok=True)
    for cfgfile, pinefile, title, short in MAP:
        d = json.load(open(os.path.join(ROOT, "configs", cfgfile)))
        s, r, b, e = d["strategy"], d["risk"], d["filters"]["bollinger"], d["execution"]
        m = d.get("_reference_metrics", {})
        uo, un = m.get("unseen_filter_off", {}), m.get("unseen_filter_on", {})
        src = TEMPLATE.format(
            title=title, short=short, cfgfile=cfgfile,
            source=d.get("_source", ""), arch=d.get("_optimizer_architecture", ""),
            dev_start=d.get("_development_start"), dev_end=d.get("_development_end"),
            uns_start=d.get("_unseen_start"), uns_end=d.get("_unseen_end"),
            capital=int(r["initial_capital"]), commission=e["commission_pct"],
            slippage=int(e["slippage_ticks"]), tick=e["tick_size"], qstep=r["quantity_step"],
            ema=int(s["ema_period"]), rsi=int(s["rsi_period"]),
            ob=round(float(s["rsi_overbought"]), 1), os=round(float(s["rsi_oversold"]), 1),
            atr=int(s["atr_period"]), cons=int(s["consolidation_candles"]),
            cmult=round(float(s["consolidation_atr_mult"]), 2),
            swing=int(s["swing_lookback"]), vsma=int(s["volume_sma_period"]),
            vmult=round(float(s["volume_mult"]), 2), rr=round(float(s["risk_reward_ratio"]), 2),
            lev=round(float(r["leverage"]), 1), risk=round(float(r["risk_per_trade_pct"]), 2),
            alloc=round(float(r["max_position_allocation_pct"]), 1),
            bb_enabled=str(bool(b["enabled"])).lower(), bb_len=int(b["length"]),
            bb_std=round(float(b["std"]), 2), bb_minbw=round(float(b["min_bandwidth_pct"]), 2),
            bb_explb=int(b["expansion_lookback"]),
            bb_expratio=round(float(b["expansion_min_ratio"]), 2),
            bb_middist=round(float(b["min_mid_distance"]), 2),
            ref_dev_ret=m.get("development_return_pct"), ref_dev_pf=m.get("development_pf"),
            ref_dev_dd=m.get("development_max_dd_pct"), ref_dev_n=m.get("development_trades"),
            ref_uoff_ret=uo.get("return_pct"), ref_uoff_pf=uo.get("pf"),
            ref_uoff_dd=uo.get("max_dd_pct"), ref_uoff_n=uo.get("trades"),
            ref_uon_ret=un.get("return_pct"), ref_uon_pf=un.get("pf"),
            ref_uon_dd=un.get("max_dd_pct"), ref_uon_n=un.get("trades"),
        )
        open(os.path.join(OUT, pinefile), "w").write(src)
        print(f"  pine/{pinefile}  <- configs/{cfgfile}")


if __name__ == "__main__":
    main()
