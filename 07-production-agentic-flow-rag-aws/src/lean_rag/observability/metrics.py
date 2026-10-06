"""CloudWatch metrics via Embedded Metric Format (EMF).

Writing EMF JSON to stdout lets the ECS awslogs driver ship metrics to CloudWatch without
any PutMetricData calls on the request path. Percentiles (p95 latency) come from CloudWatch.
Queue depth and DLQ size are native SQS metrics and are alarmed on in Terraform.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import defaultdict
from typing import Literal

Unit = Literal["Count", "Milliseconds", "None", "Percent"]

NAMESPACE = "LeanAgenticRAG"


class Metrics:
    """Emits EMF lines and keeps in-process counters (used by tests and the eval runner)."""

    def __init__(self, service: str, emit: bool = True) -> None:
        self.service = service
        self.emit = emit
        self._lock = threading.Lock()
        self.counters: dict[str, float] = defaultdict(float)
        self.samples: dict[str, list[float]] = defaultdict(list)

    def put(self, name: str, value: float, unit: Unit = "Count", **dimensions: str) -> None:
        with self._lock:
            self.counters[name] += value
            self.samples[name].append(value)
        if not self.emit:
            return
        dims = {"Service": self.service, **dimensions}
        record = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        # Always publish the service-level aggregate, plus the detailed series.
                        "Dimensions": [["Service"]] + ([list(dims.keys())] if dimensions else []),
                        "Metrics": [{"Name": name, "Unit": unit}],
                    }
                ],
            },
            name: value,
            **dims,
        }
        sys.stdout.write(json.dumps(record) + "\n")

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.samples.clear()
