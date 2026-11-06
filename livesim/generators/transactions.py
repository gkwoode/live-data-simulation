"""Card authorisations with realistic spending habits and labelled fraud patterns."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..reference import CITIES, MERCHANT_CATEGORY
from ..schema import F
from ..util import DailyPattern, Weighted, haversine_km, iso, make_id
from .base import Generator

ENTRY_MODES = Weighted(("chip", "contactless", "swipe"), (45, 47, 8))
FRAUD_PATTERNS = Weighted(("card_testing", "account_takeover", "impossible_travel", "spending_spree"), (30, 30, 20, 20))
HIGH_RISK = ("Electronics", "Jewelry", "Gift Cards", "Crypto Exchange")
SPREE = ("Electronics", "Jewelry", "Department Stores", "Gift Cards", "Clothing")


class CardTransactionsGenerator(Generator):
    name = "card_transactions"
    title = "Card payments (fraud)"
    category = "Banking & fintech"
    description = (
        "Card authorisations across 2,000 accounts and ~400 merchants. Each account has its own spending habits; "
        "about 1.5% of records are labelled fraud from four patterns: card testing, account takeover, impossible travel and "
        "spending sprees."
    )
    default_rate = 3.0
    history_per_day = 10000
    key_fields = ("account_id",)
    pattern = DailyPattern([(12.5, 2.0, 0.9), (18.5, 2.5, 1.0)], base=0.15, weekdays=(1.0, 1.0, 1.0, 1.0, 1.1, 1.15, 0.9))
    metric = ("Fraud share of transactions", "is_fraud", "avg")
    related = ("dim_account", "dim_customer", "dim_merchant", "dim_date")
    fields = [
        F("transaction_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("account_id", "string", "fk", "-> dim_account"),
        F("customer_id", "string", "fk", "-> dim_customer"),
        F("card_network", "string", "dimension"),
        F("merchant_id", "string", "fk", "-> dim_merchant"),
        F("merchant_name", "string", "dimension"),
        F("merchant_category", "string", "dimension"),
        F("mcc", "string", "dimension", "ISO 18245 merchant category code"),
        F("amount", "float", "measure", "USD"),
        F("currency", "string", "attribute"),
        F("channel", "string", "dimension", "in_store | online"),
        F("entry_mode", "string", "dimension", "chip | contactless | swipe | ecommerce"),
        F("city", "string", "dimension", "Merchant city in store, device (IP) city online"),
        F("country", "string", "dimension"),
        F("latitude", "float", "attribute", "Merchant location (null online)"),
        F("longitude", "float", "attribute"),
        F("distance_from_home_km", "float", "measure", "Distance between transaction location and the cardholder's home city"),
        F("is_international", "bool", "attribute"),
        F("auth_result", "string", "dimension", "approved | declined"),
        F("is_fraud", "bool", "label", "Ground truth fraud label"),
        F(
            "fraud_pattern",
            "string",
            "label",
            "card_testing | account_takeover | impossible_travel | spending_spree (null if legitimate)",
        ),
    ]
    use_cases = {
        "analyst": [
            "Build a fraud monitoring dashboard: fraud rate, losses and decline rate by merchant category and country.",
            "Map transactions by location and highlight international and high-distance activity.",
            "Profile customer spend by category with dim_customer demographics.",
        ],
        "engineer": [
            "Compute per-account velocity features (count and sum over 1, 10 and 60 minutes) in a streaming job.",
            "Detect impossible travel by comparing consecutive transactions per account (stateful processing).",
            "Score transactions in real time and route suspected fraud to a webhook.",
        ],
        "scientist": [
            "Train an imbalanced classifier (is_fraud) with precision-recall evaluation and cost-sensitive thresholds.",
            "Engineer behavioural features: deviation from the account's typical amount and category mix.",
            "Explain models with SHAP and analyse errors per fraud_pattern.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.accounts = Weighted(ref.accounts, [ref.account_profiles[a["account_id"]]["activity"] for a in ref.accounts])
        self.local: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.online: dict[str, list[dict]] = defaultdict(list)
        for merchant in ref.merchants:
            if merchant["is_online"]:
                self.online[merchant["category"]].append(merchant)
            else:
                self.local[(merchant["category"], merchant["city"])].append(merchant)

    def _merchant(self, category, city, online):
        local = self.local.get((category, city.name))
        if online or not local:
            return self.rng.choice(self.online[category])
        return self.rng.choice(local)

    def _amount(self, category, spend_factor, sigma=0.65):
        median = MERCHANT_CATEGORY[category][2]
        return median * spend_factor * self.rng.lognormvariate(0, sigma)

    def _far_city(self, home):
        options = [c for c in CITIES if c.region != home.region]
        return self.rng.choice(options)

    def _record(self, ts, account, profile, merchant, amount, location, fraud=None, decline_p=0.025):
        rng = self.rng
        home = profile["city"]
        online = merchant["is_online"]
        if online:
            lat, lon = None, None
            distance = haversine_km(home.lat, home.lon, location.lat, location.lon)
        else:
            lat, lon = merchant["latitude"], merchant["longitude"]
            distance = haversine_km(home.lat, home.lon, lat, lon)
        return {
            "transaction_id": make_id("TXN", ts, rng),
            "event_time": iso(ts),
            "account_id": account["account_id"],
            "customer_id": account["customer_id"],
            "card_network": account["card_network"],
            "merchant_id": merchant["merchant_id"],
            "merchant_name": merchant["merchant_name"],
            "merchant_category": merchant["category"],
            "mcc": merchant["mcc"],
            "amount": round(max(amount, 0.5), 2),
            "currency": "USD",
            "channel": "online" if online else "in_store",
            "entry_mode": "ecommerce" if online else ENTRY_MODES.pick(rng),
            "city": location.name if online else merchant["city"],
            "country": location.country if online else merchant["country"],
            "latitude": lat,
            "longitude": lon,
            "distance_from_home_km": round(distance, 1),
            "is_international": (location.country if online else merchant["country"]) != account["home_country"],
            "auth_result": "declined" if rng.random() < decline_p else "approved",
            "is_fraud": fraud is not None,
            "fraud_pattern": fraud,
        }

    def _legit(self, ts, account=None):
        rng = self.rng
        account = account or self.accounts.pick(rng)
        profile = self.ref.account_profiles[account["account_id"]]
        category = profile["categories"].pick(rng)
        city = profile["city"] if rng.random() > 0.03 else rng.choice(CITIES)  # legitimate travel
        merchant = self._merchant(category, city, rng.random() < MERCHANT_CATEGORY[category][3])
        amount = self._amount(category, profile["spend_factor"])
        if rng.random() < 0.01:
            amount *= rng.uniform(4, 9)  # occasional legitimate big-ticket purchase
        return self._record(ts, account, profile, merchant, amount, city)

    def _fraud(self, ts):
        rng = self.rng
        account = rng.choice(self.ref.accounts)
        profile = self.ref.account_profiles[account["account_id"]]
        home = profile["city"]
        pattern = FRAUD_PATTERNS.pick(rng)

        if pattern == "card_testing":  # burst of tiny online authorisations from one place
            attacker = rng.choice(CITIES)
            merchant = rng.choice(self.online["Digital Goods"])
            count = rng.randint(3, 7)
            gaps = sorted(rng.uniform(2, 20) * k for k in range(count))
            return [
                self._record(
                    ts - timedelta(seconds=gaps[-1] - gap),
                    account,
                    profile,
                    merchant,
                    rng.uniform(0.5, 4.99),
                    attacker,
                    pattern,
                    0.4,
                )
                for gap in gaps
            ]
        if pattern == "account_takeover":  # big purchase in a high-risk category, usually online
            category = rng.choice(HIGH_RISK)
            location = self._far_city(home) if rng.random() < 0.7 else home
            merchant = self._merchant(category, location, rng.random() < 0.75)
            amount = self._amount(category, profile["spend_factor"], 0.3) * rng.uniform(4, 15)
            return [self._record(ts, account, profile, merchant, amount, location, pattern, 0.3)]
        if pattern == "impossible_travel":  # a normal purchase at home, then a card-present one far away
            earlier = self._legit(ts - timedelta(minutes=rng.uniform(10, 50)), account)
            far = self._far_city(home)
            category = rng.choice(("Electronics", "Department Stores", "Jewelry", "Fuel"))
            merchant = self._merchant(category, far, online=False)
            amount = self._amount(category, profile["spend_factor"], 0.4) * rng.uniform(1.5, 5)
            return [earlier, self._record(ts, account, profile, merchant, amount, far, pattern, 0.25)]
        # spending_spree: several large purchases in quick succession
        location = home if rng.random() < 0.5 else self._far_city(home)
        count = rng.randint(2, 4)
        out = []
        for k in range(count):
            category = rng.choice(SPREE)
            merchant = self._merchant(category, location, rng.random() < 0.4)
            amount = self._amount(category, profile["spend_factor"], 0.4) * rng.uniform(3, 8)
            when = ts - timedelta(seconds=(count - 1 - k) * rng.uniform(30, 240))
            out.append(self._record(when, account, profile, merchant, amount, location, pattern, 0.2))
        return out

    def generate(self, ts):
        if self.rng.random() < 0.006:
            return self._fraud(ts)
        return [self._legit(ts)]
