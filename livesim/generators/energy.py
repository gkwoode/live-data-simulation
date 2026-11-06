"""Smart electricity meters with load profiles, weather, rooftop solar and outages."""

from __future__ import annotations

import math

from ..schema import F
from ..util import clamp, iso, make_id
from .base import Generator

PRICES = {"peak": 0.34, "shoulder": 0.21, "off_peak": 0.11}  # USD/kWh residential
PRICE_FACTOR = {"residential": 1.0, "commercial": 0.85, "industrial": 0.7}
POWER_FACTOR = {"residential": (0.9, 0.99), "commercial": (0.85, 0.97), "industrial": (0.8, 0.95)}


def _bump(hour: float, center: float, width: float) -> float:
    d = abs(hour - center) % 24
    d = min(d, 24 - d)
    return math.exp(-0.5 * (d / width) ** 2)


def _ramp(hour: float, start: float, end: float) -> float:
    """Smooth 0 -> 1 -> 0 window for business hours."""
    return 1 / (1 + math.exp(-(hour - start) * 2.5)) * (1 / (1 + math.exp((hour - end) * 2.5)))


def demand_profile(meter_type: str, hour: float, weekday: int) -> float:
    weekend = weekday >= 5
    if meter_type == "residential":
        morning = _bump(hour, 9.5 if weekend else 7.5, 1.3) * 0.8
        midday = _bump(hour, 13, 2.5) * (0.5 if weekend else 0.15)
        evening = _bump(hour, 19.5, 2.0) * 1.7
        return 0.35 + morning + midday + evening
    if meter_type == "commercial":
        return 0.3 + (0.15 if weekend else 0.75) * _ramp(hour, 7.5, 18.5)
    shift_dip = 0.08 * sum(_bump(hour, h, 0.4) for h in (6, 14, 22))
    return (0.6 if weekend else 0.88) - shift_dip


def tariff_period(hour: float, weekday: int) -> str:
    if hour >= 23 or hour < 7:
        return "off_peak"
    if weekday < 5 and 17 <= hour < 21:
        return "peak"
    return "shoulder"


