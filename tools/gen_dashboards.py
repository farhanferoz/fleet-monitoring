#!/usr/bin/env python3
"""Write the engine's Grafana dashboards (dashboards/*.json) from one place.

Run after editing a panel here: python3 tools/gen_dashboards.py
Edit this file, never the generated JSON: the JSON is overwritten on every run.
"""

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "fleet-prometheus"}
LOKI = {"type": "loki", "uid": "fleet-loki"}
OUT = Path(__file__).resolve().parent.parent / "dashboards"


class Board:
    """One dashboard: rows and panels laid out top to bottom on Grafana's 24-column grid."""

    def __init__(self) -> None:
        """Start an empty dashboard."""
        self.panels: list[dict] = []
        self._id = 0

    def _nid(self) -> int:
        """Return the next panel id."""
        self._id += 1
        return self._id

    def row(self, title: str, y: int) -> None:
        """Add a section heading at row y."""
        self.panels.append({"id": self._nid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})

    def ts(self, title: str, x: int, y: int, targets: list[tuple[str, str]], unit: str, *, w: int = 8, h: int = 8,
           desc: str | None = None, mn: float | None = None, mx: float | None = None, ds: dict = PROM) -> None:
        """Add a time-series panel; targets are (query, legend) pairs."""
        d: dict = {"unit": unit, "custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10, "showPoints": "never", "spanNulls": False}}
        if mn is not None:
            d["min"] = mn
        if mx is not None:
            d["max"] = mx
        p = {"id": self._nid(), "type": "timeseries", "title": title, "datasource": ds, "gridPos": {"h": h, "w": w, "x": x, "y": y},
             "targets": [{"datasource": ds, "expr": e, "legendFormat": leg, "refId": chr(65 + i)} for i, (e, leg) in enumerate(targets)],
             "fieldConfig": {"defaults": d, "overrides": []},
             "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["last", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}}
        if desc:
            p["description"] = desc
        self.panels.append(p)

    def table(self, title: str, x: int, y: int, expr: str, *, w: int = 24, h: int = 8, desc: str | None = None) -> None:
        """Add a table of the latest value of each series."""
        p = {"id": self._nid(), "type": "table", "title": title, "datasource": PROM, "gridPos": {"h": h, "w": w, "x": x, "y": y},
             "targets": [{"datasource": PROM, "expr": expr, "format": "table", "instant": True, "refId": "A"}],
             "options": {"showHeader": True}, "fieldConfig": {"defaults": {}, "overrides": []},
             "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True}}}]}
        if desc:
            p["description"] = desc
        self.panels.append(p)

    def logs(self, title: str, x: int, y: int, expr: str, *, w: int = 24, h: int = 14) -> None:
        """Add a log lines panel (Loki)."""
        self.panels.append({"id": self._nid(), "type": "logs", "title": title, "datasource": LOKI, "gridPos": {"h": h, "w": w, "x": x, "y": y},
                            "targets": [{"datasource": LOKI, "expr": expr, "refId": "A"}],
                            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True}})

    def write(self, uid: str, title: str, variables: list[dict], *, refresh: str = "30s", time_from: str = "now-6h") -> None:
        """Write dashboards/<uid without the fleet- prefix>.json."""
        dash = {"uid": uid, "title": title, "tags": ["fleet"], "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
                "graphTooltip": 1, "refresh": refresh, "time": {"from": time_from, "to": "now"},
                "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                                          "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]},
                "templating": {"list": variables}, "panels": self.panels}
        path = OUT / f"{uid.removeprefix('fleet-')}.json"
        path.write_text(json.dumps(dash, indent=2) + "\n")


def machine_var(query: str, ds: dict = PROM, name: str = "instance") -> dict:
    """A multi-select 'Machine' variable filled from a label's values."""
    return {"name": name, "label": "Machine", "type": "query", "datasource": ds, "query": {"query": query, "refId": name},
            "refresh": 2, "includeAll": True, "multi": True, "current": {"selected": True, "text": "All", "value": "$__all"}, "sort": 1}


INST = 'instance=~"$instance"'


