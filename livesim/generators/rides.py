"""Ride-hailing trips in three cities with zones, traffic, weather and surge pricing."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

from ..reference import CITY_BY_NAME, ZONES
from ..schema import F
from ..util import DailyPattern, Weighted, haversine_km, iso, make_id
from .base import Generator

CITY_SHARE = Weighted(("New York", "London", "Singapore"), (45, 35, 20))
VEHICLES = Weighted(("economy", "comfort", "xl", "premium"), (62, 20, 12, 6))
VEHICLE_MULTIPLIER = {"economy": 1.0, "comfort": 1.3, "xl": 1.6, "premium": 2.2}
PAYMENTS = Weighted(("card", "wallet", "cash"), (68, 24, 8))
RATINGS = Weighted((5, 4, 3, 2, 1), (60, 25, 9, 3, 3))
# city: (base fare, per km, per minute, minimum fare) in USD, typical speed km/h, driver id offset, fleet size
CITY_ECONOMICS = {
    "New York": ((3.0, 1.75, 0.45, 8.0), 18.0, 0, 400),
    "London": ((2.8, 1.6, 0.35, 6.5), 16.0, 400, 300),
    "Singapore": ((2.6, 0.55, 0.22, 4.0), 28.0, 700, 200),
}
WEATHER_NEXT = {
    "clear": Weighted(("cloudy", "rain"), (80, 20)),
    "cloudy": Weighted(("clear", "rain", "heavy_rain"), (50, 40, 10)),
    "rain": Weighted(("cloudy", "heavy_rain", "clear"), (50, 25, 25)),
    "heavy_rain": Weighted(("rain", "cloudy"), (70, 30)),
}


class RidesGenerator(Generator):
    name = "rides"
    title = "Ride-hailing trips"
    category = "Mobility & logistics"
    description = (
        "Completed and cancelled trips in New York, London and Singapore with pickup and drop-off coordinates, zones, "
        "distance, duration, surge pricing, tips, ratings and weather. Commute peaks and nightlife hotspots shift demand "
        "during the day."
    )
    default_rate = 1.5
    history_per_day = 8000
    key_fields = ("trip_id",)
    pattern = DailyPattern(
        [(8.5, 1.5, 0.9), (18.0, 2.0, 1.1), (23.5, 1.5, 0.5)], base=0.25, weekdays=(0.95, 0.95, 1.0, 1.0, 1.15, 1.2, 0.85)
    )
    metric = ("Average fare (USD)", "fare_amount", "avg")
    related = ("dim_zone", "dim_date")
    fields = [
        F("trip_id", "string", "id"),
        F("event_time", "timestamp", "time", "When the trip completed or was cancelled"),
        F("request_time", "timestamp", "attribute"),
        F("pickup_time", "timestamp", "attribute", "Null for cancelled trips"),
        F("dropoff_time", "timestamp", "attribute", "Null for cancelled trips"),
        F("city", "string", "dimension"),
        F("pickup_zone_id", "string", "fk", "-> dim_zone"),
        F("dropoff_zone_id", "string", "fk", "-> dim_zone"),
        F("pickup_latitude", "float", "attribute"),
        F("pickup_longitude", "float", "attribute"),
        F("dropoff_latitude", "float", "attribute"),
        F("dropoff_longitude", "float", "attribute"),
        F("distance_km", "float", "measure"),
        F("duration_min", "float", "measure"),
        F("vehicle_type", "string", "dimension"),
        F("driver_id", "string", "attribute"),
        F("rider_id", "string", "attribute"),
        F("surge_multiplier", "float", "measure"),
        F("fare_amount", "float", "measure", "USD"),
        F("tip_amount", "float", "measure", "USD"),
        F("total_amount", "float", "measure", "USD"),
        F("currency", "string", "attribute"),
        F("payment_type", "string", "dimension"),
        F("trip_status", "string", "dimension", "completed | rider_cancelled | driver_cancelled"),
        F("weather", "string", "dimension", "clear | cloudy | rain | heavy_rain"),
        F("rating", "int", "measure", "Rider rating 1-5, often not given"),
    ]
    use_cases = {
        "analyst": [
            "Map pickups and drop-offs (Power BI / Tableau maps) and find the busiest zones by hour.",
            "Analyse surge pricing versus weather and time of day.",
            "Track cancellation rate, average fare and tips by city and vehicle type.",
        ],
        "engineer": [
            "Compute live demand per zone in 5-minute windows and publish it to a dashboard.",
            "Join trips to dim_zone in a stream and write enriched, partitioned files.",
            "Validate geodata quality (coordinates, impossible speeds) with data-quality checks.",
        ],
        "scientist": [
            "Predict fare_amount and trip duration (the classic taxi regression problem).",
            "Forecast demand per zone and hour.",
            "Model the probability of a trip being cancelled.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.zones: dict[str, list[tuple]] = defaultdict(list)
        for zone in ZONES:
            self.zones[zone[2]].append(zone)
        self.weather = {city: "clear" for city in CITY_ECONOMICS}

    def _zone(self, city, hour, weekday, role):
        multipliers = {"business": 1.0, "residential": 1.0, "nightlife": 1.0, "airport": 1.0}
        if 6 <= hour < 10 and weekday < 5:
            multipliers["residential" if role == "pickup" else "business"] = 2.5
        elif 16 <= hour < 20 and weekday < 5:
            multipliers["business" if role == "pickup" else "residential"] = 2.5
        elif hour >= 21 or hour < 3:
            multipliers["nightlife" if role == "pickup" else "residential"] = 2.5
        zones = self.zones[city]
        return Weighted(zones, [z[6] * multipliers[z[3]] for z in zones]).pick(self.rng)

    def _point(self, zone):
        rng = self.rng
        lat = zone[4] + rng.gauss(0, 0.008)
        return round(lat, 6), round(zone[5] + rng.gauss(0, 0.008) / math.cos(math.radians(lat)), 6)

    def generate(self, ts):
        rng = self.rng
        city_name = CITY_SHARE.pick(rng)
        city = CITY_BY_NAME[city_name]
        if rng.random() < 0.003:
            self.weather[city_name] = WEATHER_NEXT[self.weather[city_name]].pick(rng)
        weather = self.weather[city_name]
        local = city.local_time(ts)
        hour, weekday = local.hour, local.weekday()
        (base, per_km, per_min, minimum), speed, driver_offset, fleet = CITY_ECONOMICS[city_name]

        pickup = self._zone(city_name, hour, weekday, "pickup")
        dropoff = self._zone(city_name, hour, weekday, "dropoff")
        if dropoff is pickup and rng.random() < 0.7:
            dropoff = self._zone(city_name, hour, weekday, "dropoff")
        p_lat, p_lon = self._point(pickup)
        d_lat, d_lon = self._point(dropoff)
        distance = max(0.4, haversine_km(p_lat, p_lon, d_lat, d_lon) * rng.uniform(1.15, 1.45))

        rush = weekday < 5 and (7 <= hour < 10 or 16 <= hour < 19)
        if rush:
            speed *= 0.7
        elif hour >= 22 or hour < 5:
            speed *= 1.4
        if "airport" in (pickup[3], dropoff[3]):
            speed *= 1.3
        if weather == "heavy_rain":
            speed *= 0.8
        duration = distance / speed * 60 * rng.lognormvariate(0, 0.15) + rng.uniform(1, 3)

        surge = 1.0
        if rush and rng.random() < 0.35:
            surge += rng.uniform(0.1, 0.8)
        if weather == "rain" and rng.random() < 0.5:
            surge += rng.uniform(0.2, 0.5)
        elif weather == "heavy_rain" and rng.random() < 0.7:
            surge += rng.uniform(0.4, 1.0)
        if weekday in (4, 5) and (hour >= 22 or hour < 2) and rng.random() < 0.5:
            surge += rng.uniform(0.2, 1.2)
        surge = round(min(surge, 3.0), 1)

        vehicle = VEHICLES.pick(rng)
        payment = PAYMENTS.pick(rng)
        cancel_p = 0.08 * (1 + (surge - 1)) + (0.04 if weather == "heavy_rain" else 0)
        roll = rng.random()
        status = "completed" if roll > cancel_p else ("rider_cancelled" if roll < cancel_p * 0.6 else "driver_cancelled")
        wait = rng.uniform(2, 12) * (1.4 if weather in ("rain", "heavy_rain") else 1.0)

        if status == "completed":
            fare = max(minimum, base + per_km * distance + per_min * duration) * VEHICLE_MULTIPLIER[vehicle] * surge
            tip_p = 0.15 if payment == "cash" else 0.55
            tip = fare * rng.uniform(0.08, 0.25) if rng.random() < tip_p else 0.0
            pickup_time = ts - timedelta(minutes=duration)
            request_time = pickup_time - timedelta(minutes=wait)
            dropoff_time = ts
            rating = RATINGS.pick(rng) if rng.random() < 0.75 else None
            distance_out, duration_out = round(distance, 2), round(duration, 1)
        else:
            fare = 5.0 if status == "rider_cancelled" and rng.random() < 0.5 else 0.0
            tip, rating = 0.0, None
            request_time = ts - timedelta(minutes=rng.uniform(1, 8))
            pickup_time = dropoff_time = None
            distance_out = duration_out = None
            d_lat = d_lon = None

        return [
            {
                "trip_id": make_id("TRP", ts, rng),
                "event_time": iso(ts),
                "request_time": iso(request_time),
                "pickup_time": iso(pickup_time) if pickup_time else None,
                "dropoff_time": iso(dropoff_time) if dropoff_time else None,
                "city": city_name,
                "pickup_zone_id": pickup[0],
                "dropoff_zone_id": dropoff[0],
                "pickup_latitude": p_lat,
                "pickup_longitude": p_lon,
                "dropoff_latitude": d_lat,
                "dropoff_longitude": d_lon,
                "distance_km": distance_out,
                "duration_min": duration_out,
                "vehicle_type": vehicle,
                "driver_id": f"DRV-{driver_offset + rng.randint(1, fleet):04d}",
                "rider_id": f"RDR-{rng.randint(1, 60000):06d}",
                "surge_multiplier": surge,
                "fare_amount": round(fare, 2),
                "tip_amount": round(tip, 2),
                "total_amount": round(fare + tip, 2),
                "currency": "USD",
                "payment_type": payment,
                "trip_status": status,
                "weather": weather,
                "rating": rating,
            }
        ]
