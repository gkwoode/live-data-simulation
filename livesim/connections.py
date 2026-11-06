"""Ready-to-paste connection details for BI tools, languages, streaming engines and databases.

Snippets are plain templates with %TOKENS% (not f-strings) so code containing braces stays
readable. Everything is rendered with the caller's real base URL, stream and sink settings.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .schema import POWERBI_TYPES, SYSTEM_FIELDS, spark_ddl
from .sinks import mask_url
from .util import iso, utcnow

if TYPE_CHECKING:
    from .engine import Engine, StreamRuntime


def _section(
    title: str,
    *,
    text: str = "",
    steps: list[str] | None = None,
    code: str = "",
    lang: str = "",
    note: str = "",
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "text": text,
        "steps": steps or [],
        "code": code.strip("\n"),
        "lang": lang,
        "note": note,
        "table": table,
    }


def build_connections(engine: "Engine", rt: "StreamRuntime", base: str) -> dict[str, Any]:
    settings = engine.settings
    stream = rt.name
    fields = rt.fields + list(SYSTEM_FIELDS)
    host = urlsplit(base).hostname or "127.0.0.1"
    ws_base = ("wss://" if base.startswith("https://") else "ws://") + base.split("://", 1)[-1]
    db_path = engine.store.path.resolve()
    lake_path = settings.lake_dir.resolve()
    yesterday = iso(utcnow() - timedelta(days=1))[:19] + "Z"
    related = list(rt.cls.related)
    sql_cfg = engine.sinks.config["sql"]
    kafka_cfg = engine.sinks.config["kafka"]
    sql_url = sql_cfg.get("url") or ""
    sql_on = bool(engine.sinks.sql)
    kafka_on = bool(engine.sinks.kafka)
    kafka_topic = kafka_cfg.get("single_topic") or f"{kafka_cfg.get('topic_prefix') or 'livesim.'}{stream}"
    kafka_bootstrap = kafka_cfg.get("bootstrap_servers") or "localhost:19092"
    key_hint = "&api_key=YOUR_KEY" if settings.api_key else ""
    auth_note = (
        "An API key is required: send it as the X-API-Key header, as ?api_key=..., or as the password of HTTP Basic "
        "authentication (any user name). In Power BI and Excel choose Basic and paste the key as the password."
        if settings.api_key
        else "No authentication is configured. Choose Anonymous / No authentication when a tool asks."
    )

    tokens = {
        "%BASE%": base,
        "%WS%": ws_base,
        "%STREAM%": stream,
        "%TITLE%": rt.cls.title,
        "%HOST%": host,
        "%TCP%": str(settings.tcp_port),
        "%DB%": str(db_path),
        "%DB_FWD%": db_path.as_posix(),
        "%DB_URI%": db_path.as_uri() + "?mode=ro",
        "%LAKE%": lake_path.as_posix(),
        "%LAKE_WIN%": str(lake_path),
        "%SPARK_SCHEMA%": spark_ddl(fields) + ", _stream STRING",
        "%SPARK_SCHEMA_FILE%": spark_ddl(fields),
        "%YESTERDAY%": yesterday,
        "%RELATED%": ", ".join(related) or "none",
        "%KAFKA_TOPIC%": kafka_topic,
        "%KAFKA_BOOTSTRAP%": kafka_bootstrap,
        "%KEY%": key_hint,
    }

    def t(text: str) -> str:
        for token, value in tokens.items():
            text = text.replace(token, value)
        return text

    pbi_fields = {
        "columns": ["Field", "Power BI type"],
        "rows": [[f.name, POWERBI_TYPES[f.type] + (" (1 = true)" if f.type == "bool" else "")] for f in fields],
    }

    platforms: list[dict[str, Any]] = []

    def add(pid: str, name: str, group: str, summary: str, sections: list[dict[str, Any]]) -> None:
        for s in sections:
            for key in ("text", "code", "note"):
                s[key] = t(s[key])
            s["steps"] = [t(step) for step in s["steps"]]
            s["title"] = t(s["title"])
        platforms.append({"id": pid, "name": name, "group": group, "summary": t(summary), "sections": sections})

    # -- BI & spreadsheets ---------------------------------------------------------------------

    add(
        "powerbi",
        "Power BI",
        "BI & spreadsheets",
        "Import or query %TITLE% with the OData feed, CSV/JSON over HTTP, a database or a push dataset.",
        [
            _section(
                "OData feed (recommended)",
                steps=[
                    "Home > Get data > OData feed.",
                    "URL: %BASE%/odata",
                    auth_note,
                    "Tick %STREAM% plus the tables it joins to (%RELATED%), then Load. Power BI detects relationships on "
                    "matching column names such as customer_id.",
                    "Home > Refresh pulls the latest rows. The feed pages through every retained row and folds filters into "
                    "$filter.",
                ],
                code="""
