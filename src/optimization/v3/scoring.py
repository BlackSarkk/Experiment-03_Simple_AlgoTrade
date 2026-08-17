"""V3 gates and scores — stdlib only, pure functions. Fixed before any run.

Every rule here is deterministic and version-stamped. Failures are GRADED, never a flat
sentinel, so TPE always has an ordering to learn from.
"""
from . import spec


def clip(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def relu(v):
    return v if v > 0.0 else 0.0


def min_trades(rows):
    return max(spec.MIN_TRADES_FLOOR, rows // spec.MIN_TRADES_PER_ROWS)


def gate_shortfall(tr, va, min_tr, min_va):
    """0.0 when every requirement is met; each violated requirement adds up to 1.0."""
    if tr is None or va is None:
        return 6.0
    g = spec.GATE
    return (clip(1.0 - tr["trades"] / max(min_tr, 1), 0.0, 1.0)
            + clip(1.0 - va["trades"] / max(min_va, 1), 0.0, 1.0)
            + clip(-va["return_pct"] / 100.0, 0.0, 1.0)
            + clip((g["valid_profit_factor_ge"] - va["pf"]) / g["valid_profit_factor_ge"], 0.0, 1.0)
            + clip((va["max_dd"] - g["valid_max_dd_pct_le"]) / g["valid_max_dd_pct_le"], 0.0, 1.0)
            + clip(-tr["return_pct"] / 100.0, 0.0, 1.0))


def passes(tr, va, min_tr, min_va):
    return gate_shortfall(tr, va, min_tr, min_va) <= 0.0


def score(tr, va, min_tr, min_va):
    """Returns (value, components). Passing scores are always > FAIL_BASE."""
    sf = gate_shortfall(tr, va, min_tr, min_va)
    if sf > 0.0:
        return spec.FAIL_BASE - spec.FAIL_SPAN * clip(sf / 6.0, 0.0, 1.0), {"gate_shortfall": sf}
    w, c = spec.W, spec.CAPS
    comp = {
        "va_ret":      w["va_ret"] * clip(va["return_pct"] / c["va_ret_cap_pct"], -1.0, 1.0),
        "va_pf":       w["va_pf"] * clip(va["pf"] - 1.0, 0.0, c["va_pf_cap"] - 1.0),
        "va_dd":      -w["va_dd"] * clip(relu(va["max_dd"] - c["va_dd_free_pct"]) / c["va_dd_span_pct"], 0.0, 1.0),
        "va_sample":   w["va_sample"] * clip(va["trades"] / (c["va_sample_target_x"] * max(min_va, 1)), 0.0, 1.0),
        "tr_ret":      w["tr_ret"] * clip(tr["return_pct"] / c["tr_ret_cap_pct"], -1.0, 1.0),
        "tr_pf":       w["tr_pf"] * clip(tr["pf"] - 1.0, 0.0, c["tr_pf_cap"] - 1.0),
        "consistency": -w["consistency"] * clip(
            abs(tr["return_pct"] - va["return_pct"])
            / max(abs(tr["return_pct"]) + abs(va["return_pct"]), 1e-9), 0.0, 1.0),
    }
    return sum(comp.values()), comp


def boll_score(off, on, min_tr, min_va):
    """TRAIN and VALID both contribute PF, net P&L and drawdown. Graded failure."""
    t_off, v_off, t_on, v_on = off["train"], off["valid"], on["train"], on["valid"]
    if v_on is None or t_on is None:
        return spec.FAIL_BASE - spec.FAIL_SPAN, {"reason": "no trades with filter on"}
    ratio = v_on["trades"] / max(v_off["trades"], 1)
    sf = (clip(1.0 - v_on["trades"] / max(min_va, 1), 0.0, 1.0)
          + clip(1.0 - t_on["trades"] / max(min_tr, 1), 0.0, 1.0)
          + clip((spec.BOLL_MIN_TRADE_RETENTION - ratio) / spec.BOLL_MIN_TRADE_RETENTION, 0.0, 1.0))
    if sf > 0.0:
        return spec.FAIL_BASE - spec.FAIL_SPAN * clip(sf / 3.0, 0.0, 1.0), {"gate_shortfall": sf,
                                                                            "valid_trade_ratio": ratio}
    def dpf(a, b):
        return clip(b["pf"] - a["pf"], -0.8, 0.8) / 0.8

    def dnet(a, b):
        return clip((b["net_pnl"] - a["net_pnl"]) / max(abs(a["net_pnl"]), 1.0), -1.0, 1.0)

    def ddd(a, b):
        return clip((a["max_dd"] - b["max_dd"]) / max(a["max_dd"], 1e-9), -1.0, 1.0)

    bw = spec.BW
    comp = {"va_pf": bw["va_pf"] * dpf(v_off, v_on), "va_netpnl": bw["va_netpnl"] * dnet(v_off, v_on),
            "va_dd": bw["va_dd"] * ddd(v_off, v_on), "tr_pf": bw["tr_pf"] * dpf(t_off, t_on),
            "tr_netpnl": bw["tr_netpnl"] * dnet(t_off, t_on), "tr_dd": bw["tr_dd"] * ddd(t_off, t_on),
            "valid_trade_ratio": ratio}
    return sum(v for k, v in comp.items() if k != "valid_trade_ratio"), comp
