def trade_levels(price, config):
    stop_pct = float(config.get("stop_loss_pct", 0.03))
    t1_pct = float(config.get("target_1_pct", 0.04))
    t2_pct = float(config.get("target_2_pct", 0.07))
    return {
        "entry_low": round(price * 0.998, 2),
        "entry_high": round(price * 1.005, 2),
        "stop_loss": round(price * (1-stop_pct), 2),
        "target_1": round(price * (1+t1_pct), 2),
        "target_2": round(price * (1+t2_pct), 2),
        "risk_reward_t1": round(t1_pct/stop_pct, 2) if stop_pct else None
    }
