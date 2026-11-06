import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from livesim.chaos import ISSUES, Chaos, ChaosConfig
from livesim.generators import GENERATORS
from livesim.reference import ReferenceData

PY_TYPES = {"string": str, "int": int, "float": (int, float), "bool": bool, "timestamp": str, "date": str}


@pytest.fixture(scope="module")
def ref():
    return ReferenceData(42)


def _valid(value, field_type):
    if value is None:
        return True
    if field_type in ("int", "float") and isinstance(value, bool):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return isinstance(value, PY_TYPES[field_type])


def _run(generator, n=1500, step=0.4):
    start = datetime.now(timezone.utc) - timedelta(seconds=n * step)
    rows = []
    for i in range(n):
        rows.extend(generator.generate(start + timedelta(seconds=i * step)))
    return rows


def test_reference_data_is_deterministic_and_keys_are_unique(ref):
    again = ReferenceData(42)
    for name, table in ref.tables.items():
        assert table.rows == again.tables[name].rows, name
        keys = [row[table.key] for row in table.rows]
        assert len(keys) == len(set(keys)), f"duplicate keys in {name}"
        for row in table.rows:
            assert list(row) == [f.name for f in table.fields]
            assert all(_valid(row[f.name], f.type) for f in table.fields), name


@pytest.mark.parametrize("cls", GENERATORS, ids=lambda c: c.name)
def test_records_match_declared_schema(cls, ref):
    rows = _run(cls(ref, random.Random(1)))
    names = [f.name for f in cls.fields]
    assert rows
    for row in rows:
        assert list(row) == names
        for f in cls.fields:
            assert _valid(row[f.name], f.type), (f.name, row[f.name])
        assert row["event_time"].endswith("Z") and len(row["event_time"]) == 24


@pytest.mark.parametrize("cls", GENERATORS, ids=lambda c: c.name)
def test_metadata_is_complete(cls):
    names = {f.name for f in cls.fields}
    assert set(cls.key_fields) <= names
    label, field, agg = cls.metric
    assert label and agg in ("sum", "avg") and (field is None or field in names)
    assert set(cls.use_cases) == {"analyst", "engineer", "scientist"}
    assert cls.default_rate > 0 and cls.history_per_day > 0


def test_foreign_keys_resolve_to_reference_tables(ref):
    keys = {name: {row[t.key] for row in t.rows} for name, t in ref.tables.items()}
    by_name = {g.name: g for g in GENERATORS}
    orders = _run(by_name["orders"](ref, random.Random(2)), 500)
    assert {r["customer_id"] for r in orders} <= keys["dim_customer"]
    assert {r["product_id"] for r in orders} <= keys["dim_product"]
    assert {r["store_id"] for r in orders} <= keys["dim_store"]
    txns = _run(by_name["card_transactions"](ref, random.Random(3)), 500)
    assert {r["merchant_id"] for r in txns} <= keys["dim_merchant"]
    assert {r["account_id"] for r in txns} <= keys["dim_account"]


def test_business_ids_are_unique(ref):
    by_name = {g.name: g for g in GENERATORS}
    rows = _run(by_name["app_logs"](ref, random.Random(4)), 5000, step=0.001)
    ids = [r["log_id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_fraud_and_failures_are_present_but_rare(ref):
    by_name = {g.name: g for g in GENERATORS}
    txns = _run(by_name["card_transactions"](ref, random.Random(5)), 6000)
    fraud_share = sum(r["is_fraud"] for r in txns) / len(txns)
    assert 0.003 < fraud_share < 0.05
    readings = _run(by_name["iot_telemetry"](ref, random.Random(6)), 20000)
    states = {r["health_state"] for r in readings}
    assert {"normal", "degrading"} <= states


def test_cdc_stock_never_goes_negative(ref):
    by_name = {g.name: g for g in GENERATORS}
    rows = _run(by_name["inventory_cdc"](ref, random.Random(7)), 5000)
    assert min(r["quantity_on_hand"] for r in rows) >= 0
    lsns = [r["lsn"] for r in rows]
    assert lsns == sorted(lsns) and len(set(lsns)) == len(lsns)


@pytest.mark.parametrize("cls", GENERATORS, ids=lambda c: c.name)
def test_chaos_keeps_types_keys_and_labels(cls, ref):
    rows = _run(cls(ref, random.Random(8)), 800)
    originals = [dict(r) for r in rows]
    chaos = Chaos(cls.fields, random.Random(9))
    config = ChaosConfig(enabled=True, rate=1.0, issues=list(ISSUES))
    out = chaos.apply(rows, config) + chaos.drain()
    assert len(out) >= len(originals)
    protected = [f.name for f in cls.fields if f.role in ("id", "label")]
    for before, after in zip(originals, out[: len(originals)], strict=True):
        for f in cls.fields:
            assert _valid(after[f.name], f.type), (f.name, after[f.name])
        assert all(before[p] == after[p] for p in protected)


def test_chaos_disabled_is_a_no_op(ref):
    cls = GENERATORS[0]
    rows = _run(cls(ref, random.Random(10)), 200)
    copies = [dict(r) for r in rows]
    assert Chaos(cls.fields, random.Random(1)).apply(rows, ChaosConfig(enabled=False)) == copies
