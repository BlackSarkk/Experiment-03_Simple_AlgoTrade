"""Script to export V3 winner to Pine format.
"""
import json
import os
import sys

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
sys.path.insert(0, ROOT)

import tools.generate_pine as gp

def verify_parity(cfg, pine_path, is_bb_on):
    with open(pine_path, "r") as f:
        content = f.read()

    errors = []
    
    # 11 strategy parameters
    s = cfg["strategy"]
    for k, v in [
        ("ema_len", s["ema_period"]),
        ("rsi_len", s["rsi_period"]),
        ("rsi_ob", s["rsi_overbought"]),
        ("rsi_os", s["rsi_oversold"]),
        ("atr_len", s["atr_period"]),
        ("cons_len", s["consolidation_candles"]),
        ("cons_mult", s["consolidation_atr_mult"]),
        ("vol_sma_len", s["volume_sma_period"]),
        ("vol_mult", s["volume_mult"]),
        ("rr_ratio", s["risk_reward_ratio"]),
        ("swing_len", s["swing_lookback"]),
    ]:
        found = False
        for line in content.splitlines():
            sline = line.strip()
            if sline.startswith(k) and sline[len(k):].strip().startswith("="):
                found = True
                val_part = line.split("=", 1)[1].strip()
                if "input.int(" in val_part or "input.float(" in val_part:
                    val_str = val_part.split("(")[1].split(",")[0].strip()
                    val = float(val_str) if "." in val_str else int(val_str)
                    if abs(val - v) > 1e-6:
                        errors.append(f"Parity mismatch for strategy {k}: config has {v}, Pine input has {val}")
                break
        if not found:
            errors.append(f"Could not find input declaration for {k} in Pine script")

    # 3 risk parameters
    r = cfg["risk"]
    for k, v in [
        ("leverage", r["leverage"]),
        ("risk_pct", r["risk_per_trade_pct"]),
        ("max_alloc", r["max_position_allocation_pct"]),
    ]:
        found = False
        for line in content.splitlines():
            sline = line.strip()
            if sline.startswith(k) and sline[len(k):].strip().startswith("="):
                found = True
                val_part = line.split("=", 1)[1].strip()
                if "input.float(" in val_part:
                    val_str = val_part.split("(")[1].split(",")[0].strip()
                    val = float(val_str)
                    if abs(val - v) > 1e-6:
                        errors.append(f"Parity mismatch for risk {k}: config has {v}, Pine input has {val}")
                break
        if not found:
            errors.append(f"Could not find input declaration for {k} in Pine script")

    # execution
    e = cfg["execution"]
    # commission
    comm_line = "commission_value="
    if comm_line in content:
        val_str = content.split(comm_line)[1].split(",")[0].strip()
        val = float(val_str)
        if abs(val - e["commission_pct"]) > 1e-6:
            errors.append(f"Parity mismatch for commission: config has {e['commission_pct']}, Pine has {val}")
    else:
        errors.append("commission_value not found in strategy definition")

    # slippage
    slip_line = "slippage="
    if slip_line in content:
        val_str = content.split(slip_line)[1].split(",")[0].strip()
        val = int(val_str)
        if val != e["slippage_ticks"]:
            errors.append(f"Parity mismatch for slippage: config has {e['slippage_ticks']}, Pine has {val}")
    else:
        errors.append("slippage not found in strategy definition")

    # tick_size
    tick_line = "tick_size "
    found_tick = False
    for line in content.splitlines():
        if line.strip().startswith(tick_line):
            found_tick = True
            val_part = line.split("=", 1)[1].strip()
            val_str = val_part.split("(")[1].split(",")[0].strip()
            val = float(val_str)
            if abs(val - e["tick_size"]) > 1e-6:
                errors.append(f"Parity mismatch for tick_size: config has {e['tick_size']}, Pine has {val}")
            break
    if not found_tick:
        errors.append("tick_size input not found")

    # qty_step
    qstep_line = "qty_step "
    found_qstep = False
    for line in content.splitlines():
        if line.strip().startswith(qstep_line):
            found_qstep = True
            val_part = line.split("=", 1)[1].strip()
            val_str = val_part.split("(")[1].split(",")[0].strip()
            val = float(val_str)
            if abs(val - r["quantity_step"]) > 1e-6:
                errors.append(f"Parity mismatch for qty_step: config has {r['quantity_step']}, Pine has {val}")
            break
    if not found_qstep:
        errors.append("qty_step input not found")

    # long/short flags
    enable_long_line = "enable_long "
    enable_short_line = "enable_short "
    for line in content.splitlines():
        if line.strip().startswith(enable_long_line):
            val_str = line.split("input.bool(")[1].split(",")[0].strip()
            if val_str != "true":
                errors.append(f"Parity mismatch for long_enabled: config has True, Pine has {val_str}")
        if line.strip().startswith(enable_short_line):
            val_str = line.split("input.bool(")[1].split(",")[0].strip()
            if val_str != "false":
                errors.append(f"Parity mismatch for short_enabled: config has False, Pine has {val_str}")

    # Bollinger parameters and enabled state
    b = cfg["filters"]["bollinger"]
    bb_enabled_line = "bb_enabled "
    found_bb_enabled = False
    for line in content.splitlines():
        if line.strip().startswith(bb_enabled_line):
            found_bb_enabled = True
            val_str = line.split("input.bool(")[1].split(",")[0].strip()
            expected = "true" if is_bb_on else "false"
            if val_str != expected:
                errors.append(f"Parity mismatch for bb_enabled: config has {expected}, Pine has {val_str}")
            break
    if not found_bb_enabled:
        errors.append("bb_enabled input not found")

    for k, v in [
        ("bb_len", b["length"]),
        ("bb_std", b["std"]),
        ("bb_min_bw", b["min_bandwidth_pct"]),
        ("bb_exp_lb", b["expansion_lookback"]),
        ("bb_exp_ratio", b["expansion_min_ratio"]),
        ("bb_mid_dist", b["min_mid_distance"]),
    ]:
        found = False
        for line in content.splitlines():
            sline = line.strip()
            if sline.startswith(k) and sline[len(k):].strip().startswith("="):
                found = True
                val_part = line.split("=", 1)[1].strip()
                if "input.int(" in val_part or "input.float(" in val_part:
                    val_str = val_part.split("(")[1].split(",")[0].strip()
                    val = float(val_str) if "." in val_str else int(val_str)
                    if abs(val - v) > 1e-6:
                        errors.append(f"Parity mismatch for bollinger {k}: config has {v}, Pine input has {val}")
                break
        if not found:
            errors.append(f"Could not find input declaration for {k} in Pine script")

    return errors