let
    Source = OData.Feed("%BASE%/odata", null, [Implementation = "2.0"]),
    Data = Source{[Name = "%STREAM%", Signature = "table"]}[Data]
in
    Data""",
                lang="powerquery",
            ),
            _section(
                "Only recent data (filtered OData URL)",
                text="Paste a URL with query options to load a slice. Power Query also writes these for you when you filter "
                "columns.",
                code="%BASE%/odata/%STREAM%?$filter=event_time ge %YESTERDAY%&$orderby=event_time desc&$top=50000",
                lang="text",
            ),
            _section(
                "CSV over HTTP (Get data > Web)",
                code="""
let
    Source = Csv.Document(
        Web.Contents("%BASE%", [RelativePath = "api/v1/streams/%STREAM%/records.csv", Query = [limit = "50000"]]),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
in
    Promoted""",
                lang="powerquery",
                note="Web.Contents with RelativePath/Query keeps the source refreshable in the Power BI service (through a "
                "gateway or tunnel).",
            ),
            _section(
                "JSON API with incremental refresh",
                text="Create DateTime parameters RangeStart and RangeEnd, use this query, then set up incremental refresh on "
                "event_time.",
                code="""
let
    Source = Json.Document(Web.Contents("%BASE%", [
        RelativePath = "api/v1/streams/%STREAM%/records",
        Query = [
            since = DateTime.ToText(RangeStart, "yyyy-MM-ddTHH:mm:ss"),
            until = DateTime.ToText(RangeEnd, "yyyy-MM-ddTHH:mm:ss"),
            limit = "50000",
            order = "asc"
        ]
    ])),
    Records = Table.FromRecords(Source[records])
in
    Records""",
                lang="powerquery",
            ),
            _section(
                "DirectQuery for near-real-time visuals",
                text=(
                    "Enable the SQL database sink (Sinks page) with PostgreSQL - the Docker setup does this for you - then "
                    "connect "
                    "with Get data > PostgreSQL database, choose DirectQuery, and turn on Automatic page refresh in the "
                    "report page's "
                    "format pane. Visuals then requery every few seconds."
                ),
                code="Server: localhost:5432\nDatabase: livesim\nUser / password: livesim / livesim  (Docker defaults)",
                lang="text",
                note="SQL sink is currently "
                + ("ON: " + mask_url(sql_url) if sql_on else "off - turn it on from the Sinks page."),
            ),
            _section(
                "Real-time streaming tile (push dataset)",
                steps=[
                    "Power BI service > your workspace > New > Streaming semantic model > API.",
                    "Add the fields you want from the table below (names must match exactly), switch on Historic data "
                    "analysis if you want to build reports, then copy the Push URL.",
                    "In LiveSim open Sinks > Webhooks, choose %STREAM%, paste the Push URL, pick format 'Power BI' and list "
                    "the same fields.",
                    "Build a dashboard tile on the streaming model; it updates every couple of seconds.",
                ],
                table=pbi_fields,
                note="Microsoft is phasing out Power BI real-time streaming models in favour of Fabric Real-Time "
                "Intelligence. If your tenant cannot create one, send the Kafka sink to a Fabric Eventstream custom "
                "endpoint instead.",
            ),
            _section(
                "SQLite file via ODBC",
                steps=[
                    "Install the SQLite ODBC driver (for example from http://www.ch-werner.de/sqliteodbc/).",
                    "Get data > ODBC > Advanced and paste the connection string below.",
                ],
                code="Driver={SQLite3 ODBC Driver};Database=%DB%;",
                lang="text",
            ),
        ],
    )

    add(
        "tableau",
        "Tableau",
        "BI & spreadsheets",
        "Connect Tableau Desktop through OData, PostgreSQL, the landing-zone files or ODBC.",
        [
            _section(
                "OData connector",
                steps=[
                    "Connect > To a Server > More... > OData.",
                    "Server: %BASE%/odata/%STREAM% (repeat for each dim_* table you want and relate them on their keys).",
                    auth_note,
                    "Tableau keeps OData data as an extract: use Data > Refresh (or schedule extract refreshes) to pick up "
                    "new rows.",
                ],
                code="%BASE%/odata/%STREAM%",
                lang="text",
            ),
            _section(
                "PostgreSQL live connection",
                text="With the SQL sink writing to PostgreSQL, choose Connect > PostgreSQL and a Live connection for "
                "real-time dashboards.",
                code="Server: localhost   Port: 5432   Database: livesim   Username: livesim   Password: livesim",
                lang="text",
            ),
            _section(
                "Text files from the landing zone",
                text="Turn on the file landing zone with CSV format, then Connect > Text file, pick one file and use 'New "
                "Union' with a wildcard search to combine every part file.",
                code="%LAKE_WIN%\\%STREAM%",
                lang="text",
            ),
            _section("SQLite via Other Databases (ODBC)", code="Driver={SQLite3 ODBC Driver};Database=%DB%;", lang="text"),
        ],
    )

    add(
        "excel",
        "Excel",
        "BI & spreadsheets",
        "Live-refreshing workbooks with Power Query.",
        [
            _section(
                "OData feed",
                steps=[
                    "Data > Get Data > From Other Sources > From OData Feed.",
                    "URL: %BASE%/odata  then pick %STREAM% and any dim_* tables > Load.",
                    "Data > Queries & Connections > right-click the query > Properties > tick 'Refresh every 1 minutes' for "
                    "a self-updating sheet.",
                ],
                code="%BASE%/odata",
                lang="text",
            ),
            _section(
                "CSV from the web",
                text="Data > From Web, then paste:",
                code="%BASE%/api/v1/streams/%STREAM%/records.csv?limit=20000%KEY%",
                lang="text",
            ),
        ],
    )

    # -- code ----------------------------------------------------------------------------------

    add(
        "python",
        "Python",
        "Code & notebooks",
        "pandas, incremental loads, live consumers and direct database access.",
        [
            _section(
                "pandas in one line",
                code="""
import pandas as pd

df = pd.read_csv(
    "%BASE%/api/v1/streams/%STREAM%/records.csv?limit=20000%KEY%",
    parse_dates=["event_time", "_ingested_at"],
)
print(df.shape)
df.head()""",
                lang="python",
            ),
            _section(
                "Incremental loader with the _seq cursor",
                text="The pattern every ingestion tool uses: remember the last cursor and only fetch newer records.",
                code="""
import time
import requests

BASE = "%BASE%"
cursor = 0  # persist this between runs (file, table, Airflow Variable...)

while True:
    page = requests.get(f"{BASE}/api/v1/streams/%STREAM%/records",
                        params={"after_seq": cursor, "limit": 5000}, timeout=30).json()
    if page["records"]:
        # write page["records"] to your warehouse, parquet files, a database...
        cursor = page["next_after_seq"]
        print(f"loaded {page['count']} records, cursor is now {cursor}")
    if not page["has_more"]:
        time.sleep(5)  # caught up: poll again shortly""",
                lang="python",
            ),
            _section(
                "Live push with Server-Sent Events",
                code="""
import json
import requests

with requests.get("%BASE%/api/v1/streams/%STREAM%/sse", stream=True, timeout=(5, None)) as response:
    for line in response.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            record = json.loads(line[6:])
            print(record["_seq"], record["event_time"])""",
                lang="python",
            ),
            _section(
                "WebSocket",
                code="""
import asyncio, json
import websockets  # pip install websockets

async def main():
    async with websockets.connect("%WS%/ws?streams=%STREAM%") as ws:
        async for message in ws:
            record = json.loads(message)
            print(record["_stream"], record["_seq"])

asyncio.run(main())""",
                lang="python",
            ),
            _section(
                "Query the SQLite store directly",
                code="""
import sqlite3
import pandas as pd

con = sqlite3.connect("%DB_URI%", uri=True)  # read-only
df = pd.read_sql("SELECT * FROM %STREAM% ORDER BY _seq DESC LIMIT 1000", con)""",
                lang="python",
            ),
            _section(
                "DuckDB over the store or the landing zone",
                code="""
import duckdb

con = duckdb.connect()
con.execute("INSTALL sqlite; LOAD sqlite;")
con.execute("ATTACH '%DB_FWD%' AS live (TYPE sqlite, READ_ONLY)")
print(con.sql("SELECT COUNT(*) FROM live.%STREAM%"))

# files written by the landing zone (JSON Lines format, Hive partitions)
print(con.sql("SELECT * FROM read_json_auto('%LAKE%/%STREAM%/*/*/part-*.jsonl', hive_partitioning = true) LIMIT 5"))""",
                lang="python",
            ),
        ],
    )

    add(
        "r",
        "R",
        "Code & notebooks",
        "readr, jsonlite and DBI.",
        [
            _section(
                "CSV, JSON and SQLite",
                code="""
library(readr)
library(jsonlite)

df <- read_csv("%BASE%/api/v1/streams/%STREAM%/records.csv?limit=20000%KEY%")
page <- fromJSON("%BASE%/api/v1/streams/%STREAM%/records?limit=1000%KEY%")
head(page$records)

library(DBI)
con <- dbConnect(RSQLite::SQLite(), "%DB_FWD%", flags = RSQLite::SQLITE_RO)
dbGetQuery(con, "SELECT * FROM %STREAM% ORDER BY _seq DESC LIMIT 10")""",
                lang="r",
            ),
        ],
    )

    add(
        "javascript",
        "JavaScript",
        "Code & notebooks",
        "Browser dashboards, Node services and notebooks.",
        [
            _section(
                "EventSource, WebSocket and fetch",
                code="""
// Live records in the browser (Server-Sent Events)
const source = new EventSource("%BASE%/api/v1/streams/%STREAM%/sse");
source.onmessage = (event) => {
  const record = JSON.parse(event.data);
  console.log(record._seq, record.event_time);
};

// WebSocket (browser or Node 22+)
const ws = new WebSocket("%WS%/ws?streams=%STREAM%");
ws.onmessage = (event) => console.log(JSON.parse(event.data));

// Stored records
const page = await (await fetch("%BASE%/api/v1/streams/%STREAM%/records?limit=100")).json();
console.table(page.records);""",
                lang="javascript",
                note="Cross-origin GET requests are allowed (CORS), so pages hosted anywhere can read the data.",
            ),
        ],
    )

    # -- streaming -----------------------------------------------------------------------------

    add(
        "spark",
        "Spark & Databricks",
        "Streaming",
        "Structured Streaming from the TCP socket, the landing zone or Kafka.",
        [
            _section(
                "Socket source (TCP firehose)",
                text="The firehose on port %TCP% carries every stream as JSON lines; filter on _stream.",
                code="""
from pyspark.sql import SparkSession, functions as F

spark = SparkSession.builder.appName("livesim-%STREAM%").getOrCreate()
schema = "%SPARK_SCHEMA%"

raw = (spark.readStream.format("socket")
       .option("host", "%HOST%").option("port", %TCP%).load())

events = (raw.select(F.from_json("value", schema).alias("r")).select("r.*")
          .where(F.col("_stream") == "%STREAM%"))

per_minute = (events.withWatermark("event_time", "10 minutes")
              .groupBy(F.window("event_time", "1 minute")).count())

per_minute.writeStream.outputMode("update").format("console").start().awaitTermination()""",
                lang="python",
            ),
            _section(
                "File source over the landing zone",
                text="Turn on the file landing zone (JSON Lines). Files appear when they roll; in-progress files start with "
                "_ and are ignored.",
                code="""
events = (spark.readStream
          .schema("%SPARK_SCHEMA_FILE%")
          .json("%LAKE%/%STREAM%"))

# Databricks Auto Loader, once the folder is synced to cloud storage:
# spark.readStream.format("cloudFiles").option("cloudFiles.format", "json").load("<cloud path>/%STREAM%")""",
                lang="python",
            ),
            _section(
                "Kafka source",
                code="""
# needs the spark-sql-kafka-0-10 package that matches your Spark version
raw = (spark.readStream.format("kafka")
       .option("kafka.bootstrap.servers", "%KAFKA_BOOTSTRAP%")
       .option("subscribe", "%KAFKA_TOPIC%")
       .option("startingOffsets", "earliest").load())

events = raw.select(F.from_json(F.col("value").cast("string"), "%SPARK_SCHEMA%").alias("r")).select("r.*")""",
                lang="python",
                note="Kafka sink is currently "
                + ("ON (topic %KAFKA_TOPIC%)." if kafka_on else "off - turn it on from the Sinks page."),
            ),
        ],
    )

    add(
        "kafka",
        "Kafka",
        "Streaming",
        "Topics per stream (or one topic), JSON values, keyed by the record's business key.",
        [
            _section(
                "Connection",
                code="bootstrap.servers = %KAFKA_BOOTSTRAP%\ntopic             = %KAFKA_TOPIC%\nkey               = "
                + "|".join(rt.cls.key_fields)
                + "\nvalue             = JSON record (with a _stream field)\nheader            = stream=%STREAM%",
                lang="text",
                note="Kafka sink is currently "
                + (
                    "ON."
                    if kafka_on
                    else "off. Start Kafka with docker compose -f docker-compose.yml -f docker-compose.kafka.yml up, then "
                    "enable the sink on the Sinks page."
                ),
            ),
            _section(
                "Consume from the command line",
                code="""
# inside the Docker Kafka container
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \\
  --bootstrap-server localhost:9092 --topic %KAFKA_TOPIC% --from-beginning

# kcat from your machine
kcat -b %KAFKA_BOOTSTRAP% -t %KAFKA_TOPIC% -C -o end""",
                lang="bash",
            ),
            _section(
                "Python consumer",
                code="""
import json
from confluent_kafka import Consumer  # pip install confluent-kafka

consumer = Consumer({"bootstrap.servers": "%KAFKA_BOOTSTRAP%", "group.id": "practice", "auto.offset.reset": "earliest"})
consumer.subscribe(["%KAFKA_TOPIC%"])
while True:
    message = consumer.poll(1.0)
    if message is None or message.error():
        continue
    record = json.loads(message.value())
    print(message.key(), record["_seq"])""",
                lang="python",
            ),
            _section(
                "Azure Event Hubs, Fabric Eventstream or Confluent Cloud",
                text="Use the service's Kafka endpoint as bootstrap servers, put the hub / topic name in 'Single topic', and "
                "paste the SASL settings as extra configuration on the Sinks page.",
                code="""
{
  "security.protocol": "SASL_SSL",
  "sasl.mechanisms": "PLAIN",
  "sasl.username": "$ConnectionString",
  "sasl.password": "Endpoint=sb://<namespace>.servicebus.windows.net/;SharedAccessKeyName=<name>;SharedAccessKey=<key>"
}""",
                lang="json",
                note="Event Hubs: bootstrap servers <namespace>.servicebus.windows.net:9093. Confluent Cloud: sasl.username "
                "/ sasl.password are the API key and secret.",
            ),
        ],
    )

    add(
        "push",
        "SSE, WebSocket & TCP",
        "Streaming",
        "Raw live feeds for apps, notebooks and command-line tools.",
        [
            _section(
                "Endpoints",
                code="""
Server-Sent Events (one stream)  %BASE%/api/v1/streams/%STREAM%/sse
Server-Sent Events (several)     %BASE%/api/v1/sse?streams=%STREAM%,clickstream
WebSocket                        %WS%/ws?streams=%STREAM%
TCP JSON lines (every stream)    %HOST%:%TCP%""",
                lang="text",
                note="Push feeds deliver live data only. After a disconnect, catch up with /records?after_seq=<last _seq you "
                "saw>.",
            ),
            _section(
                "Try it from a terminal",
                code="""
curl -N %BASE%/api/v1/streams/%STREAM%/sse

# TCP: optionally send a comma-separated list of streams to filter
python -c "
import socket
s = socket.create_connection(('%HOST%', %TCP%))
s.sendall(b'%STREAM%\\n')
for line in s.makefile():
    print(line, end='')
"
""",
                lang="bash",
            ),
            _section(
                "Change a WebSocket subscription on the fly",
                code='{"subscribe": ["%STREAM%", "rides"]}   // send [] to receive every stream',
                lang="json",
            ),
        ],
    )

    # -- databases -----------------------------------------------------------------------------

    sql_sections = [
        _section(
            "Built-in SQLite store (always on)",
            text="Every stream and reference table lives in one SQLite file in WAL mode, so tools can read while LiveSim writes.",
            code="""
File          %DB%
SQLAlchemy    sqlite:///%DB_FWD%
JDBC          jdbc:sqlite:%DB_FWD%
ODBC          Driver={SQLite3 ODBC Driver};Database=%DB%;
DuckDB        ATTACH '%DB_FWD%' AS live (TYPE sqlite, READ_ONLY);""",
            lang="text",
            note="DBeaver / DataGrip / VS Code: create a SQLite connection to the file above and mark it read-only.",
        ),
    ]
    if sql_on:
        parts = urlsplit(sql_url.replace("+psycopg", "").replace("+psycopg2", ""))
        jdbc = ""
        if engine.sinks.sql.dialect == "postgresql":
            jdbc = f"\nJDBC          jdbc:postgresql://{parts.hostname}:{parts.port or 5432}{parts.path}"
        sql_sections.append(
            _section(
                "Your SQL database (SQL sink is on)",
                code=f"SQLAlchemy    {mask_url(sql_url)}{jdbc}\n"
                f"Tables        {stream} + the other streams, dim_* reference tables",
                lang="text",
            )
        )
    sql_sections.append(
        _section(
            "Send the streams to another database",
            text="Open Sinks > SQL database and paste a SQLAlchemy URL (install the driver first). Tables are created "
            "automatically and the reference tables are loaded once.",
            code="""
PostgreSQL   postgresql+psycopg://user:password@localhost:5432/livesim
SQL Server   mssql+pyodbc://user:password@localhost:1433/livesim?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
MySQL        mysql+pymysql://user:password@localhost:3306/livesim
Snowflake    snowflake://user:password@account/database/schema?warehouse=COMPUTE_WH
SQLite       sqlite:///C:/path/to/another.db""",
            lang="text",
        )
    )
    sql_sections.append(
        _section(
            "Practice query",
            code=f"""
SELECT substr(event_time, 1, 13) AS hour, COUNT(*) AS records
FROM {stream}
GROUP BY 1
ORDER BY 1 DESC
LIMIT 24;""",
            lang="sql",
        )
    )
    add(
        "databases",
        "Databases & SQL clients",
        "Databases",
        "Connection strings for the built-in store and your own database.",
        sql_sections,
    )

    # -- APIs & cloud ----------------------------------------------------------------------------

    add(
        "rest",
        "REST API",
        "APIs & cloud",
        "Every capability is an HTTP endpoint; interactive docs at /docs.",
        [
            _section(
                "Endpoints",
                table={
                    "columns": ["Method & path", "Purpose"],
                    "rows": [
                        ["GET /api/v1/streams", "Streams with configuration and live statistics"],
                        [f"GET /api/v1/streams/{stream}/records", "JSON records (after_seq, since, until, limit, fields)"],
                        [f"GET /api/v1/streams/{stream}/records.csv", "CSV export"],
                        [f"GET /api/v1/streams/{stream}/records.ndjson", "Newline-delimited JSON export"],
                        [f"GET /api/v1/streams/{stream}/latest", "Newest records from memory"],
                        [f"GET /api/v1/streams/{stream}/schema", "JSON Schema, Spark DDL, SQL DDL, Power BI types"],
                        [f"PATCH /api/v1/streams/{stream}", "Change rate, pause, seasonality, chaos"],
                        [f"POST /api/v1/streams/{stream}/backfill", "Generate history"],
                        ["GET /api/v1/reference/{table}", "Dimension tables (JSON or .csv)"],
                        ["POST /api/v1/sql", "Read-only SQL over the store"],
                        ["GET /odata", "OData v4 service (Power BI, Excel, Tableau)"],
                    ],
                },
            ),
            _section(
                "curl",
                code="""
curl "%BASE%/api/v1/streams/%STREAM%/records?limit=5"
curl "%BASE%/api/v1/streams/%STREAM%/records?after_seq=0&limit=1000"
curl -X PATCH "%BASE%/api/v1/streams/%STREAM%" -H "Content-Type: application/json" -d '{"rate": 20}'
curl -X POST "%BASE%/api/v1/streams/%STREAM%/backfill" -H "Content-Type: application/json" -d '{"days": 30}'""",
                lang="bash",
                note="Interactive documentation: %BASE%/docs  -  OpenAPI JSON: %BASE%/openapi.json. " + auth_note,
            ),
        ],
    )

    add(
        "cloud",
        "Cloud & orchestration",
        "APIs & cloud",
        "Reach LiveSim from Fabric, Databricks, Azure Data Factory, Airflow or dbt.",
        [
            _section(
                "Expose LiveSim with a tunnel",
                text="Cloud services cannot reach localhost. Start a tunnel, then restart LiveSim with the public URL and an "
                "API key so generated snippets use it.",
                code="""
cloudflared tunnel --url http://localhost:8000        # or: ngrok http 8000

# .env
LIVESIM_PUBLIC_URL=https://<your-tunnel-host>
LIVESIM_API_KEY=<long random string>""",
                lang="bash",
            ),
            _section(
                "Microsoft Fabric & Power BI service",
                text="Dataflow Gen2 or a semantic model: use the OData connector with https://<public-url>/odata and Basic "
                "authentication (any user, API key as password). For streaming, point the Kafka sink at an Eventstream "
                "custom endpoint.",
            ),
            _section(
                "Azure Data Factory / Synapse",
                text="REST linked service with base URL https://<public-url>/api/v1/streams/%STREAM%/records. Add a "
                "pagination rule that sets the query parameter after_seq from the response body's $.next_after_seq and "
                "stop when $.has_more is false. Store the last cursor to make runs incremental.",
            ),
            _section(
                "Apache Airflow",
                code="""
import pendulum, requests
from airflow.decorators import dag, task
from airflow.models import Variable

@dag(schedule="*/5 * * * *", start_date=pendulum.datetime(2026, 1, 1), catchup=False)
def livesim_%STREAM%():
    @task
    def pull():
        cursor = int(Variable.get("livesim_%STREAM%_cursor", 0))
        page = requests.get("%BASE%/api/v1/streams/%STREAM%/records",
                            params={"after_seq": cursor, "limit": 50000}, timeout=60).json()
        # load page["records"] into your warehouse here
        Variable.set("livesim_%STREAM%_cursor", page["next_after_seq"])
        return page["count"]
    pull()

livesim_%STREAM%()""",
                lang="python",
            ),
            _section(
                "dbt",
                text="Point a dbt profile at the database used by the SQL sink and declare the stream tables as sources; "
                "build staging, intermediate and mart models on top (orders + dim_customer + dim_product makes a "
                "classic star schema).",
            ),
        ],
    )

    add(
        "webhooks",
        "Webhooks",
        "APIs & cloud",
        "LiveSim POSTs batches of %STREAM% records to any HTTP endpoint.",
        [
            _section(
                "Configure",
                steps=[
                    "Sinks > Webhooks > choose %STREAM%, paste the endpoint URL and pick a payload format.",
                    "array: JSON array of records. powerbi: the same with booleans as 1/0 (Power BI push URLs). ndjson: one "
                    'record per line. envelope: {"stream", "count", "records"}.',
                    "Optionally list fields to send, the batch size and the flush interval; use 'Send test' to post one "
                    "record immediately.",
                ],
            ),
            _section(
                "Receivers to try",
                text="Power BI streaming models, Azure Functions / AWS Lambda HTTP triggers, Logic Apps, n8n, Make or Zapier "
                "catch hooks, or https://webhook.site to inspect payloads.",
            ),
        ],
    )

    return {
        "stream": stream,
        "title": rt.cls.title,
        "base_url": base,
        "auth": {"api_key_required": bool(settings.api_key), "note": auth_note},
        "platforms": platforms,
    }