def box() -> None:
    """'This box': system, NVIDIA and AMD GPU panels for one or more machines."""
    b = Board()
    b.row("System", 0)
    b.ts("CPU busy", 0, 1, [(f'100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",{INST}}}[$__rate_interval])))', "{{instance}}")], "percent", mn=0, mx=100)
    b.ts("Load average", 8, 1, [(f"node_load1{{{INST}}}", "1 min {{instance}}"), (f"node_load5{{{INST}}}", "5 min {{instance}}"), (f"node_load15{{{INST}}}", "15 min {{instance}}")],
         "short", mn=0, desc="Runnable processes averaged over 1, 5 and 15 minutes. Compare with the number of CPU threads.")
    b.ts("Memory used", 16, 1, [(f"node_memory_MemTotal_bytes{{{INST}}} - node_memory_MemAvailable_bytes{{{INST}}}", "used {{instance}}"), (f"node_memory_MemTotal_bytes{{{INST}}}", "total {{instance}}")],
         "bytes", mn=0, desc="Used = total minus what the kernel reports as available (caches count as available).")
    fs = 'fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|fuse.*"'
    b.ts("Disk space used", 0, 9, [(f"100 * (1 - node_filesystem_avail_bytes{{{INST},{fs}}} / node_filesystem_size_bytes{{{INST},{fs}}})", "{{instance}} {{mountpoint}}")], "percent", mn=0, mx=100)
    dev = 'device!~"loop.*|ram.*|dm-.*"'
    b.ts("Disk read and write", 8, 9, [(f"rate(node_disk_read_bytes_total{{{INST},{dev}}}[$__rate_interval])", "read {{instance}} {{device}}"),
                                        (f"rate(node_disk_written_bytes_total{{{INST},{dev}}}[$__rate_interval])", "write {{instance}} {{device}}")], "Bps")
    nic = 'device!~"lo|veth.*|docker.*|br-.*"'
    b.ts("Network", 16, 9, [(f"rate(node_network_receive_bytes_total{{{INST},{nic}}}[$__rate_interval])", "in {{instance}} {{device}}"),
                             (f"rate(node_network_transmit_bytes_total{{{INST},{nic}}}[$__rate_interval])", "out {{instance}} {{device}}")], "Bps")
    b.ts("Temperatures (hwmon)", 0, 17, [(f"node_hwmon_temp_celsius{{{INST}}}", "{{instance}} {{chip}} {{sensor}}")], "celsius", w=24, h=9,
         desc="Every temperature sensor the kernel exposes (CPU, disk, Wi-Fi, GPU). Names are the kernel's chip and sensor names.")
    b.row("NVIDIA GPU (needs the nvidia role; no data otherwise)", 26)
    j = "on(instance, uuid) group_left(name)"
    b.ts("GPU utilization", 0, 27, [(f"nvidia_smi_utilization_gpu_ratio{{{INST}}} * {j} nvidia_smi_gpu_info", "{{instance}} {{name}}")], "percentunit", mn=0, mx=1)
    b.ts("GPU memory used", 8, 27, [(f"nvidia_smi_memory_used_bytes{{{INST}}} * {j} nvidia_smi_gpu_info", "used {{instance}} {{name}}"),
                                     (f"nvidia_smi_memory_total_bytes{{{INST}}} * {j} nvidia_smi_gpu_info", "total {{instance}} {{name}}")], "bytes", mn=0,
         desc="Empty on GPUs that share memory with the CPU (for example the DGX Spark's GB10): the driver reports no separate GPU memory there. Look at Memory used above.")
    b.ts("GPU temperature", 16, 27, [(f"nvidia_smi_temperature_gpu{{{INST}}} * {j} nvidia_smi_gpu_info", "{{instance}} {{name}}")], "celsius")
    b.ts("GPU power draw", 0, 35, [(f"nvidia_smi_power_draw_watts{{{INST}}} * {j} nvidia_smi_gpu_info", "{{instance}} {{name}}")], "watt", mn=0)
    b.ts("GPU graphics clock", 8, 35, [(f"nvidia_smi_clocks_current_graphics_clock_hz{{{INST}}} * {j} nvidia_smi_gpu_info", "{{instance}} {{name}}")], "hertz", mn=0)
    b.ts("GPU memory-controller utilization", 16, 35, [(f"nvidia_smi_utilization_memory_ratio{{{INST}}} * {j} nvidia_smi_gpu_info", "{{instance}} {{name}}")], "percentunit", mn=0, mx=1)
    b.row("AMD GPU (node-exporter drm collector; no data otherwise)", 43)
    b.ts("GPU busy", 0, 44, [(f"node_drm_gpu_busy_percent{{{INST}}}", "{{instance}} {{card}}")], "percent", mn=0, mx=100,
         desc="Not tested on Strix Halo. Some older integrated AMD GPUs report a value stuck at 100: https://gitlab.freedesktop.org/drm/amd/-/issues/2448")
    b.ts("VRAM used", 8, 44, [(f"node_drm_memory_vram_used_bytes{{{INST}}}", "used {{instance}} {{card}}"), (f"node_drm_memory_vram_size_bytes{{{INST}}}", "size {{instance}} {{card}}")], "bytes", mn=0,
         desc="Memory carved out for the GPU in firmware. On APUs such as Strix Halo most GPU work uses GTT (next panel) instead.")
    b.ts("GTT used (system memory the GPU maps)", 16, 44, [(f"node_drm_memory_gtt_used_bytes{{{INST}}}", "used {{instance}} {{card}}"),
                                                           (f"node_drm_memory_gtt_size_bytes{{{INST}}}", "size {{instance}} {{card}}")], "bytes", mn=0)
    b.write("fleet-box", "This box", [machine_var("label_values(node_uname_info, instance)")])


