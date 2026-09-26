"""Phase 5: Walk-forward validation first fold demonstration."""
import sys
import datetime
import os

sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis, generate_signal
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator
from trading_platform.domain import Instrument, Bar

print("=" * 60)
print("PHASE 5: WALK-FORWARD VALIDATION - FIRST FOLD DEMO")
print("=" * 60)

# 1. Define period split (60 train / 20 validation / 30 test)
train_dates = [datetime.date(2020, 1, 1) + datetime.timedelta(days=i) for i in range(60)]
val_dates = [datetime.date(2020, 1, 1) + datetime.timedelta(days=60+i) for i in range(20)]
test_dates = [datetime.date(2020, 1, 1) + datetime.timedelta(days=80+i) for i in range(30)]

# 2. Generate mock bar data
def make_bars(dates):
    bars = {}
    for d in dates:
        price = 100.0 + (d.day % 15 - 7) * 0.8
        bars[d.isoformat()] = Bar(
            instrument=Instrument(symbol="AAPL"),
            timestamp=datetime.datetime(d.year, d.month, d.day, 16, 0, 0),
            open=price, high=price+2, low=price-2, close=price, volume=1000
        )
    return bars

train_bars = make_bars(train_dates)
val_bars = make_bars(val_dates)
test_bars = make_bars(test_dates)

# 3. Setup hypothesis (PROVISIONAL - fixed parameters)
hypothesis = MaCrossHypothesis(fast_length=5, slow_length=20, commission_per_order=1.0)
print(f"\nHypothesis: {hypothesis.hypothesis_id}")
print(f"  Fast SMA: {hypothesis.fast_length}, Slow SMA: {hypothesis.slow_length}")

# 4. Generate signals for test period only (the locked test)
signals = []
for bar_date, bar_list in sorted(test_bars.items()):
    sig = generate_signal({"AAPL": train_bars}, hypothesis, "AAPL", bar_date)
    if sig:
        signals.append(sig)

print(f"\nGenerated {len(signals)} signals from test period")

# 5. Run simulator on test period with signals from train hypothesis
sim = EventDrivenSimulator(mode="DETERMINISTIC", start_cash=10000.0, fill_assumption="CLOSE")
result = sim.run(bars=test_bars, signals=signals)

print(f"\nTEST PERIOD RESULTS (LOCKED):")
print(f"  Final cash:      ${result.final_cash:.2f}")
print(f"  Total PnL:       ${result.final_portfolio.total_pnl:.2f}")
print(f"  Trade count:     {len(result.trade_ledger)}")
print(f"  Commission:      ${result.total_commission:.2f}")
print(f"  Slippage:        ${result.total_slippage:.2f}")

# 6. G4/S4 criteria
oos_pnl = result.final_portfolio.total_pnl  # starting was 10000, now final
total_costs = result.total_commission + result.total_slippage
oos_expectancy = oos_pnl - total_costs

print(f"\nG4/S4 CRITERIA:")
print(f"  Out-of-sample PnL:        ${oos_pnl:.2f}")
print(f"  Total costs (commission+slippage): ${total_costs:.2f}")
print(f"  OOS expectancy (PnL - costs): ${oos_expectancy:.2f}")
if oos_expectancy > 0:
    print(f"  ✅ PASS: Positive expectancy after costs")
else:
    print(f"  ❌ FAIL: Negative expectancy after costs - hypothesis needs revision")

print(f"\n{'=' * 60}")
print("Phase 5 first fold validation complete.")
print("To run full walk-forward: iterate over multiple folds,")
print("perform sensitivity analysis on SMA lengths,")
print("and test under increased costs for robustness.")
print("=" * 60)