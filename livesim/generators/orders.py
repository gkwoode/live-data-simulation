"""Omnichannel retail sales at order-line grain."""

from __future__ import annotations

from collections import defaultdict

from ..schema import F
from ..util import DailyPattern, Weighted, iso, make_id
from .base import Generator

CHANNELS = Weighted(("web", "mobile_app", "in_store"), (45, 30, 25))
ONLINE_PAYMENTS = Weighted(
    ("credit_card", "debit_card", "paypal", "mobile_wallet", "buy_now_pay_later", "gift_card"), (42, 20, 16, 14, 6, 2)
)
STORE_PAYMENTS = Weighted(("credit_card", "debit_card", "mobile_wallet", "cash", "gift_card"), (38, 27, 18, 15, 2))
LINE_COUNTS = Weighted((1, 2, 3, 4, 5, 6), (52, 24, 12, 7, 3, 2))
PROMOS = Weighted(("WELCOME10", "SAVE15", "FLASH20", "LOYALTY5", "BUNDLE25"), (30, 25, 15, 20, 10))
PROMO_DISCOUNT = {"WELCOME10": 10.0, "SAVE15": 15.0, "FLASH20": 20.0, "LOYALTY5": 5.0, "BUNDLE25": 25.0}
STATUSES = Weighted(("completed", "cancelled", "returned"), (93, 3.5, 3.5))
MULTI_QTY_CATEGORIES = {"Grocery", "Beauty & Personal Care"}


class OrdersGenerator(Generator):
    name = "orders"
    title = "Retail orders"
    category = "Retail & e-commerce"
    description = (
        "Omnichannel sales at order-line grain: one record per product in an order. Foreign keys join to "
        "dim_customer, dim_product, dim_store and dim_date, so it is ready for star-schema modelling."
    )
    default_rate = 1.5
    history_per_day = 6000
    key_fields = ("order_id", "line_number")
    pattern = DailyPattern([(12.5, 1.6, 0.7), (20.0, 2.2, 1.2)], base=0.18, weekdays=(0.92, 0.9, 0.95, 1.0, 1.1, 1.3, 1.2))
    metric = ("Revenue per second (USD)", "line_total", "sum")
    related = ("dim_customer", "dim_product", "dim_store", "dim_date")
    fields = [
        F("order_id", "string", "id", "Order id, shared by every line of the order"),
        F("line_number", "int", "id", "Line number within the order (1..n)"),
        F("event_time", "timestamp", "time", "When the order was placed (UTC)"),
        F("customer_id", "string", "fk", "-> dim_customer"),
        F("product_id", "string", "fk", "-> dim_product"),
        F("store_id", "string", "fk", "-> dim_store (ONLINE for web and app orders)"),
        F("channel", "string", "dimension", "web | mobile_app | in_store"),
        F("payment_method", "string", "dimension"),
        F("quantity", "int", "measure"),
        F("unit_price", "float", "measure", "List price in USD"),
        F("discount_pct", "float", "measure", "Discount applied to the line, 0-100"),
        F("line_total", "float", "measure", "quantity x unit_price x (1 - discount_pct/100), USD"),
        F("currency", "string", "attribute"),
        F("promo_code", "string", "attribute", "Null when no promotion was used"),
        F("order_status", "string", "dimension", "completed | cancelled | returned"),
        F("city", "string", "dimension", "Store city for in-store orders, customer city for deliveries"),
        F("country", "string", "dimension"),
    ]
    use_cases = {
        "analyst": [
            "Build a star schema in Power BI or Tableau: relate orders to dim_customer, dim_product, dim_store and dim_date.",
            "Report revenue, average order value and gross margin (unit_cost lives in dim_product) by channel, region and "
            "category.",
            "Run RFM segmentation and cohort retention by customer signup month.",
        ],
        "engineer": [
            "Load incrementally with the _seq cursor and MERGE into a warehouse table keyed on (order_id, line_number).",
            "Roll line items up to an order-header table and deduplicate replayed events.",
            "Partition a lakehouse table by order date and build daily sales snapshots.",
        ],
        "scientist": [
            "Forecast hourly revenue using the daily and weekly seasonality (backfill 30 days first).",
            "Mine association rules (market-basket analysis) from order lines.",
            "Predict returns from channel, discount and category.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.customers = Weighted(ref.customers, ref.customer_weights)
        self.products = Weighted(ref.products, ref.product_weights)
        self.online_store = ref.stores[0]
        self.physical = [s for s in ref.stores if s["store_type"] != "Online"]
        self.stores_by_city = defaultdict(list)
        for store in self.physical:
            self.stores_by_city[store["city"]].append(store)

    def generate(self, ts):
        rng = self.rng
        customer = self.customers.pick(rng)
        channel = CHANNELS.pick(rng)
        if channel == "in_store":
            local = self.stores_by_city.get(customer["city"])
            store = rng.choice(local) if local and rng.random() < 0.9 else rng.choice(self.physical)
            city, country, payment = store["city"], store["country"], STORE_PAYMENTS.pick(rng)
        else:
            store = self.online_store
            city, country, payment = customer["city"], customer["country"], ONLINE_PAYMENTS.pick(rng)
        promo = PROMOS.pick(rng) if rng.random() < 0.15 else None
        status = STATUSES.pick(rng)
        order_id = make_id("ORD", ts, rng)
        event_time = iso(ts)

        chosen: dict[str, dict] = {}
        for _ in range(LINE_COUNTS.pick(rng)):
            product = self.products.pick(rng)
            chosen.setdefault(product["product_id"], product)

        lines = []
        for number, product in enumerate(chosen.values(), 1):
            if product["category"] in MULTI_QTY_CATEGORIES:
                quantity = rng.choices((1, 2, 3, 4, 6), (40, 28, 14, 10, 8))[0]
            else:
                quantity = rng.choices((1, 2, 3), (84, 13, 3))[0]
            if promo:
                discount = PROMO_DISCOUNT[promo]
            elif rng.random() < 0.1:
                discount = float(rng.choice((5, 10, 15, 20, 30)))
            else:
                discount = 0.0
            price = product["list_price"]
            lines.append(
                {
                    "order_id": order_id,
                    "line_number": number,
                    "event_time": event_time,
                    "customer_id": customer["customer_id"],
                    "product_id": product["product_id"],
                    "store_id": store["store_id"],
                    "channel": channel,
                    "payment_method": payment,
                    "quantity": quantity,
                    "unit_price": price,
                    "discount_pct": discount,
                    "line_total": round(quantity * price * (1 - discount / 100), 2),
                    "currency": "USD",
                    "promo_code": promo,
                    "order_status": status,
                    "city": city,
                    "country": country,
                }
            )
        return lines