def disks() -> None:
    """'Disks': SMART health, temperature and wear from smartctl-exporter."""
    b = Board()
    b.table("Disk health now", 0, 0, f"smartctl_device_smart_status{{{INST}}}", h=7,
            desc="1 = the disk's own SMART self-assessment passes, 0 = it reports failing. Needs the smartctl role.")
    b.ts("Disk temperature", 0, 7, [(f'smartctl_device_temperature{{{INST},temperature_type="current"}}', "{{instance}} {{device}}")], "celsius", w=12)
    b.ts("SSD wear (percentage used)", 12, 7, [(f"smartctl_device_percentage_used{{{INST}}}", "{{instance}} {{device}}")], "percent", w=12, mn=0, mx=100,
         desc="The NVMe drive's own estimate of how much of its rated life is used. Over 100 is possible and not by itself a failure.")
    b.ts("Reallocated and pending sectors (hard disks)", 0, 15,
         [(f'smartctl_device_attribute{{{INST},attribute_value_type="raw",attribute_name=~"Reallocated_Sector_Ct|Current_Pending_Sector|Offline_Uncorrectable"}}',
           "{{instance}} {{device}} {{attribute_name}}")], "short", w=12, mn=0,
         desc="Counts that should stay at 0 on a healthy hard disk; a rising count is an early warning.")
    b.ts("NVMe media errors", 12, 15, [(f"smartctl_device_media_errors{{{INST}}}", "{{instance}} {{device}}")], "short", w=12, mn=0)
    b.write("fleet-disks", "Disks", [machine_var("label_values(smartctl_device, instance)")])


def containers() -> None:
    """'Containers': per-container CPU, memory and network from cAdvisor."""
    b = Board()
    c = f'{INST},name!=""'
    b.ts("CPU per container (cores)", 0, 0, [(f"sum by (instance, name) (rate(container_cpu_usage_seconds_total{{{c}}}[$__rate_interval]))", "{{instance}} {{name}}")], "short", w=12, mn=0,
         desc="1 = one CPU core fully busy. Needs the cadvisor role.")
    b.ts("Memory per container (working set)", 12, 0, [(f"container_memory_working_set_bytes{{{c}}}", "{{instance}} {{name}}")], "bytes", w=12, mn=0,
         desc="Memory the container is actively using (what the kernel would not reclaim first).")
    b.ts("Network in per container", 0, 8, [(f"sum by (instance, name) (rate(container_network_receive_bytes_total{{{c}}}[$__rate_interval]))", "{{instance}} {{name}}")], "Bps", w=12)
    b.ts("Network out per container", 12, 8, [(f"sum by (instance, name) (rate(container_network_transmit_bytes_total{{{c}}}[$__rate_interval]))", "{{instance}} {{name}}")], "Bps", w=12,
         desc="Containers on the host network show no traffic here: their traffic is the machine's own (see This box > Network).")
    b.write("fleet-containers", "Containers", [machine_var("label_values(container_last_seen, instance)")])


def logs() -> None:
    """'Logs': log volume and lines from Loki, filtered by machine and text."""
    b = Board()
    sel = '{host=~"$host"}'
    b.ts("Log lines per minute", 0, 0, [(f'sum by (host, source) (count_over_time({sel} |~ "$search" [1m]))', "{{host}} {{source}}")], "short", w=24, h=7, mn=0, ds=LOKI)
    b.logs("Log lines", 0, 7, f'{sel} |~ "$search"')
    # allValue ".+": Loki refuses a selector whose only matcher can match an empty value (".*").
    host = {"name": "host", "label": "Machine", "type": "query", "datasource": LOKI, "query": {"query": "label_values(host)", "refId": "host"},
            "refresh": 2, "includeAll": True, "allValue": ".+", "multi": True, "current": {"selected": True, "text": "All", "value": "$__all"}, "sort": 1}
    search = {"name": "search", "label": "Contains (regex)", "type": "textbox", "query": "", "current": {"value": ""}}
    b.write("fleet-logs", "Logs", [host, search], time_from="now-1h")


if __name__ == "__main__":
    box()
    disks()
    containers()
    logs()
