"""Research package: hypothesis backtesting, reverification, reporting."""

from trading_platform.research.backtest import (
    BacktestConfig,
    BacktestResult,
    CostModel,
    Trade,
    run_backtest,
)
from trading_platform.research.runner import (
    ExternalSetupRequired,
    load_parquet_universe,
    run_family_research,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "CostModel",
    "ExternalSetupRequired",
    "Trade",
    "load_parquet_universe",
    "run_backtest",
    "run_family_research",
]
