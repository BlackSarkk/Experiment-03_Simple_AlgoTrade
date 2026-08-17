import pandas as pd
df = pd.read_csv("src/optimization/new_optimizer_lab/phase12_parity/data/ETHUSDT_15m_warmup_dev_test.csv")
dt = pd.to_datetime(df["datetime"], utc=True)

# 1. Warmup check
dev_start = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
warmup_count = (dt < dev_start).sum()
print("Warmup rows before 2024-07-16 00:00:", warmup_count)

# 2. DEV check
dev_end = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")
dev_rows = ((dt >= dev_start) & (dt <= dev_end)).sum()
print("DEV rows (2024-07-16 00:00 to 2026-07-15 23:45):", dev_rows)

# 3. 70/30 split boundary check
train_rows = int(dev_rows * 0.70)
valid_rows = dev_rows - train_rows
print(f"70/30 row split inside DEV: TRAIN={train_rows}, VALID={valid_rows}")
valid_start_idx = warmup_count + train_rows
print("VALID start timestamp:", dt.iloc[valid_start_idx])

# 4. Comparison check
comp_start = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")
comp_end = pd.Timestamp("2026-08-15 23:45:00", tz="UTC")
comp_rows = ((dt >= comp_start) & (dt <= comp_end)).sum()
print("Locked comparison rows:", comp_rows)
print("Locked comparison ends at:", dt[dt <= comp_end].iloc[-1])
