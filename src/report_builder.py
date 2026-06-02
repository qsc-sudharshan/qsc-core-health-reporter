"""
report_builder.py
Builds the full 12-section QSC Core Health Analysis HTML report
from data collected by GrafanaClient.
"""

import html as html_lib

# ── Thresholds ────────────────────────────────────────────────────────────────
CPU_WARN = 0.70       # 70%
CPU_CRIT = 0.90       # 90%
MEM_UTIL_WARN = 0.70  # 70%
MEM_UTIL_CRIT = 0.85  # 85%
THREAD_WARN = 100
THREAD_CRIT = 200
LOG_FATAL_WARN = 1
LOG_FATAL_CRIT = 5
LEAK_GROWTH_WARN = 0.15   # 15% growth over 24h → potential leak
LEAK_GROWTH_CRIT = 0.30   # 30% growth over 24h → probable leak

# ── Color palette ─────────────────────────────────────────────────────────────
COLORS = {
    "HEALTHY":  {"bg": "#e3fcef", "fg": "#006644", "badge": "#00875a"},
    "WARNING":  {"bg": "#fff4e5", "fg": "#663d00", "badge": "#ff8b00"},
    "CRITICAL": {"bg": "#ffd5d2", "fg": "#bf2600", "badge": "#de350b"},
    "UNKNOWN":  {"bg": "#f4f5f7", "fg": "#5e6c84", "badge": "#97a0af"},
}


# ── Formatting helpers ────────────────────────────────────────────────────────

def badge(status: str) -> str:
    c = COLORS.get(status, COLORS["UNKNOWN"])
    return (
        f'<span style="background-color:{c["badge"]};color:#fff;'
        f'padding:2px 10px;border-radius:3px;font-weight:bold;font-size:11px;">'
        f'{status}</span>'
    )


def pct(v) -> str:
    return f"{v * 100:.2f}%" if v is not None else "N/A"


def human_bytes(v) -> str:
    if v is None:
        return "N/A"
    if v >= 1_073_741_824:
        return f"{v / 1_073_741_824:.2f} GB"
    if v >= 1_048_576:
        return f"{v / 1_048_576:.2f} MB"
    if v >= 1_024:
        return f"{v / 1_024:.2f} KB"
    return f"{v:.0f} B"


