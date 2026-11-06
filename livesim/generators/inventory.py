"""Change data capture (CDC) feed from an operational inventory table."""

from __future__ import annotations

from ..schema import F
from ..util import DailyPattern, Weighted, iso, make_id
from .base import Generator

TARGET_ROWS = 400
UPDATE_REASONS = Weighted(("sale", "restock", "cycle_count", "cost_change", "reorder_point_change"), (70, 10, 10, 7, 3))
ACTORS = {
    "new_listing": ("catalog-service", "a.mensah", "j.okafor"),
    "sale": ("pos-integration", "web-orders-sync"),
    "restock": ("replenishment-bot", "wms-sync"),
    "cycle_count": ("cycle-count-app", "k.tanaka", "s.patel"),
    "cost_change": ("erp-pricing-job", "m.garcia"),
    "reorder_point_change": ("planning-service",),
    "discontinued": ("catalog-service", "l.dubois"),
}


class InventoryCdcGenerator(Generator):
    name = "inventory_cdc"
    title = "Inventory CDC"
    category = "Supply chain"
    description = (
        "A change-data-capture feed (Debezium style: INSERT, UPDATE and DELETE events with full row after-images and a "
        "monotonic log sequence number) from an ERP inventory table keyed by product and warehouse."
    )
    default_rate = 1.5
    history_per_day = 5000
    key_fields = ("product_id", "warehouse_id")
    pattern = DailyPattern([(10.0, 3.0, 1.0), (15.0, 3.0, 0.8)], base=0.35, weekdays=(1.1, 1.1, 1.1, 1.1, 1.05, 0.7, 0.55))
    metric = ("Net stock change per second (units)", "quantity_delta", "sum")
    related = ("dim_product", "dim_warehouse", "dim_date")
    fields = [
        F("change_id", "string", "id"),
        F("event_time", "timestamp", "time", "Commit timestamp of the change in the source database"),
        F("lsn", "int", "attribute", "Log sequence number: strictly increasing, use it to order and deduplicate changes"),
        F("op", "string", "dimension", "INSERT | UPDATE | DELETE"),
        F("table_name", "string", "attribute"),
        F("product_id", "string", "fk", "-> dim_product (part of the source primary key)"),
        F("warehouse_id", "string", "fk", "-> dim_warehouse (part of the source primary key)"),
        F("quantity_on_hand", "int", "measure", "Row value after the change (last value for deletes)"),
        F(
            "quantity_delta",
            "int",
            "measure",
            "Change versus the previous version (initial stock for INSERT, minus remaining stock for DELETE)",
        ),
        F("reorder_point", "int", "measure"),
        F("unit_cost", "float", "measure", "USD"),
        F("row_version", "int", "attribute", "Version of the source row, starting at 1"),
        F("change_reason", "string", "dimension"),
        F("changed_by", "string", "attribute"),
    ]
    use_cases = {
        "analyst": [
            "Rebuild the current stock position per warehouse and flag items below their reorder point.",
            "Report stock movements by reason (sales, restocks, adjustments) over time.",
            "Value inventory at cost (quantity_on_hand x unit_cost) by category and region.",
        ],
        "engineer": [
            "Apply the feed with MERGE / upsert into a target table, honouring DELETE events and ordering by lsn.",
            "Build a Slowly Changing Dimension Type 2 history table with valid_from / valid_to.",
            "Make the pipeline idempotent: replays and duplicates (chaos) must not change the result.",
        ],
        "scientist": [
            "Forecast stock-outs per product and warehouse from depletion rates.",
            "Optimise reorder points from the observed demand distribution.",
            "Detect unusual adjustments (shrinkage) in cycle-count changes.",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.products = Weighted(ref.products, ref.product_weights)
        self.warehouses = [w["warehouse_id"] for w in ref.warehouses]
        self.rows: dict[tuple[str, str], dict] = {}
        self.keys: list[tuple[str, str]] = []
        self.last_lsn = 0

    def _remove(self, key):
        index = self.keys.index(key)
        self.keys[index] = self.keys[-1]
        self.keys.pop()
        del self.rows[key]

    def generate(self, ts):
        rng = self.rng
        size = len(self.rows)
        roll = rng.random()
        if size < 30 or (size < TARGET_ROWS and roll < 0.25):
            op = "INSERT"
        elif size > 50 and roll > 0.99:
            op = "DELETE"
        else:
            op = "UPDATE"

        if op == "INSERT":
            for _ in range(10):
                product = self.products.pick(rng)
                key = (product["product_id"], rng.choice(self.warehouses))
                if key not in self.rows:
                    break
            else:
                op = "UPDATE"
        if op == "INSERT":
            quantity = rng.randint(40, 600)
            row = {
                "qty": quantity,
                "reorder": rng.randint(20, 120),
                "cost": round(product["unit_cost"] * rng.uniform(0.95, 1.05), 2),
                "version": 1,
            }
            self.rows[key] = row
            self.keys.append(key)
            delta, reason = quantity, "new_listing"
        elif op == "DELETE":
            key = rng.choice(self.keys)
            row = self.rows[key]
            row["version"] += 1
            delta, reason = -row["qty"], "discontinued"
            self._remove(key)
        else:
            key = rng.choice(self.keys)
            row = self.rows[key]
            reason = "restock" if row["qty"] < row["reorder"] and rng.random() < 0.6 else UPDATE_REASONS.pick(rng)
            if reason == "sale" and row["qty"] == 0:
                reason = "restock"
            delta = 0
            if reason == "sale":
                delta = -min(row["qty"], rng.randint(1, 12))
            elif reason == "restock":
                delta = rng.randint(100, 500)
            elif reason == "cycle_count":
                delta = max(-row["qty"], rng.choice((-5, -4, -3, -2, -1, 1, 2, 3)))
            elif reason == "cost_change":
                row["cost"] = round(row["cost"] * rng.uniform(0.93, 1.08), 2)
            else:
                row["reorder"] = max(5, row["reorder"] + rng.randint(-20, 30))
            row["qty"] += delta
            row["version"] += 1

        self.last_lsn = max(self.last_lsn + 1, int(ts.timestamp() * 1000) * 100)
        return [
            {
                "change_id": make_id("CHG", ts, rng),
                "event_time": iso(ts),
                "lsn": self.last_lsn,
                "op": op,
                "table_name": "erp.inventory",
                "product_id": key[0],
                "warehouse_id": key[1],
                "quantity_on_hand": row["qty"],
                "quantity_delta": delta,
                "reorder_point": row["reorder"],
                "unit_cost": row["cost"],
                "row_version": row["version"],
                "change_reason": reason,
                "changed_by": rng.choice(ACTORS[reason]),
            }
        ]
