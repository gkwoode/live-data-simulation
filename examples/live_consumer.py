"""Consume a stream in real time over Server-Sent Events, without losing records on reconnect.

Push feeds deliver live data only, so after any disconnect this consumer back-fills the gap
from the REST API using the last _seq it processed, then resumes the live feed. Standard library only.

    python examples/live_consumer.py --stream card_transactions --seconds 30
"""

import argparse
import json
import time
import urllib.parse
import urllib.request


def get_json(url: str, api_key: str | None) -> dict:
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def handle(record: dict, stats: dict) -> None:
    stats["count"] += 1
    if record.get("is_fraud"):
        print(f"  FRAUD  {record['transaction_id']}  {record['amount']:>9.2f} USD  pattern={record['fraud_pattern']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--stream", default="card_transactions")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--api-key")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    stats = {"count": 0, "caught_up": 0}
    last_seq = get_json(f"{base}/api/v1/streams/{args.stream}/records?limit=1", args.api_key)["next_after_seq"]
    deadline = time.monotonic() + args.seconds
    print(f"Listening to {args.stream} from _seq {last_seq} for {args.seconds:.0f}s ...")

    while time.monotonic() < deadline:
        # 1. catch up anything missed since last_seq (no-op on a healthy connection)
        while True:
            query = urllib.parse.urlencode({"after_seq": last_seq, "limit": 5000})
            page = get_json(f"{base}/api/v1/streams/{args.stream}/records?{query}", args.api_key)
            for record in page["records"]:
                handle(record, stats)
                stats["caught_up"] += 1
            last_seq = page["next_after_seq"]
            if not page["has_more"]:
                break
        # 2. follow the live feed, skipping anything already handled
        url = f"{base}/api/v1/streams/{args.stream}/sse" + (
            f"?api_key={urllib.parse.quote(args.api_key)}" if args.api_key else ""
        )
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                for raw in response:
                    if time.monotonic() >= deadline:
                        break
                    line = raw.decode("utf-8").rstrip("\n")
                    if not line.startswith("data: "):
                        continue
                    record = json.loads(line[6:])
                    if record["_seq"] > last_seq:
                        handle(record, stats)
                        last_seq = record["_seq"]
        except (OSError, ValueError) as exc:
            print(f"Connection lost ({exc}); catching up from _seq {last_seq} and reconnecting ...")
            time.sleep(1)

    print(f"Processed {stats['count']} records ({stats['caught_up']} via catch-up). Last _seq {last_seq}.")


if __name__ == "__main__":
    main()
