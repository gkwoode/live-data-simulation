"""Spark Structured Streaming over the LiveSim TCP firehose: per-minute revenue by channel.

    pip install pyspark
    python examples/spark_streaming.py            # LiveSim must be running (TCP port 9999)

Demonstrates JSON parsing with an explicit schema, filtering a multiplexed feed, event-time
windows and watermarks (turn on 'late' chaos for the orders stream to see late data handled).
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

SCHEMA = (
    "order_id STRING, line_number BIGINT, event_time TIMESTAMP, customer_id STRING, product_id STRING, "
    "store_id STRING, channel STRING, payment_method STRING, quantity BIGINT, unit_price DOUBLE, "
    "discount_pct DOUBLE, line_total DOUBLE, currency STRING, promo_code STRING, order_status STRING, "
    "city STRING, country STRING, _seq BIGINT, _ingested_at TIMESTAMP, _stream STRING"
)

spark = SparkSession.builder.appName("livesim-orders").config("spark.sql.shuffle.partitions", "4").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

raw = spark.readStream.format("socket").option("host", "127.0.0.1").option("port", 9999).load()
orders = raw.select(F.from_json("value", SCHEMA).alias("r")).select("r.*").where(F.col("_stream") == "orders")

revenue = (
    orders.withWatermark("event_time", "10 minutes")
    .groupBy(F.window("event_time", "1 minute"), "channel")
    # exact distinct counts are not allowed in streaming aggregations, so use the approximate one
    .agg(F.round(F.sum("line_total"), 2).alias("revenue"), F.approx_count_distinct("order_id").alias("orders"))
)

(
    revenue.writeStream.outputMode("update")
    .format("console")
    .option("truncate", False)
    .trigger(processingTime="10 seconds")
    .start()
    .awaitTermination()
)
