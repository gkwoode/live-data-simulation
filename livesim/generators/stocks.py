"""Market data: correlated, mean-reverting price paths with jumps, spreads and trade prints."""

from __future__ import annotations

import math

from ..schema import F
from ..util import Weighted, iso, make_id
from .base import Generator

SIGMA = 0.00025  # idiosyncratic volatility per sqrt(second) for a volatility factor of 1
SIGMA_MARKET = 0.0002
KAPPA = 1 / (3 * 86400)  # mean reversion of each stock towards its reference price
KAPPA_MARKET = 1 / (5 * 86400)


class StockTicksGenerator(Generator):
    name = "stock_ticks"
    title = "Stock market ticks"
    category = "Finance & markets"
    description = (
        "Trade prints for 20 fictional listed companies. Prices follow a correlated, mean-reverting random walk "
        "(market factor x beta + idiosyncratic noise + occasional jumps) with realistic bid/ask spreads and trade sizes."
    )
    default_rate = 5.0
    history_per_day = 20000
    key_fields = ("symbol",)
    metric = ("Shares traded per second", "volume", "sum")
    related = ("dim_symbol", "dim_date")
    fields = [
        F("tick_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("symbol", "string", "fk", "-> dim_symbol"),
        F("exchange", "string", "dimension"),
        F("price", "float", "measure", "Trade price (prints at the ask for buys, at the bid for sells)"),
        F("bid", "float", "measure"),
        F("ask", "float", "measure"),
        F("bid_size", "int", "measure"),
        F("ask_size", "int", "measure"),
        F("volume", "int", "measure", "Shares traded in this print"),
        F("side", "string", "dimension", "Aggressor side: buy | sell"),
        F("currency", "string", "attribute"),
    ]
    use_cases = {
        "analyst": [
            "Build OHLC candlestick charts per symbol at 1-minute and 1-hour grain.",
            "Rank sectors by return and traded value (price x volume); join dim_symbol for sector.",
            "Calculate market capitalisation with shares_outstanding_m.",
        ],
        "engineer": [
            "Aggregate ticks to OHLCV bars with streaming tumbling windows.",
            "Compute VWAP and rolling volatility per symbol in near real time.",
            "Fan the stream out to Kafka keyed by symbol and consume per partition.",
        ],
        "scientist": [
            "Estimate betas and the correlation matrix from returns.",
            "Forecast short-term volatility (GARCH) and detect price jumps.",
            "Backtest a mean-reversion strategy against the reference price.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.params = ref.symbol_params
        self.exchange = {row["symbol"]: row["exchange"] for row in ref.symbols}
        self.pick = Weighted(list(self.params), [p["liquidity"] for p in self.params.values()])
        self.state = {
            symbol: {
                "logp": math.log(p["price"]) + rng.gauss(0, 0.02),
                "anchor": math.log(p["price"]),
                "market_ref": 0.0,
                "last": None,
            }
            for symbol, p in self.params.items()
        }
        self.market = 0.0
        self.market_last = None

    def generate(self, ts):
        rng = self.rng
        dt_market = 1.0 if self.market_last is None else min(max((ts - self.market_last).total_seconds(), 1e-3), 3600)
        self.market += -KAPPA_MARKET * self.market * dt_market + SIGMA_MARKET * math.sqrt(dt_market) * rng.gauss(0, 1)
        self.market_last = ts

        symbol = self.pick.pick(rng)
        p, st = self.params[symbol], self.state[symbol]
        dt = 1.0 if st["last"] is None else min(max((ts - st["last"]).total_seconds(), 1e-3), 3600)
        st["last"] = ts
        d_market = self.market - st["market_ref"]
        st["market_ref"] = self.market
        ret = (
            p["beta"] * d_market
            + SIGMA * p["vol"] * math.sqrt(dt) * rng.gauss(0, 1)
            - KAPPA * (st["logp"] - st["anchor"] - p["beta"] * self.market) * dt
        )
        if rng.random() < 0.0008 * p["vol"]:
            ret += rng.gauss(0, 0.012 * p["vol"])  # news jump
        st["logp"] += ret

        mid = math.exp(st["logp"])
        spread = max(0.01, mid * p["spread_bps"] / 10_000)
        bid = round(mid - spread / 2, 2)
        ask = max(round(mid + spread / 2, 2), round(bid + 0.01, 2))
        side = "buy" if (ret > 0) != (rng.random() < 0.35) else "sell"
        lot = 100 * max(1, int(p["liquidity"] / 3))
        return [
            {
                "tick_id": make_id("TCK", ts, rng),
                "event_time": iso(ts),
                "symbol": symbol,
                "exchange": self.exchange[symbol],
                "price": ask if side == "buy" else bid,
                "bid": bid,
                "ask": ask,
                "bid_size": lot * rng.randint(1, 40),
                "ask_size": lot * rng.randint(1, 40),
                "volume": max(1, int(rng.lognormvariate(math.log(60 * p["liquidity"]), 1.0) * (1 + 60 * abs(ret)))),
                "side": side,
                "currency": "USD",
            }
        ]
