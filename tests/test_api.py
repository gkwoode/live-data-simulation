"""End-to-end tests of the HTTP API with the full engine running in-process."""

import base64
import csv
import io
import json
import time
import xml.dom.minidom

import pytest
from fastapi.testclient import TestClient

from livesim.app import create_app
from livesim.config import Settings


def wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError("condition not met in time")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    settings = Settings(data_dir=tmp_path_factory.mktemp("data"), history_days=0, tcp_port=0, odata_page_size=100)
    with TestClient(create_app(settings)) as c:
        response = c.post("/api/v1/streams/orders/backfill", json={"days": 1, "events": 300})
        assert response.status_code == 200
        wait_for(lambda: c.get("/api/v1/streams/orders").json()["stats"]["stored"] >= 300)
        yield c


def test_health_overview_and_stream_list(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    streams = client.get("/api/v1/streams").json()
    assert len(streams) == 10
    first = streams[0]
    assert {"config", "stats", "series", "metric", "use_cases"} <= set(first)
    assert len(first["series"]["records"]) == 120
    assert client.get("/api/v1/overview").json()["streams_total"] == 10


def test_patch_validates_and_applies(client):
    updated = client.patch("/api/v1/streams/reviews", json={"rate": 4, "chaos": {"enabled": True, "rate": 0.3}}).json()
    assert updated["config"]["rate"] == 4 and updated["config"]["chaos"]["enabled"] is True
    assert client.patch("/api/v1/streams/reviews", json={"rate": 0}).status_code == 422
    assert client.patch("/api/v1/streams/reviews", json={"rate": 99999}).status_code == 422
    assert client.patch("/api/v1/streams/nope", json={"rate": 1}).status_code == 404
    client.patch("/api/v1/streams/reviews", json={"chaos": {"enabled": False}})


def test_cursor_pagination_returns_every_record_once(client):
    seen, cursor = [], 0
    while True:
        page = client.get("/api/v1/streams/orders/records", params={"after_seq": cursor, "limit": 97}).json()
        seen.extend(r["_seq"] for r in page["records"])
        cursor = page["next_after_seq"]
        if not page["has_more"]:
            break
    assert seen == sorted(seen) and len(seen) == len(set(seen)) and len(seen) >= 300


def test_record_filters_and_formats(client):
    newest = client.get("/api/v1/streams/orders/records", params={"limit": 5, "fields": "order_id,line_total"}).json()
    assert newest["order"] == "desc" and set(newest["records"][0]) == {"order_id", "line_total", "_seq"}
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v1/streams/orders/records.csv", params={"limit": 50}).text)))
    assert len(rows) == 50 and "event_time" in rows[0]
    lines = client.get("/api/v1/streams/orders/records.ndjson", params={"limit": 3}).text.strip().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["order_id"].startswith("ORD-")
    assert client.get("/api/v1/streams/orders/records", params={"since": "yesterday"}).status_code == 400
    assert client.get("/api/v1/streams/orders/records", params={"fields": "nope"}).status_code == 400


def test_schema_and_reference(client):
    schema = client.get("/api/v1/streams/rides/schema").json()
    assert "TIMESTAMP" in schema["spark_ddl"] and schema["json_schema"]["type"] == "object"
    tables = client.get("/api/v1/reference").json()
    assert {t["name"] for t in tables} >= {"dim_customer", "dim_product", "dim_date"}
    customers = client.get("/api/v1/reference/dim_customer").json()
    assert customers["count"] == 5000
    assert client.get("/api/v1/reference/dim_store.csv").text.startswith("store_id,")


def test_odata_metadata_and_service_document(client):
    service = client.get("/odata").json()
    assert {s["name"] for s in service["value"]} >= {"orders", "dim_customer"}
    metadata = client.get("/odata/$metadata")
    assert metadata.headers["odata-version"] == "4.0"
    doc = xml.dom.minidom.parseString(metadata.text)
    assert len(doc.getElementsByTagName("EntitySet")) == 21


def test_odata_server_paging_is_complete_and_stable(client):
    total = int(client.get("/odata/orders/$count").text)
    url, rows = "/odata/orders?$select=order_id", []
    while url:
        page = client.get(url).json()
        rows.extend(page["value"])
        url = page.get("@odata.nextLink", "").replace("http://testserver", "")
    assert len(rows) >= total and len(rows) >= 300
    top = client.get("/odata/dim_customer", params={"$top": "150", "$skip": "10"}).json()
    assert len(top["value"]) == 100 and "$top=50" in top["@odata.nextLink"]


def test_odata_query_options(client):
    result = client.get(
        "/odata/orders",
        params={
            "$filter": "line_total gt 20 and channel ne 'in_store'",
            "$orderby": "line_total desc",
            "$select": "order_id,channel,line_total",
            "$top": "5",
            "$count": "true",
        },
    ).json()
    values = [r["line_total"] for r in result["value"]]
    assert values == sorted(values, reverse=True) and all(v > 20 for v in values)
    assert all(r["channel"] != "in_store" for r in result["value"])
    assert result["@odata.count"] >= len(values)
    assert client.get("/odata/dim_product('P00001')").json()["product_id"] == "P00001"
    assert client.get("/odata/orders", params={"$filter": "bogus eq 1"}).json()["error"]["code"] == "BadRequest"
    assert client.get("/odata/orders", params={"$expand": "x"}).status_code == 501


def test_sql_lab_is_read_only(client):
    ok = client.post("/api/v1/sql", json={"sql": "SELECT channel, COUNT(*) AS n FROM orders GROUP BY channel"}).json()
    assert ok["columns"] == ["channel", "n"] and ok["row_count"] >= 1
    for statement in (
        "DELETE FROM orders",
        "DROP TABLE orders",
        "ATTACH 'x.db' AS x",
        "PRAGMA journal_mode=DELETE",
        "SELECT 1; DELETE FROM orders",
    ):
        response = client.post("/api/v1/sql", json={"sql": statement})
        assert response.status_code == 400, statement
    assert client.get("/api/v1/sql/tables").json()[0]["kind"] == "stream"


def test_connections_are_filled_in(client):
    data = client.get("/api/v1/connections", params={"stream": "iot_telemetry"}).json()
    ids = {p["id"] for p in data["platforms"]}
    assert {"powerbi", "tableau", "excel", "python", "spark", "kafka", "databases", "rest"} <= ids
    text = json.dumps(data)
    assert "iot_telemetry" in text and "%STREAM%" not in text and "%BASE%" not in text


def test_guard_blocks_rebinding_and_cross_site_writes(client):
    assert client.get("/api/v1/health", headers={"host": "attacker.example"}).status_code == 403
    assert client.post("/api/v1/control/stop-all", headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/v1/streams", headers={"origin": "https://anywhere.example"}).status_code == 200


def test_sinks_status_and_validation(client):
    status = client.get("/api/v1/sinks").json()
    assert status["sqlite"]["running"] and status["lake"]["config"]["enabled"] is False
    assert client.put("/api/v1/sinks/webhooks/orders", json={"url": "ftp://example.com"}).status_code == 400
    assert client.put("/api/v1/sinks/sql", json={"enabled": True, "url": "notadialect://x"}).status_code == 400


def test_api_key_protection(tmp_path):
    settings = Settings(data_dir=tmp_path, history_days=0, tcp_port=0, api_key="s3cret", autostart=False)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/v1/health").status_code == 200
        denied = c.get("/api/v1/streams")
        assert denied.status_code == 401 and "Basic" in denied.headers["www-authenticate"]
        assert c.get("/api/v1/streams", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.get("/odata", params={"api_key": "s3cret"}).status_code == 200
        basic = base64.b64encode(b"powerbi:s3cret").decode()
        assert c.get("/odata/$metadata", headers={"Authorization": f"Basic {basic}"}).status_code == 200
        assert c.get("/api/v1/streams", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/").status_code == 200  # the dashboard shell itself is public


def test_websocket_delivers_live_records_and_resubscribes(client):
    with client.websocket_connect("/ws?streams=stock_ticks") as ws:
        record = json.loads(ws.receive_text())
        assert record["_stream"] == "stock_ticks" and "price" in record
        ws.send_text(json.dumps({"subscribe": ["app_logs"]}))
        for _ in range(500):  # ticks already queued may arrive first
            record = json.loads(ws.receive_text())
            if record["_stream"] == "app_logs":
                break
        assert record["_stream"] == "app_logs"
    assert client.get("/api/v1/sinks").json()["subscribers"].get("websocket", 0) == 0
