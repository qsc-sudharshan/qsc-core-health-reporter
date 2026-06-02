"""
main.py — QSC Core Health Reporter
Orchestrates: Grafana Cloud data collection → 12-section HTML report → Confluence publish.
Runs in GitHub Actions every Monday morning (or on-demand via workflow_dispatch).
"""

import sys
import os
from pathlib import Path

# Load .env for local development
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print(f"[Config] Loaded {env_path}")
except ImportError:
    pass

REQUIRED = [
    "GRAFANA_PROMETHEUS_URL",
    "GRAFANA_LOKI_URL",
    "GRAFANA_INSTANCE_ID",
    "GRAFANA_SERVICE_TOKEN",
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_EMAIL",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_SPACE_KEY",
    "CONFLUENCE_PARENT_PAGE_ID",
]

missing = [v for v in REQUIRED if not os.environ.get(v)]
if missing:
    print(f"[ERROR] Missing required environment variables: {', '.join(missing)}")
    sys.exit(1)

from grafana_client import GrafanaClient
from report_builder import build_html_report
from confluence_publisher import ConfluencePublisher


def main():
    core_id = os.environ.get("CORE_ID", "eternia-Core24-d447")

    print(f"\n{'=' * 65}")
    print(f"  QSC Core Health Reporter — GitHub Actions Pipeline")
    print(f"  Core   : {core_id}")
    print(f"  Period : Last 24 Hours")
    print(f"{'=' * 65}\n")

    # Step 1 — Collect from Grafana Cloud
    print("[1/3] Collecting data from Grafana Cloud...")
    client = GrafanaClient(core_id)
    data = client.collect()
    m, l = data["metrics"], data["logs"]
    print(f"  Period  : {data['period_start']} → {data['period_end']}")
    print(f"  CPU     : {m['cpu_current']}")
    print(f"  Mem     : {m['mem_util_current']}")
    print(f"  Uptime  : {m['uptime_current']}s")
    print(f"  Threads : {m['threads_current']}")
    print(f"  Fatals  : {l['fatal_count']}")

    # Step 2 — Build 12-section HTML report
    print("\n[2/3] Building 12-section HTML report...")
    html_body = build_html_report(data)
    print(f"  HTML size: {len(html_body):,} characters")

    # Step 3 — Publish to Confluence
    print("\n[3/3] Publishing to Confluence...")
    title = f"Core Health Report — {core_id} — {data['report_date']}"
    publisher = ConfluencePublisher()
    result = publisher.publish(title, html_body)

    page_id = result.get("id", "N/A")
    webui = result.get("_links", {}).get("webui", "")
    base = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
    if base.endswith("/wiki"):
        base = base[:-5]
    full_url = f"{base}{webui}" if webui else "N/A"

    print(f"\n{'=' * 65}")
    print(f"  Report published successfully!")
    print(f"  Title    : {title}")
    print(f"  Page ID  : {page_id}")
    print(f"  URL      : {full_url}")
    print(f"{'=' * 65}\n")


if __name__ == "__main__":
    main()