def main():
    target_off = os.path.join(ROOT, "pine", "v3_eth15m_bb_off.pine")
    target_on = os.path.join(ROOT, "pine", "v3_eth15m_bb_on.pine")
    
    if os.path.exists(target_off) or os.path.exists(target_on):
        print(f"FAIL: Target files already exist: off_exists={os.path.exists(target_off)}, on_exists={os.path.exists(target_on)}")
        sys.exit(1)

    results_path = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase14_v3_eth", "phase14_results.json")
    with open(results_path, "r") as f:
        res = json.load(f)

    winner_params = res["stages"]["2a_final"]["params"]
    boll_params = res["stages"]["2b_boll"]["cfg"]
    
    # Construct base dictionary matching generate_pine render layout
    # strategy params
    s_cfg = {
        "ema_period": int(winner_params["ema_period"]),
        "rsi_period": int(winner_params["rsi_period"]),
        "rsi_overbought": float(winner_params["rsi_overbought"]),
        "rsi_oversold": float(winner_params["rsi_oversold"]),
        "atr_period": int(winner_params["atr_period"]),
        "consolidation_candles": int(winner_params["consolidation_candles"]),
        "consolidation_atr_mult": float(winner_params["consolidation_atr_mult"]),
        "swing_lookback": int(winner_params["swing_lookback"]),
        "volume_sma_period": int(winner_params["volume_sma_period"]),
        "volume_mult": float(winner_params["volume_mult"]),
        "risk_reward_ratio": float(winner_params["risk_reward_ratio"]),
        "long_enabled": True,
        "short_enabled": False
    }

    # risk params (converted to percentages for pine generator)
    r_cfg = {
        "initial_capital": 10000.0,
        "leverage": float(winner_params["leverage"]),
        "risk_per_trade_pct": float(winner_params["risk_per_trade_pct"]) * 100.0,
        "max_position_allocation_pct": float(winner_params["max_position_allocation_pct"]) * 100.0,
        "quantity_step": 0.001
    }

    # execution params
    e_cfg = {
        "commission_pct": 0.05,
        "slippage_ticks": 1,
        "tick_size": 0.01
    }

    b_cfg = {
        "enabled": False,
        "length": int(boll_params["length"]),
        "std": float(boll_params["std"]),
        "min_bandwidth_pct": float(boll_params["min_bandwidth_pct"]),
        "expansion_lookback": int(boll_params["expansion_lookback"]),
        "expansion_min_ratio": float(boll_params["expansion_min_ratio"]),
        "min_mid_distance": float(boll_params["min_mid_distance"])
    }

    cfg_off = {
        "strategy": s_cfg,
        "risk": r_cfg,
        "filters": {"bollinger": b_cfg},
        "execution": e_cfg,
        "_source": "Phase 15 V3 ETH15m BB OFF export",
        "_optimizer_architecture": "new_optimizer_v3",
        "_train_start": "2024-07-16 00:00:00+00:00",
        "_validation_end": "2026-05-31 23:45:00+00:00",
        "_reference_metrics": {
            "development_return_pct": res["dev_metrics"]["off"]["train"]["return_pct"] + res["dev_metrics"]["off"]["valid"]["return_pct"],
            "development_pf": res["dev_metrics"]["off"]["valid"]["pf"],
            "development_max_dd_pct": res["dev_metrics"]["off"]["valid"]["max_dd"],
            "development_trades": res["dev_metrics"]["off"]["train"]["trades"] + res["dev_metrics"]["off"]["valid"]["trades"]
        }
    }

    # For BB ON
    cfg_on = json.loads(json.dumps(cfg_off))
    cfg_on["filters"]["bollinger"]["enabled"] = True
    cfg_on["_source"] = "Phase 15 V3 ETH15m BB ON export"
    cfg_on["_reference_metrics"] = {
        "development_return_pct": res["dev_metrics"]["on"]["train"]["return_pct"] + res["dev_metrics"]["on"]["valid"]["return_pct"],
        "development_pf": res["dev_metrics"]["on"]["valid"]["pf"],
        "development_max_dd_pct": res["dev_metrics"]["on"]["valid"]["max_dd"],
        "development_trades": res["dev_metrics"]["on"]["train"]["trades"] + res["dev_metrics"]["on"]["valid"]["trades"]
    }

    # Custom render caller that takes direct config dictionary
    def render_dict(d, title, short):
        s, r, b, e = d["strategy"], d["risk"], d["filters"]["bollinger"], d["execution"]
        m = d.get("_reference_metrics", {})
        uo, un = m.get("unseen_filter_off", {}), m.get("unseen_filter_on", {})
        return gp.TEMPLATE.format(
            title=title, short=short, cfgfile="v3_memory_config",
            source=d.get("_source", ""),
            arch=d.get("_optimizer_architecture", ""),
            dev_start=gp._fmt(d.get("_development_start") or d.get("_train_start")),
            dev_end=gp._fmt(d.get("_development_end") or d.get("_validation_end")),
            uns_start=gp._fmt(d.get("_unseen_start")), uns_end=gp._fmt(d.get("_unseen_end")),
            capital=int(r["initial_capital"]), commission=e["commission_pct"],
            slippage=int(e["slippage_ticks"]), tick=e["tick_size"],
            qstep=r["quantity_step"],
            ema=int(s["ema_period"]), rsi=int(s["rsi_period"]),
            ob=round(float(s["rsi_overbought"]), 1), os=round(float(s["rsi_oversold"]), 1),
            atr=int(s["atr_period"]), cons=int(s["consolidation_candles"]),
            cmult=round(float(s["consolidation_atr_mult"]), 2),
            swing=int(s["swing_lookback"]), vsma=int(s["volume_sma_period"]),
            vmult=round(float(s["volume_mult"]), 2),
            rr=round(float(s["risk_reward_ratio"]), 2),
            lev=round(float(r["leverage"]), 1),
            risk=round(float(r["risk_per_trade_pct"]), 2),
            alloc=round(float(r["max_position_allocation_pct"]), 1),
            bb_enabled=str(bool(b["enabled"])).lower(), bb_len=int(b["length"]),
            bb_std=round(float(b["std"]), 2),
            bb_minbw=round(float(b["min_bandwidth_pct"]), 2),
            bb_explb=int(b["expansion_lookback"]),
            bb_expratio=round(float(b["expansion_min_ratio"]), 2),
            bb_middist=round(float(b["min_mid_distance"]), 2),
            ref_dev_ret=gp._fmt(m.get("development_return_pct")),
            ref_dev_pf=gp._fmt(m.get("development_pf")),
            ref_dev_dd=gp._fmt(m.get("development_max_dd_pct")),
            ref_dev_n=gp._fmt(m.get("development_trades")),
            ref_uoff_ret=gp._fmt(uo.get("return_pct")), ref_uoff_pf=gp._fmt(uo.get("pf")),
            ref_uoff_dd=gp._fmt(uo.get("max_dd_pct")), ref_uoff_n=gp._fmt(uo.get("trades")),
            ref_uon_ret=gp._fmt(un.get("return_pct")), ref_uon_pf=gp._fmt(un.get("pf")),
            ref_uon_dd=gp._fmt(un.get("max_dd_pct")), ref_uon_n=gp._fmt(un.get("trades")),
        )

    # Write scripts
    os.makedirs(os.path.join(ROOT, "pine"), exist_ok=True)
    with open(target_off, "w") as f:
        f.write(render_dict(cfg_off, "ETHUSDT 15m V3 BB OFF", "V3-ETH15m-OFF"))
        
    with open(target_on, "w") as f:
        f.write(render_dict(cfg_on, "ETHUSDT 15m V3 BB ON", "V3-ETH15m-ON"))

    # Verify parity
    err_off = verify_parity(cfg_off, target_off, False)
    err_on = verify_parity(cfg_on, target_on, True)

    all_errs = err_off + err_on
    if all_errs:
        print("PARITY: FAIL")
        for err in all_errs:
            print(f"  {err}")
        sys.exit(1)
    else:
        print("PARITY: PASS")
        print(f"  Generated off: {target_off}")
        print(f"  Generated on:  {target_on}")

if __name__ == "__main__":
    main()
