"""
grafana_client.py
Fetches QSC Core Health data from Grafana Cloud for the last 24 hours.

Prometheus metrics (per instance):
  process_cpu_utilization_ratio, process_memory_utilization_ratio,
  process_memory_usage_bytes, process_uptime_seconds, process_threads

Loki logs:
  service_name="stacktrace", instance="<core_id>"
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from requests.auth import HTTPBasicAuth


class GrafanaClient:
    def __init__(self, core_id: str):
        self.core_id = core_id
        self.prometheus_url = os.environ["GRAFANA_PROMETHEUS_URL"].rstrip("/")
        self.loki_url = os.environ["GRAFANA_LOKI_URL"].rstrip("/")
        self.instance_id = os.environ["GRAFANA_INSTANCE_ID"]
        # GRAFANA_SERVICE_TOKEN: token from a Grafana Service Account
        # (Grafana 9+ recommended replacement for deprecated API Keys)
        # Create at: sleestack.grafana.net → Administration → Users and access → Service accounts
        self.service_token = os.environ["GRAFANA_SERVICE_TOKEN"]
        # Grafana Cloud auth: HTTP Basic — username=instance_id, password=service_token
        self.auth = HTTPBasicAuth(self.instance_id, self.service_token)

        now = datetime.now(timezone.utc)
        self.end_time = now
        self.start_time = now - timedelta(hours=24)
        self.end_ts = int(now.timestamp())
        self.start_ts = int((now - timedelta(hours=24)).timestamp())

    # ── Prometheus ────────────────────────────────────────────────────────────

    def _prom_instant(self, query: str) -> list:
        """Run an instant PromQL query at the end of the 24h window."""
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": query, "time": self.end_ts},
                auth=self.auth,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("result", [])
        except Exception as exc:
            print(f"[Prometheus instant error] {query}: {exc}")
            return []

    def _prom_range(self, query: str, step: int = 3600) -> list:
        """Run a Prometheus range query over the 24h window with hourly steps."""
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": self.start_ts,
                    "end": self.end_ts,
                    "step": step,
                },
                auth=self.auth,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("result", [])
        except Exception as exc:
            print(f"[Prometheus range error] {query}: {exc}")
            return []

    def _instant_value(self, results: list):
        if not results:
            return None
        try:
            return float(results[0]["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            return None

    def _series_values(self, results: list) -> list:
        if not results:
            return []
        values = []
        for val in results[0].get("values", []):
            try:
                values.append(float(val[1]))
            except (ValueError, TypeError):
                pass
        return values

    # ── Loki ──────────────────────────────────────────────────────────────────

    def _loki_count(self, pattern: str = "") -> int:
        """Count Loki log lines for this core over 24h, optionally filtered by pattern."""
        base = f'{{service_name="stacktrace", instance="{self.core_id}"}}'
        query = (
            f'count_over_time({base} |= "{pattern}" [24h])'
            if pattern
            else f"count_over_time({base} [24h])"
        )
        try:
            resp = requests.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": self.start_ts,
                    "end": self.end_ts,
                    "step": 86400,
                    "limit": 5,
                },
                auth=self.auth,
                timeout=30,
            )
            resp.raise_for_status()
            results = resp.json().get("data", {}).get("result", [])
            total = 0
            for series in results:
                for _, v in series.get("values", []):
                    try:
                        total += int(float(v))
                    except (ValueError, TypeError):
                        pass
            return total
        except Exception as exc:
            print(f"[Loki count error] pattern='{pattern}': {exc}")
            return -1  # -1 signals a fetch error

    def _loki_sample_lines(self, pattern: str, limit: int = 15) -> list:
        """Fetch recent log lines matching a pattern (most recent first)."""
        query = f'{{service_name="stacktrace", instance="{self.core_id}"}} |= "{pattern}"'
        try:
            resp = requests.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": self.start_ts,
                    "end": self.end_ts,
                    "limit": limit,
                    "direction": "backward",
                },
                auth=self.auth,
                timeout=30,
            )
            resp.raise_for_status()
            lines = []
            for stream in resp.json().get("data", {}).get("result", []):
                for _ts, line in stream.get("values", []):
                    lines.append(line)
            return lines[:limit]
        except Exception as exc:
            print(f"[Loki sample error] pattern='{pattern}': {exc}")
            return []

    # ── Main collector ────────────────────────────────────────────────────────

    def collect(self) -> dict:
        """Collect all metrics and logs. Returns a structured dict for the report builder."""
        f = f'instance="{self.core_id}"'

        print("  Fetching Prometheus instant metrics...")
        cpu_cur = self._instant_value(self._prom_instant(f"process_cpu_utilization_ratio{{{f}}}"))
        mem_util_cur = self._instant_value(self._prom_instant(f"process_memory_utilization_ratio{{{f}}}"))
        mem_bytes_cur = self._instant_value(self._prom_instant(f"process_memory_usage_bytes{{{f}}}"))
        uptime_cur = self._instant_value(self._prom_instant(f"process_uptime_seconds{{{f}}}"))
        threads_cur = self._instant_value(self._prom_instant(f"process_threads{{{f}}}"))

        print("  Fetching Prometheus range series (24 × 1h steps)...")
        cpu_series = self._series_values(self._prom_range(f"process_cpu_utilization_ratio{{{f}}}"))
        mem_util_series = self._series_values(self._prom_range(f"process_memory_utilization_ratio{{{f}}}"))
        mem_bytes_series = self._series_values(self._prom_range(f"process_memory_usage_bytes{{{f}}}"))
        uptime_series = self._series_values(self._prom_range(f"process_uptime_seconds{{{f}}}"))
        threads_series = self._series_values(self._prom_range(f"process_threads{{{f}}}"))

        print("  Fetching Loki log counts...")
        fatal_count = self._loki_count("fatal")
        error_count = self._loki_count("error")
        panic_count = self._loki_count("panic")
        total_count = self._loki_count()
        sample_fatals = self._loki_sample_lines("fatal", limit=10)

        def avg(s): return sum(s) / len(s) if s else None
        def mx(s): return max(s) if s else None

        return {
            "core_id": self.core_id,
            "report_date": self.end_time.strftime("%Y-%m-%d"),
            "period_start": self.start_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "period_end": self.end_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "generated_at": self.end_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "metrics": {
                "cpu_current": cpu_cur,
                "cpu_avg": avg(cpu_series),
                "cpu_max": mx(cpu_series),
                "cpu_series": cpu_series,
                "mem_util_current": mem_util_cur,
                "mem_util_avg": avg(mem_util_series),
                "mem_util_max": mx(mem_util_series),
                "mem_util_series": mem_util_series,
                "mem_bytes_current": mem_bytes_cur,
                "mem_bytes_series": mem_bytes_series,
                "uptime_current": uptime_cur,
                "uptime_series": uptime_series,
                "threads_current": threads_cur,
                "threads_avg": avg(threads_series),
                "threads_max": mx(threads_series),
                "threads_series": threads_series,
            },
            "logs": {
                "fatal_count": fatal_count,
                "error_count": error_count,
                "panic_count": panic_count,
                "total_count": total_count,
                "sample_fatals": sample_fatals,
            },
        }