def human_uptime(v) -> str:
    if v is None:
        return "N/A"
    d = int(v // 86400)
    h = int((v % 86400) // 3600)
    m = int((v % 3600) // 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def fmt(v, decimals=2) -> str:
    return f"{v:.{decimals}f}" if v is not None else "N/A"


def esc(s) -> str:
    return html_lib.escape(str(s))


# ── Analysis helpers ──────────────────────────────────────────────────────────

def cpu_status(v) -> str:
    if v is None: return "UNKNOWN"
    if v >= CPU_CRIT: return "CRITICAL"
    if v >= CPU_WARN: return "WARNING"
    return "HEALTHY"


def mem_status(v) -> str:
    if v is None: return "UNKNOWN"
    if v >= MEM_UTIL_CRIT: return "CRITICAL"
    if v >= MEM_UTIL_WARN: return "WARNING"
    return "HEALTHY"


def thread_status(v) -> str:
    if v is None: return "UNKNOWN"
    if v >= THREAD_CRIT: return "CRITICAL"
    if v >= THREAD_WARN: return "WARNING"
    return "HEALTHY"


def uptime_status(v, restarts: int) -> str:
    if v is None: return "UNKNOWN"
    if restarts > 0: return "WARNING"
    if v < 3600: return "WARNING"  # less than 1h uptime
    return "HEALTHY"


def log_status(fatal: int, error: int) -> str:
    if fatal < 0: return "UNKNOWN"
    if fatal >= LOG_FATAL_CRIT: return "CRITICAL"
    if fatal >= LOG_FATAL_WARN: return "WARNING"
    return "HEALTHY"


def overall_status(statuses: list) -> str:
    if "CRITICAL" in statuses: return "CRITICAL"
    if "WARNING" in statuses: return "WARNING"
    if all(s == "HEALTHY" for s in statuses): return "HEALTHY"
    return "UNKNOWN"


def detect_restarts(series: list) -> int:
    """Count uptime drops >5 min in the series (each drop = one restart)."""
    return sum(
        1 for i in range(1, len(series))
        if series[i] < series[i - 1] - 300
    )


def detect_leak(series: list) -> dict:
    if len(series) < 4:
        return {"detected": False, "growth": None, "severity": "UNKNOWN"}
    start, end = series[0], series[-1]
    if start == 0:
        return {"detected": False, "growth": None, "severity": "UNKNOWN"}
    growth = (end - start) / start
    if growth >= LEAK_GROWTH_CRIT:
        return {"detected": True, "growth": growth, "severity": "CRITICAL"}
    if growth >= LEAK_GROWTH_WARN:
        return {"detected": True, "growth": growth, "severity": "WARNING"}
    return {"detected": False, "growth": growth, "severity": "HEALTHY"}


def cpu_trend(series: list) -> str:
    if len(series) < 4:
        return "Insufficient data"
    mid = len(series) // 2
    first = sum(series[:mid]) / mid
    second = sum(series[mid:]) / (len(series) - mid)
    diff = second - first
    if diff > 0.05: return "Rising ↑"
    if diff < -0.05: return "Falling ↓"
    return "Stable →"


def build_anomalies(m: dict, l: dict, analysis: dict) -> list:
    items = []
    if m.get("cpu_max") is not None and m["cpu_max"] >= CPU_CRIT:
        items.append(("CRITICAL", "CPU", f"CPU peaked at {pct(m['cpu_max'])} during the 24h window."))
    elif m.get("cpu_max") is not None and m["cpu_max"] >= CPU_WARN:
        items.append(("WARNING", "CPU", f"CPU peaked at {pct(m['cpu_max'])} during the 24h window."))

    if m.get("mem_util_max") is not None and m["mem_util_max"] >= MEM_UTIL_CRIT:
        items.append(("CRITICAL", "Memory", f"Memory utilization peaked at {pct(m['mem_util_max'])}."))
    elif m.get("mem_util_max") is not None and m["mem_util_max"] >= MEM_UTIL_WARN:
        items.append(("WARNING", "Memory", f"Memory utilization peaked at {pct(m['mem_util_max'])}."))

    if analysis["restarts"] > 0:
        items.append(("WARNING", "Uptime", f"Process restarted {analysis['restarts']} time(s) in the last 24 hours."))

    leak = analysis["leak"]
    if leak["detected"]:
        items.append((leak["severity"], "Memory Leak",
                       f"Memory grew {leak['growth'] * 100:.1f}% over 24h — potential memory leak."))

    fatal = l.get("fatal_count", 0)
    if fatal >= LOG_FATAL_CRIT:
        items.append(("CRITICAL", "Logs", f"{fatal} fatal error(s) detected in Loki logs."))
    elif fatal >= LOG_FATAL_WARN:
        items.append(("WARNING", "Logs", f"{fatal} fatal error(s) detected in Loki logs."))

    if not items:
        items.append(("HEALTHY", "General", "No anomalies detected in the 24h window."))
    return items


def build_actions(analysis: dict) -> list:
    acts = []
    cs, ms = analysis["cpu_st"], analysis["mem_st"]
    leak, restarts = analysis["leak"], analysis["restarts"]
    ts, ls = analysis["thread_st"], analysis["log_st"]

    if cs == "CRITICAL":
        acts.append("IMMEDIATE: Investigate CPU spike — profile running processes on the core.")
    elif cs == "WARNING":
        acts.append("Monitor CPU utilization — consider scaling or optimizing the workload.")

    if ms == "CRITICAL":
        acts.append("IMMEDIATE: Memory utilization critical — check for memory leaks or over-allocated processes.")
    elif ms == "WARNING":
        acts.append("Memory utilization elevated — monitor for further growth and review memory limits.")

    if leak["detected"]:
        acts.append(f"Memory leak suspected ({leak['growth'] * 100:.1f}% growth in 24h) — capture heap dump and review allocations.")

    if restarts > 0:
        acts.append(f"Process restarted {restarts} time(s) — review crash logs in Loki and increase restart monitoring.")

    if ts == "CRITICAL":
        acts.append("Thread count critical — risk of thread exhaustion. Review thread pool configuration.")
    elif ts == "WARNING":
        acts.append("Thread count elevated — monitor for thread leaks.")

    if ls == "CRITICAL":
        acts.append("Multiple fatal errors in logs — immediate investigation required. Review Loki stacktrace logs.")
    elif ls == "WARNING":
        acts.append("Fatal errors present — review Loki logs for root cause.")

    if not acts:
        acts.append("No immediate actions required. Continue routine monitoring.")
        acts.append("Schedule next health check for next Monday.")
    return acts


# ── HTML table helpers ────────────────────────────────────────────────────────

def trow(label: str, value: str, status: str = None) -> str:
    if status:
        c = COLORS.get(status, COLORS["UNKNOWN"])
        val_td = f'<td style="background-color:{c["bg"]};color:{c["fg"]};font-weight:bold;">{value}</td>'
    else:
        val_td = f"<td>{value}</td>"
    return f"<tr><td><strong>{label}</strong></td>{val_td}</tr>\n"


def trow3(label, value, avg_val, peak_val, st) -> str:
    return (
        f"<tr><td>{label}</td>"
        f"<td>{value}</td>"
        f"<td>{avg_val}</td>"
        f"<td>{peak_val}</td>"
        f"<td>{badge(st)}</td></tr>\n"
    )


# ── Main report builder ───────────────────────────────────────────────────────

def build_html_report(data: dict) -> str:
    m = data["metrics"]
    l = data["logs"]
    core = data["core_id"]

    # Pre-compute analysis
    restarts = detect_restarts(m.get("uptime_series", []))
    leak = detect_leak(m.get("mem_bytes_series", []))

    cpu_st = cpu_status(m.get("cpu_current"))
    mem_st = mem_status(m.get("mem_util_current"))
    thread_st = thread_status(m.get("threads_current"))
    uptime_st = uptime_status(m.get("uptime_current"), restarts)
    log_st = log_status(l.get("fatal_count", 0), l.get("error_count", 0))
    overall = overall_status([cpu_st, mem_st, thread_st, uptime_st, log_st])

    analysis = {
        "cpu_st": cpu_st, "mem_st": mem_st, "thread_st": thread_st,
        "uptime_st": uptime_st, "log_st": log_st, "overall": overall,
        "restarts": restarts, "leak": leak,
        "cpu_trend": cpu_trend(m.get("cpu_series", [])),
    }

    anomalies = build_anomalies(m, l, analysis)
    actions = build_actions(analysis)
    oc = COLORS.get(overall, COLORS["UNKNOWN"])

    mem_series = m.get("mem_bytes_series", [])
    start_mem = human_bytes(mem_series[0]) if mem_series else "N/A"
    end_mem = human_bytes(mem_series[-1]) if mem_series else "N/A"
    growth_str = f"{leak['growth'] * 100:.2f}%" if leak.get("growth") is not None else "N/A"
    leak_c = COLORS.get(leak["severity"], COLORS["UNKNOWN"])

    H = []  # HTML parts

    # ── Overall banner ────────────────────────────────────────────────────────
    H.append(f"""<div style="background-color:{oc['bg']};border-left:5px solid {oc['badge']};padding:14px 18px;margin-bottom:24px;border-radius:4px;">
<h2 style="margin:0;color:{oc['fg']};">QSC Core Health Analysis — {esc(core)}</h2>
<p style="margin:6px 0 0;color:{oc['fg']};">
  <strong>Overall Status:</strong> {badge(overall)} &nbsp;|&nbsp;
  <strong>Period:</strong> {esc(data['period_start'])} → {esc(data['period_end'])} &nbsp;|&nbsp;
  <strong>Generated:</strong> {esc(data['generated_at'])}
</p></div>
""")

    # ── 1. CORE IDENTITY ──────────────────────────────────────────────────────
    H.append(f"""<h1>1. Core Identity</h1>
<table><tbody>
{trow("Core ID", esc(core))}
{trow("Report Date", esc(data['report_date']))}
{trow("Analysis Period", "Last 24 Hours")}
{trow("Period Start", esc(data['period_start']))}
{trow("Period End", esc(data['period_end']))}
{trow("Metrics Source", "Grafana Cloud Prometheus (grafanacloud-prom)")}
{trow("Logs Source", "Grafana Cloud Loki (grafanacloud-logs)")}
{trow("Prometheus Metrics", "process_cpu_utilization_ratio, process_memory_utilization_ratio, process_memory_usage_bytes, process_uptime_seconds, process_threads")}
{trow("Loki Filter", f'service_name=&quot;stacktrace&quot;, instance=&quot;{esc(core)}&quot;')}
</tbody></table>
""")

    # ── 2. SYSTEM RESOURCE SUMMARY ────────────────────────────────────────────
    H.append("""<h1>2. System Resource Summary</h1>
<table>
<thead><tr><th>Resource</th><th>Value</th><th>Status</th></tr></thead>
<tbody>
""")
    summary = [
        ("CPU Utilization (Current)", pct(m.get("cpu_current")), cpu_st),
        ("Memory Utilization (Current)", pct(m.get("mem_util_current")), mem_st),
        ("Memory Usage (Current)", human_bytes(m.get("mem_bytes_current")), mem_st),
        ("Process Uptime", human_uptime(m.get("uptime_current")), uptime_st),
        ("Thread Count (Current)", fmt(m.get("threads_current"), 0), thread_st),
        ("Fatal Errors (24h)", str(l.get("fatal_count", "N/A")), log_st),
        ("Total Errors (24h)", str(l.get("error_count", "N/A")), log_st),
        ("Restarts Detected (24h)", str(restarts), uptime_st),
    ]
    for label, val, st in summary:
        c = COLORS.get(st, COLORS["UNKNOWN"])
        H.append(
            f'<tr><td>{label}</td>'
            f'<td style="background-color:{c["bg"]};color:{c["fg"]};font-weight:bold;">{esc(val)}</td>'
            f'<td>{badge(st)}</td></tr>\n'
        )
    H.append("</tbody></table>\n")

    # ── 3. CPU STATE BREAKDOWN ────────────────────────────────────────────────
    c = COLORS.get(cpu_st, COLORS["UNKNOWN"])
    H.append(f"""<h1>3. CPU State Breakdown</h1>
<table><tbody>
<tr><td><strong>Status</strong></td><td>{badge(cpu_st)}</td></tr>
<tr><td><strong>Current CPU</strong></td><td style="background-color:{c['bg']};color:{c['fg']};font-weight:bold;">{pct(m.get('cpu_current'))}</td></tr>
<tr><td><strong>24h Average</strong></td><td>{pct(m.get('cpu_avg'))}</td></tr>
<tr><td><strong>24h Peak</strong></td><td>{pct(m.get('cpu_max'))}</td></tr>
<tr><td><strong>Trend (24h)</strong></td><td>{esc(analysis['cpu_trend'])}</td></tr>
<tr><td><strong>Data Points</strong></td><td>{len(m.get('cpu_series', []))}</td></tr>
<tr><td><strong>Warning Threshold</strong></td><td>&gt;= 70%</td></tr>
<tr><td><strong>Critical Threshold</strong></td><td>&gt;= 90%</td></tr>
</tbody></table>
""")

    # ── 4. MEMORY STATE BREAKDOWN ─────────────────────────────────────────────
    c = COLORS.get(mem_st, COLORS["UNKNOWN"])
    H.append(f"""<h1>4. Memory State Breakdown</h1>
<table><tbody>
<tr><td><strong>Status</strong></td><td>{badge(mem_st)}</td></tr>
<tr><td><strong>Current Utilization</strong></td><td style="background-color:{c['bg']};color:{c['fg']};font-weight:bold;">{pct(m.get('mem_util_current'))}</td></tr>
<tr><td><strong>24h Average Utilization</strong></td><td>{pct(m.get('mem_util_avg'))}</td></tr>
<tr><td><strong>24h Peak Utilization</strong></td><td>{pct(m.get('mem_util_max'))}</td></tr>
<tr><td><strong>Current Memory Usage</strong></td><td>{human_bytes(m.get('mem_bytes_current'))}</td></tr>
<tr><td><strong>Warning Threshold</strong></td><td>&gt;= 70%</td></tr>
<tr><td><strong>Critical Threshold</strong></td><td>&gt;= 85%</td></tr>
</tbody></table>
""")

    # ── 5. PROCESS-LEVEL ANALYSIS ─────────────────────────────────────────────
    H.append(f"""<h1>5. Process-Level Analysis</h1>
<table>
<thead><tr><th>Metric</th><th>Current</th><th>24h Average</th><th>24h Peak</th><th>Status</th></tr></thead>
<tbody>
{trow3("CPU Utilization", pct(m.get('cpu_current')), pct(m.get('cpu_avg')), pct(m.get('cpu_max')), cpu_st)}
{trow3("Memory Utilization", pct(m.get('mem_util_current')), pct(m.get('mem_util_avg')), pct(m.get('mem_util_max')), mem_st)}
{trow3("Memory Usage", human_bytes(m.get('mem_bytes_current')), "N/A", "N/A", mem_st)}
{trow3("Process Uptime", human_uptime(m.get('uptime_current')), "N/A", "N/A", uptime_st)}
{trow3("Thread Count", fmt(m.get('threads_current'), 0), fmt(m.get('threads_avg'), 1), fmt(m.get('threads_max'), 0), thread_st)}
</tbody></table>
""")

    # ── 6. MEMORY LEAK DETECTION ──────────────────────────────────────────────
    H.append(f"""<h1>6. Memory Leak Detection</h1>
<table><tbody>
<tr><td><strong>Assessment</strong></td>
<td style="background-color:{leak_c['bg']};color:{leak_c['fg']};font-weight:bold;">
{'&#9888; Potential Leak Detected' if leak['detected'] else '&#10003; No Leak Detected'}</td></tr>
<tr><td><strong>Memory at Period Start</strong></td><td>{start_mem}</td></tr>
<tr><td><strong>Memory at Period End</strong></td><td>{end_mem}</td></tr>
<tr><td><strong>24h Growth</strong></td><td>{growth_str}</td></tr>
<tr><td><strong>Data Points</strong></td><td>{len(mem_series)}</td></tr>
<tr><td><strong>Warning Threshold</strong></td><td>&gt; 15% growth over 24h</td></tr>
<tr><td><strong>Critical Threshold</strong></td><td>&gt; 30% growth over 24h</td></tr>
</tbody></table>
""")

    # ── 7. CRASH AND STACKTRACE ANALYSIS ─────────────────────────────────────
    lc = COLORS.get(log_st, COLORS["UNKNOWN"])
    H.append(f"""<h1>7. Crash and Stacktrace Analysis</h1>
<table>
<thead><tr><th>Log Category</th><th>Count (24h)</th><th>Status</th></tr></thead>
<tbody>
<tr><td>Fatal Errors</td>
<td style="background-color:{lc['bg']};color:{lc['fg']};font-weight:bold;">{l.get('fatal_count', 'N/A')}</td>
<td>{badge(log_st)}</td></tr>
<tr><td>All Errors</td><td>{l.get('error_count', 'N/A')}</td><td>—</td></tr>
<tr><td>Panic Events</td><td>{l.get('panic_count', 'N/A')}</td><td>—</td></tr>
<tr><td>Total Log Lines</td><td>{l.get('total_count', 'N/A')}</td><td>—</td></tr>
</tbody></table>
""")
    samples = l.get("sample_fatals", [])
    if samples:
        H.append("<h2>Recent Fatal Error Samples</h2>\n<ul>\n")
        for line in samples[:10]:
            H.append(f"  <li><code>{esc(str(line)[:400])}</code></li>\n")
        H.append("</ul>\n")
    else:
        H.append("<p><em>No fatal error log samples found for this period.</em></p>\n")

    # ── 8. METRICS AND LOG CORRELATION ───────────────────────────────────────
    H.append("""<h1>8. Metrics and Log Correlation</h1>
<table><thead><tr><th>Observation</th><th>Finding</th></tr></thead><tbody>
""")
    corr = []
    if l.get("fatal_count", 0) > 0 and cpu_st in ("WARNING", "CRITICAL"):
        corr.append(("CPU + Fatal Errors",
            f"Elevated CPU ({pct(m.get('cpu_current'))}) coincides with {l['fatal_count']} fatal error(s) — likely related."))
    if l.get("fatal_count", 0) > 0 and mem_st in ("WARNING", "CRITICAL"):
        corr.append(("Memory + Fatal Errors",
            f"High memory ({pct(m.get('mem_util_current'))}) with {l['fatal_count']} fatal error(s) — possible OOM-related crash."))
    if restarts > 0:
        corr.append(("Restarts + Errors",
            f"{restarts} restart(s) detected — correlate with error timestamps in Loki for root cause."))
    if leak["detected"]:
        corr.append(("Memory Leak + Runtime",
            f"Memory grew {growth_str} — sustained leak may cause future OOM crashes."))
    if not corr:
        corr.append(("All Metrics Normal",
            "No significant correlation between resource metrics and log errors. System appears stable."))
    for obs, finding in corr:
        H.append(f"<tr><td><strong>{esc(obs)}</strong></td><td>{esc(finding)}</td></tr>\n")
    H.append("</tbody></table>\n")

    # ── 9. UPTIME AND RESTART ANALYSIS ───────────────────────────────────────
    uc = COLORS.get(uptime_st, COLORS["UNKNOWN"])
    H.append(f"""<h1>9. Uptime and Restart Analysis</h1>
<table><tbody>
<tr><td><strong>Status</strong></td><td>{badge(uptime_st)}</td></tr>
<tr><td><strong>Current Uptime</strong></td>
<td style="background-color:{uc['bg']};color:{uc['fg']};">{human_uptime(m.get('uptime_current'))}</td></tr>
<tr><td><strong>Restarts Detected (24h)</strong></td><td>{restarts}</td></tr>
<tr><td><strong>Uptime Series Points</strong></td><td>{len(m.get('uptime_series', []))}</td></tr>
</tbody></table>
""")
    if restarts == 0:
        H.append("<p>&#10003; No restarts detected in the 24-hour window. Process has been continuously running.</p>\n")
    else:
        H.append(f"<p>&#9888; {restarts} restart(s) detected based on uptime drops in the Prometheus series. Investigate crash logs in Loki.</p>\n")

    # ── 10. ANOMALIES ─────────────────────────────────────────────────────────
    H.append("""<h1>10. Anomalies</h1>
<table><thead><tr><th>Severity</th><th>Category</th><th>Description</th></tr></thead><tbody>
""")
    for sev, cat, desc in anomalies:
        H.append(f"<tr><td>{badge(sev)}</td><td><strong>{esc(cat)}</strong></td><td>{esc(desc)}</td></tr>\n")
    H.append("</tbody></table>\n")

    # ── 11. SUMMARY AND RISK ASSESSMENT ──────────────────────────────────────
    H.append(f"""<h1>11. Summary and Risk Assessment</h1>
<div style="background-color:{oc['bg']};border:2px solid {oc['badge']};padding:16px;border-radius:6px;margin:12px 0;">
  <h2 style="margin:0;color:{oc['fg']};">Overall Risk: {badge(overall)}</h2>
  <p style="color:{oc['fg']};margin:8px 0 0;">
    Core <strong>{esc(core)}</strong> — 24-hour assessment ending {esc(data['period_end'])}
  </p>
</div>
<table>
<thead><tr><th>Domain</th><th>Status</th><th>Key Values</th></tr></thead>
<tbody>
<tr><td>CPU</td><td>{badge(cpu_st)}</td><td>Current: {pct(m.get('cpu_current'))} / Peak: {pct(m.get('cpu_max'))} / Trend: {esc(analysis['cpu_trend'])}</td></tr>
<tr><td>Memory Utilization</td><td>{badge(mem_st)}</td><td>Current: {pct(m.get('mem_util_current'))} / Peak: {pct(m.get('mem_util_max'))}</td></tr>
<tr><td>Memory Usage</td><td>{badge(mem_st)}</td><td>Current: {human_bytes(m.get('mem_bytes_current'))}</td></tr>
<tr><td>Thread Count</td><td>{badge(thread_st)}</td><td>Current: {fmt(m.get('threads_current'), 0)} / Peak: {fmt(m.get('threads_max'), 0)}</td></tr>
<tr><td>Uptime &amp; Restarts</td><td>{badge(uptime_st)}</td><td>Uptime: {human_uptime(m.get('uptime_current'))} / Restarts: {restarts}</td></tr>
<tr><td>Fatal Log Errors</td><td>{badge(log_st)}</td><td>Fatal: {l.get('fatal_count', 'N/A')} / Errors: {l.get('error_count', 'N/A')} / Panics: {l.get('panic_count', 'N/A')}</td></tr>
<tr><td>Memory Leak</td><td>{badge(leak['severity'])}</td><td>{'Detected — ' + growth_str + ' growth in 24h' if leak['detected'] else 'Not detected (' + growth_str + ' growth)'}</td></tr>
</tbody></table>
""")

    # ── 12. RECOMMENDED ACTIONS ───────────────────────────────────────────────
    H.append("<h1>12. Recommended Actions</h1>\n<ul>\n")
    for act in actions:
        H.append(f"  <li>{esc(act)}</li>\n")
    H.append("</ul>\n")

    H.append(f"""<hr/>
<p style="color:#97a0af;font-size:12px;">
  Report auto-generated by GitHub Actions pipeline (qsc-core-health-reporter) on {esc(data['generated_at'])}.<br/>
  Data source: Grafana Cloud Prometheus + Loki | Core: {esc(core)}
</p>
""")

    return "".join(H)
