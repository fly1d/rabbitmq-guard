#!/usr/bin/env python3
"""Create a harmless local queue with messages and no consumers via the HTTP API."""

import argparse
import base64
import json
import time
from urllib.parse import quote
from urllib.request import Request, urlopen


def request_json(url, username, password, method="GET", payload=None):
    credentials = base64.b64encode("{}:{}".format(username, password).encode()).decode()
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": "Basic " + credentials,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=10) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:15673")
    parser.add_argument("--username", default="guard")
    parser.add_argument("--password", default="guard-local-only")
    parser.add_argument("--queue", default="guard.demo.no-consumers")
    parser.add_argument("--count", type=int, default=250)
    args = parser.parse_args()

    queue_name = quote(args.queue, safe="")
    request_json(
        args.url.rstrip("/") + "/api/queues/%2F/" + queue_name,
        args.username,
        args.password,
        method="PUT",
        payload={"auto_delete": False, "durable": True, "arguments": {}},
    )
    publish_url = args.url.rstrip("/") + "/api/exchanges/%2F/amq.default/publish"
    for index in range(args.count):
        result = request_json(
            publish_url,
            args.username,
            args.password,
            method="POST",
            payload={
                "properties": {"content_type": "application/json"},
                "routing_key": args.queue,
                "payload": json.dumps({"demo": True, "sequence": index}),
                "payload_encoding": "string",
            },
        )
        if not result or not result.get("routed"):
            raise RuntimeError("message {} was not routed".format(index))
    time.sleep(5)
    print("created queue {!r} with {} ready messages and no consumers".format(args.queue, args.count))


if __name__ == "__main__":
    main()
