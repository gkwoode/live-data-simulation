"""Registry of all simulated streams."""

from .base import Generator
from .clickstream import ClickstreamGenerator
from .energy import EnergyMetersGenerator
from .inventory import InventoryCdcGenerator
from .iot import IotTelemetryGenerator
from .logs import AppLogsGenerator
from .orders import OrdersGenerator
from .reviews import ReviewsGenerator
from .rides import RidesGenerator
from .stocks import StockTicksGenerator
from .transactions import CardTransactionsGenerator

GENERATORS: list[type[Generator]] = [
    OrdersGenerator,
    ClickstreamGenerator,
    CardTransactionsGenerator,
    IotTelemetryGenerator,
    StockTicksGenerator,
    AppLogsGenerator,
    RidesGenerator,
    EnergyMetersGenerator,
    ReviewsGenerator,
    InventoryCdcGenerator,
]
REGISTRY: dict[str, type[Generator]] = {g.name: g for g in GENERATORS}

__all__ = ["GENERATORS", "REGISTRY", "Generator"]