class EnergyMetersGenerator(Generator):
    name = "energy_meters"
    title = "Smart energy meters"
    category = "Energy & utilities"
    description = (
        "Readings from 48 smart meters (homes, offices and factories) in eight cities. Load follows each city's local time, "
        "weather drives heating and cooling, rooftop solar exports at midday, and the cumulative kWh register only goes up."
    )
    default_rate = 3.0
    history_per_day = 12000
    key_fields = ("meter_id",)
    metric = ("Average load per reading (kW)", "power_kw", "avg")
    related = ("dim_meter", "dim_date")
    fields = [
        F("reading_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("meter_id", "string", "fk", "-> dim_meter"),
        F("meter_type", "string", "dimension", "residential | commercial | industrial"),
        F("city", "string", "dimension"),
        F("power_kw", "float", "measure", "Instantaneous consumption"),
        F("solar_kw", "float", "measure", "Instantaneous rooftop solar generation"),
        F("net_power_kw", "float", "measure", "Consumption minus solar; negative means exporting to the grid"),
        F(
            "energy_kwh_total",
            "float",
            "measure",
            "Cumulative import register (monotonic; difference consecutive readings for usage)",
        ),
        F("voltage_v", "float", "measure"),
        F("current_a", "float", "measure"),
        F("power_factor", "float", "measure"),
        F("outdoor_temp_c", "float", "measure"),
        F("cloud_cover_pct", "float", "measure"),
        F("tariff_period", "string", "dimension", "peak | shoulder | off_peak"),
        F("price_per_kwh", "float", "measure", "USD"),
        F("status", "string", "dimension", "OK | LOW_VOLTAGE | OUTAGE"),
    ]
    use_cases = {
        "analyst": [
            "Compare load profiles by meter type and city (heatmap of hour x weekday).",
            "Estimate cost by tariff period and the value of solar exports.",
            "Track outages and low-voltage events per city.",
        ],
        "engineer": [
            "Turn the cumulative register into interval usage with window functions (LAG) per meter.",
            "Resample irregular readings to 15-minute intervals and fill gaps.",
            "Detect outages in a stream and alert when a meter goes silent or reports zero voltage.",
        ],
        "scientist": [
            "Forecast city load from temperature and calendar features (backfill 30 days first).",
            "Disaggregate solar from net load; model solar output versus cloud cover.",
            "Cluster customers by daily load shape.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.meters = []
        for row in ref.meters:
            params = ref.meter_params[row["meter_id"]]
            self.meters.append({"row": row, "p": params, "register": params["register_start"], "last": None, "outage": 0})
        cities = {m["p"]["city"].name for m in self.meters}
        self.weather = {name: {"cloud": rng.random(), "temp_dev": rng.gauss(0, 1.5)} for name in cities}
        self.index = rng.randrange(len(self.meters))

    def generate(self, ts):
        rng = self.rng
        meter = self.meters[self.index % len(self.meters)]
        self.index += 1
        p, row = meter["p"], meter["row"]
        city = p["city"]
        weather = self.weather[city.name]
        weather["cloud"] = clamp(weather["cloud"] + rng.gauss(0, 0.03), 0.0, 1.0)
        weather["temp_dev"] = 0.995 * weather["temp_dev"] + rng.gauss(0, 0.08)
        temperature = city.outdoor_temp(ts) + weather["temp_dev"]
        hour = city.local_hour(ts)
        weekday = city.local_time(ts).weekday()

        hvac = p["hvac"] * (max(0.0, temperature - 22) * 0.09 + max(0.0, 15 - temperature) * 0.06)
        power = p["scale"] * (demand_profile(p["type"], hour, weekday) + hvac) * rng.lognormvariate(0, 0.06)
        daylight = max(0.0, math.sin(math.pi * (hour - 6) / 13))
        solar = p["solar_kw"] * daylight * (1 - 0.8 * weather["cloud"]) * rng.uniform(0.92, 1.0)

        status = "OK"
        voltage = p["voltage"] * rng.uniform(0.975, 1.025)
        if meter["outage"] > 0:
            meter["outage"] -= 1
            status, voltage, power, solar = "OUTAGE", 0.0, 0.0, 0.0
        elif rng.random() < 1 / 8000:
            meter["outage"] = rng.randint(5, 25)
        elif rng.random() < 0.004:
            status, voltage = "LOW_VOLTAGE", p["voltage"] * rng.uniform(0.86, 0.92)

        net = power - solar
        if meter["last"] is not None:
            hours = clamp((ts - meter["last"]).total_seconds() / 3600, 0.0, 6.0)
            meter["register"] += max(net, 0.0) * hours
        meter["last"] = ts
        pf = rng.uniform(*POWER_FACTOR[p["type"]])
        current = power * 1000 / (voltage * pf) if voltage > 0 else 0.0
        period = tariff_period(hour, weekday)

        return [
            {
                "reading_id": make_id("MRD", ts, rng),
                "event_time": iso(ts),
                "meter_id": row["meter_id"],
                "meter_type": p["type"],
                "city": city.name,
                "power_kw": round(power, 3),
                "solar_kw": round(solar, 3),
                "net_power_kw": round(net, 3),
                "energy_kwh_total": round(meter["register"], 3),
                "voltage_v": round(voltage, 1),
                "current_a": round(current, 2),
                "power_factor": round(pf, 3),
                "outdoor_temp_c": round(temperature, 1),
                "cloud_cover_pct": round(weather["cloud"] * 100, 1),
                "tariff_period": period,
                "price_per_kwh": round(PRICES[period] * PRICE_FACTOR[p["type"]], 4),
                "status": status,
            }
        ]
