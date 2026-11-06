"""Industrial IoT telemetry with degradation, failures, repairs and sensor glitches."""

from __future__ import annotations

import math

from ..schema import F
from ..util import clamp, iso, make_id
from .base import Generator

FAILURE_MODES = {
    "bearing_wear": "E101 BEARING_WEAR",
    "overheating": "E205 OVER_TEMPERATURE",
    "pressure_loss": "E310 LOW_PRESSURE",
}


class IotTelemetryGenerator(Generator):
    name = "iot_telemetry"
    title = "IoT sensor telemetry"
    category = "IoT & manufacturing"
    description = (
        "Sensor readings from 60 industrial machines across six sites. Machines slowly degrade, fail and get repaired; "
        "ground-truth labels (health_state, rul_cycles, is_anomaly) make it ideal for predictive maintenance and anomaly "
        "detection."
    )
    default_rate = 4.0
    history_per_day = 12000
    key_fields = ("reading_id",)
    metric = ("Average temperature (°C)", "temperature_c", "avg")
    related = ("dim_device", "dim_date")
    fields = [
        F("reading_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("device_id", "string", "fk", "-> dim_device"),
        F("device_type", "string", "dimension"),
        F("site_id", "string", "dimension"),
        F("temperature_c", "float", "measure"),
        F("vibration_mm_s", "float", "measure", "RMS vibration velocity"),
        F("pressure_bar", "float", "measure", "Null for equipment without a pressure sensor"),
        F("rpm", "float", "measure"),
        F("power_kw", "float", "measure"),
        F("humidity_pct", "float", "measure", "Ambient relative humidity"),
        F("signal_strength_dbm", "int", "measure"),
        F("status", "string", "dimension", "Device-reported status: OK | WARN | ALARM"),
        F("error_code", "string", "attribute", "Set while the machine is failed"),
        F("health_state", "string", "label", "Ground truth: normal | degrading | failed"),
        F("rul_cycles", "int", "label", "Ground truth remaining useful life in readings until failure (null unless degrading)"),
        F("is_anomaly", "bool", "label", "Ground truth: reading contains an injected sensor glitch"),
    ]
    use_cases = {
        "analyst": [
            "Build a plant operations dashboard: status by site, alarms over time, mean time between failures.",
            "Map devices with dim_device coordinates and colour them by status.",
            "Compare energy use (power_kw) across device types and shifts.",
        ],
        "engineer": [
            "Compute 1-minute tumbling-window aggregates per device in Spark Structured Streaming.",
            "Raise alerts when vibration exceeds a rolling baseline and route them to a webhook.",
            "Handle late and out-of-order readings with watermarks (turn on data-quality chaos).",
        ],
        "scientist": [
            "Train a remaining-useful-life regressor on rul_cycles (NASA turbofan style).",
            "Detect sensor glitches with isolation forests and score against is_anomaly.",
            "Classify degrading machines early from vibration and temperature trends.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.states = []
        for device in ref.devices:
            params = ref.device_params[device["device_id"]]
            self.states.append(
                {
                    "device": device,
                    "p": params,
                    "health": "normal",
                    "mode": None,
                    "cycle": 0,
                    "length": 0,
                    "temp": params["temp"],
                }
            )
        self.index = rng.randrange(len(self.states))

    def _step_health(self, st):
        rng = self.rng
        if st["health"] == "normal":
            if rng.random() < 1 / 1800:  # at default rates a machine starts degrading every ~8 minutes
                modes = list(FAILURE_MODES) if st["p"]["pressure"] is not None else ["bearing_wear", "overheating"]
                st.update(health="degrading", mode=rng.choice(modes), cycle=0, length=rng.randint(40, 140))
        elif st["health"] == "degrading":
            st["cycle"] += 1
            if st["cycle"] >= st["length"]:
                st.update(health="failed", cycle=0, length=rng.randint(8, 30))
        else:
            st["cycle"] += 1
            if st["cycle"] >= st["length"]:  # repaired by maintenance
                st.update(health="normal", mode=None, cycle=0, length=0)

    def generate(self, ts):
        rng = self.rng
        if rng.random() < 0.01:  # a missed transmission leaves a gap in that device's series
            self.index += 1
        st = self.states[self.index % len(self.states)]
        self.index += 1
        self._step_health(st)
        p, device = st["p"], st["device"]
        city = p["city"]
        ambient = city.outdoor_temp(ts)
        hour = city.local_hour(ts)
        load = (rng.uniform(0.85, 1.0) if 6 <= hour < 22 else rng.uniform(0.55, 0.72)) * rng.uniform(0.97, 1.03)
        ambient_coupling = 0.05 if p["type"] == "chiller" else 0.15

        temperature = p["temp"] + ambient_coupling * (ambient - 20) + 3 * (load - 0.8) + rng.gauss(0, 0.6)
        vibration = p["vib"] * (0.9 + 0.2 * load) * rng.lognormvariate(0, 0.08)
        pressure = None if p["pressure"] is None else p["pressure"] * (0.95 + 0.08 * load) + rng.gauss(0, 0.05)
        rpm = p["rpm"] * (0.9 + 0.1 * load) + rng.gauss(0, p["rpm"] * 0.004)
        power = p["power"] * load * rng.lognormvariate(0, 0.04)
        status, error_code, rul = "OK", None, None

        if st["health"] == "degrading":
            progress = st["cycle"] / st["length"]
            wear = progress**2
            rul = st["length"] - st["cycle"]
            if st["mode"] == "bearing_wear":
                vibration *= 1 + 3.2 * wear
                temperature += 8 * wear
                rpm *= 1 - 0.04 * wear
            elif st["mode"] == "overheating":
                temperature += 26 * wear
                power *= 1 + 0.22 * wear
                vibration *= 1 + 0.4 * wear
            else:
                pressure = pressure * (1 - 0.42 * progress) if pressure is not None else None
                power *= 1 + 0.18 * wear
                temperature += 5 * wear
        elif st["health"] == "failed":
            st["temp"] = ambient + (st["temp"] - ambient) * 0.85  # cooling down while stopped
            temperature = st["temp"] + rng.gauss(0, 0.3)
            vibration = rng.uniform(0.05, 0.3)
            rpm, power = 0.0, p["power"] * rng.uniform(0.01, 0.03)
            pressure = None if pressure is None else rng.uniform(0.0, 0.4)
            status, error_code = "ALARM", FAILURE_MODES[st["mode"]]
        if st["health"] != "failed":
            st["temp"] = temperature

        is_anomaly = rng.random() < 0.002
        if is_anomaly:
            glitch = rng.choice(("temp_spike", "vibration_spike", "flatline"))
            if glitch == "temp_spike":
                temperature += rng.uniform(15, 40)
            elif glitch == "vibration_spike":
                vibration *= rng.uniform(3, 6)
            else:
                temperature, vibration = 0.0, 0.0

        if status == "OK" and (
            temperature > p["temp"] + 10
            or vibration > p["vib"] * 1.9
            or (pressure is not None and pressure < 0.7 * p["pressure"])
        ):
            status = "WARN"
        humidity = 55 + 20 * math.cos(2 * math.pi * (hour - 5) / 24) + (12 if abs(city.lat) < 15 else 0) + rng.gauss(0, 4)

        return [
            {
                "reading_id": make_id("RDG", ts, rng),
                "event_time": iso(ts),
                "device_id": device["device_id"],
                "device_type": device["device_type"],
                "site_id": device["site_id"],
                "temperature_c": round(temperature, 2),
                "vibration_mm_s": round(vibration, 3),
                "pressure_bar": None if pressure is None else round(max(pressure, 0.0), 3),
                "rpm": round(max(rpm, 0.0), 1),
                "power_kw": round(power, 2),
                "humidity_pct": round(clamp(humidity, 5, 100), 1),
                "signal_strength_dbm": int(round(p["rssi"] + rng.gauss(0, 2.5))),
                "status": status,
                "error_code": error_code,
                "health_state": st["health"],
                "rul_cycles": rul,
                "is_anomaly": is_anomaly,
            }
        ]
