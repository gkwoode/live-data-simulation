"""Web and app behavioural events with sessions and a purchase funnel."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from ..reference import CATALOG, CITIES
from ..schema import F
from ..util import DailyPattern, Weighted, clamp, hex_id, iso, make_id
from .base import Generator

SOURCES = Weighted(
    ("organic_search", "paid_search", "social", "email", "direct", "referral", "affiliate"), (30, 18, 16, 9, 17, 6, 4)
)
REFERRERS = {
    "organic_search": ["https://www.google.com/", "https://www.bing.com/", "https://duckduckgo.com/"],
    "paid_search": ["https://www.google.com/", "https://www.bing.com/"],
    "social": ["https://www.facebook.com/", "https://www.instagram.com/", "https://www.tiktok.com/", "https://www.linkedin.com/"],
    "email": ["https://mail.example.com/"],
    "direct": [None],
    "referral": ["https://blog.example.org/", "https://reviews.example.net/", "https://forum.example.com/"],
    "affiliate": ["https://deals.example.org/", "https://coupons.example.net/"],
}
CAMPAIGNS = {
    "paid_search": ["brand_search", "generic_electronics", "shopping_ads"],
    "social": ["holiday_teaser", "influencer_q4", "retargeting"],
    "email": ["newsletter_weekly", "abandoned_cart", "vip_early_access"],
    "affiliate": ["partner_deals"],
}
DEVICES = Weighted(("mobile", "desktop", "tablet"), (55, 37, 8))
OS_BY_DEVICE = {
    "mobile": Weighted(("iOS", "Android"), (46, 54)),
    "desktop": Weighted(("Windows", "macOS", "Linux", "ChromeOS"), (64, 27, 4, 5)),
    "tablet": Weighted(("iPadOS", "Android"), (68, 32)),
}
BROWSER_BY_OS = {
    "iOS": Weighted(("Safari", "Chrome"), (78, 22)),
    "iPadOS": Weighted(("Safari", "Chrome"), (80, 20)),
    "Android": Weighted(("Chrome", "Samsung Internet", "Firefox"), (75, 20, 5)),
    "Windows": Weighted(("Chrome", "Edge", "Firefox"), (66, 24, 10)),
    "macOS": Weighted(("Safari", "Chrome", "Firefox"), (52, 40, 8)),
    "Linux": Weighted(("Firefox", "Chrome"), (55, 45)),
    "ChromeOS": Weighted(("Chrome",), (1,)),
}
# Funnel state machine: state -> weighted next step ("exit" ends the session).
TRANSITIONS = {
    "landing": Weighted(("product_view", "category_view", "search", "exit"), (35, 25, 20, 20)),
    "category_view": Weighted(("product_view", "category_view", "search", "exit"), (55, 10, 10, 25)),
    "search": Weighted(("product_view", "search", "exit"), (55, 15, 30)),
    "product_view": Weighted(("add_to_cart", "product_view", "category_view", "search", "exit"), (18, 35, 10, 7, 30)),
    "add_to_cart": Weighted(("view_cart", "product_view", "exit"), (45, 30, 25)),
    "view_cart": Weighted(("begin_checkout", "remove_from_cart", "product_view", "exit"), (55, 8, 12, 25)),
    "remove_from_cart": Weighted(("view_cart", "product_view", "exit"), (40, 30, 30)),
    "begin_checkout": Weighted(("add_payment_info", "exit"), (70, 30)),
    "add_payment_info": Weighted(("purchase", "exit"), (80, 20)),
    "purchase": Weighted(("exit", "product_view"), (85, 15)),
}
SEARCH_TERMS = sorted({noun.lower() for *_, nouns in CATALOG for noun in nouns})
SESSION_TIMEOUT = timedelta(minutes=30)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class ClickstreamGenerator(Generator):
    name = "clickstream"
    title = "Web clickstream"
    category = "Digital analytics"
    description = (
        "Behavioural events from a shop's website and app: page and product views, searches, cart actions, checkout "
        "and purchases, grouped into sessions with traffic source, campaign, device and geography."
    )
    default_rate = 5.0
    history_per_day = 20000
    key_fields = ("event_id",)
    pattern = DailyPattern([(12.5, 1.8, 0.7), (20.5, 2.4, 1.1)], base=0.2, weekdays=(1.0, 0.95, 0.95, 1.0, 1.05, 1.2, 1.15))
    metric = ("Events per second", None, "sum")
    related = ("dim_customer", "dim_product", "dim_date")
    fields = [
        F("event_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("session_id", "string", "attribute", "Groups the events of one visit"),
        F("user_id", "string", "fk", "-> dim_customer; null while the visitor is anonymous"),
        F("anonymous_id", "string", "attribute", "Cookie / device id, present on every event (use it for identity stitching)"),
        F(
            "event_type",
            "string",
            "dimension",
            "page_view | search | product_view | add_to_cart | remove_from_cart | view_cart | begin_checkout | "
            "add_payment_info | purchase",
        ),
        F("page_url", "string", "attribute"),
        F("referrer", "string", "attribute", "External referrer on the first event, previous page afterwards"),
        F("traffic_source", "string", "dimension"),
        F("utm_campaign", "string", "dimension"),
        F("device_type", "string", "dimension"),
        F("os", "string", "dimension"),
        F("browser", "string", "dimension"),
        F("country", "string", "dimension"),
        F("city", "string", "dimension"),
        F("product_id", "string", "fk", "-> dim_product for product and cart events"),
        F("category", "string", "dimension"),
        F("search_query", "string", "text"),
        F("cart_value", "float", "measure", "Cart total after this event, USD"),
        F("revenue", "float", "measure", "Order value on purchase events, otherwise null"),
        F("event_sequence", "int", "attribute", "1-based position of the event within its session"),
    ]
    use_cases = {
        "analyst": [
            "Build a conversion funnel: product_view -> add_to_cart -> begin_checkout -> purchase, by device and traffic source.",
            "Compare campaign performance (utm_campaign) on sessions, conversion rate and revenue.",
            "Measure bounce rate and pages per session.",
        ],
        "engineer": [
            "Sessionize raw events with a 30-minute inactivity window using streaming windows in Spark or Flink.",
            "Stitch identities: map anonymous_id to user_id once a visitor logs in at checkout.",
            "Land events in a bronze table and model silver sessions and gold daily KPIs.",
        ],
        "scientist": [
            "Predict purchase propensity from the first N events of a session.",
            "Attribute conversions across traffic sources (first-touch vs last-touch).",
            "Cluster sessions into behavioural personas.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.customers = Weighted(ref.customers, ref.customer_weights)
        self.products = Weighted(ref.products, ref.product_weights)
        self.cities = Weighted(CITIES, [c.weight for c in CITIES])
        self.by_category: dict[str, list[dict]] = defaultdict(list)
        for product in ref.products:
            self.by_category[product["category"]].append(product)
        self.active: list[dict] = []
        self.avg_gap = 1.0
        self.last_ts = None
        self.calls = 0

    # -- session bookkeeping --------------------------------------------------------------

    def _observe(self, ts):
        if self.last_ts is not None:
            gap = max(1e-3, (ts - self.last_ts).total_seconds())
            self.avg_gap = 0.97 * self.avg_gap + 0.03 * gap
        self.last_ts = ts
        self.calls += 1
        if self.calls % 50 == 0:
            for session in [s for s in self.active if ts - s["last"] > SESSION_TIMEOUT]:
                self._end(session)

    def _end(self, session):
        last = self.active.pop()
        if last is not session:
            self.active[session["idx"]] = last
            last["idx"] = session["idx"]

    def _start(self, ts):
        rng = self.rng
        known = self.customers.pick(rng) if rng.random() < 0.55 else None
        if known:
            country, city = known["country"], known["city"]
        else:
            place = self.cities.pick(rng)
            country, city = place.country, place.name
        source = SOURCES.pick(rng)
        device = DEVICES.pick(rng)
        os_name = OS_BY_DEVICE[device].pick(rng)
        session = {
            "id": make_id("SES", ts, rng),
            "anon": "anon-" + hex_id(rng, 48),
            "known": known,
            "user": known["customer_id"] if known and rng.random() < 0.6 else None,
            "source": source,
            "referrer": rng.choice(REFERRERS[source]),
            "campaign": rng.choice(CAMPAIGNS[source]) if source in CAMPAIGNS else None,
            "device": device,
            "os": os_name,
            "browser": BROWSER_BY_OS[os_name].pick(rng),
            "country": country,
            "city": city,
            "state": "landing",
            "seq": 0,
            "last": ts,
            "page": None,
            "product": None,
            "category": None,
            "cart": {},
            "idx": len(self.active),
        }
        self.active.append(session)
        return session

    # -- events ------------------------------------------------------------------------

    def _event(self, s, ts, event_type, page, product=None, search=None, revenue=None):
        s["seq"] += 1
        referrer = s["referrer"] if s["seq"] == 1 else s["page"]
        s["page"], s["last"] = page, ts
        return {
            "event_id": make_id("EVT", ts, self.rng),
            "event_time": iso(ts),
            "session_id": s["id"],
            "user_id": s["user"],
            "anonymous_id": s["anon"],
            "event_type": event_type,
            "page_url": page,
            "referrer": referrer,
            "traffic_source": s["source"],
            "utm_campaign": s["campaign"],
            "device_type": s["device"],
            "os": s["os"],
            "browser": s["browser"],
            "country": s["country"],
            "city": s["city"],
            "product_id": product["product_id"] if product else None,
            "category": product["category"] if product else s["category"],
            "search_query": search,
            "cart_value": round(sum(s["cart"].values()), 2),
            "revenue": revenue,
            "event_sequence": s["seq"],
        }

    def _landing(self, s, ts):
        rng = self.rng
        roll = rng.random()
        if s["source"] in ("paid_search", "affiliate", "organic_search") and roll < 0.45:
            product = self.products.pick(rng)
            s["product"], s["category"] = product, product["category"]
            return self._event(s, ts, "product_view", f"/product/{product['product_id']}", product)
        if s["source"] in ("social", "email", "paid_search") and roll < 0.75:
            s["category"] = self.products.pick(rng)["category"]
            return self._event(s, ts, "page_view", f"/category/{_slug(s['category'])}")
        return self._event(s, ts, "page_view", rng.choice(("/", "/", "/", "/deals", "/new-arrivals")))

    def _advance(self, s, ts):
        rng = self.rng
        step = TRANSITIONS[s["state"]].pick(rng)
        if step == "remove_from_cart" and not s["cart"]:
            step = "product_view"
        if step == "begin_checkout" and not s["cart"]:
            step = "product_view"
        if step == "exit":
            self._end(s)
            return None
        s["state"] = step

        if step == "category_view":
            s["category"] = self.products.pick(rng)["category"]
            return self._event(s, ts, "page_view", f"/category/{_slug(s['category'])}")
        if step == "search":
            term = rng.choice(SEARCH_TERMS)
            return self._event(s, ts, "search", "/search?q=" + term.replace(" ", "+"), search=term)
        if step == "product_view":
            if s["category"] and rng.random() < 0.6:
                product = rng.choice(self.by_category[s["category"]])
            else:
                product = self.products.pick(rng)
            s["product"], s["category"] = product, product["category"]
            return self._event(s, ts, "product_view", f"/product/{product['product_id']}", product)
        if step == "add_to_cart":
            product = s["product"] or self.products.pick(rng)
            s["cart"][product["product_id"]] = s["cart"].get(product["product_id"], 0.0) + product["list_price"]
            return self._event(s, ts, "add_to_cart", f"/product/{product['product_id']}", product)
        if step == "view_cart":
            return self._event(s, ts, "view_cart", "/cart")
        if step == "remove_from_cart":
            product_id = rng.choice(list(s["cart"]))
            del s["cart"][product_id]
            return self._event(s, ts, "remove_from_cart", "/cart", self.ref.product_by_id[product_id])
        if step == "begin_checkout":
            if s["user"] is None and s["known"] is not None and rng.random() < 0.7:
                s["user"] = s["known"]["customer_id"]  # logs in to check out
            return self._event(s, ts, "begin_checkout", "/checkout")
        if step == "add_payment_info":
            return self._event(s, ts, "add_payment_info", "/checkout/payment")
        # purchase
        revenue = round(sum(s["cart"].values()), 2)
        event = self._event(s, ts, "purchase", "/checkout/confirmation", revenue=revenue)
        s["cart"] = {}
        return event

    def generate(self, ts):
        rng = self.rng
        self._observe(ts)
        target = clamp(20 / self.avg_gap, 1, 400)  # each visitor acts roughly every 20 seconds
        start_new = not self.active or rng.random() < (0.35 if len(self.active) < target else 0.03)
        if not start_new:
            event = self._advance(rng.choice(self.active), ts)
            if event is not None:
                return [event]
        return [self._landing(self._start(ts), ts)]
