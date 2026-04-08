import os
from datetime import datetime, timezone


def log(msg: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"{now} {msg}", flush=True)


def log_mem(tag: str):
    try:
        rss_kb = 0
        mem_avail_kb = 0

        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break

        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
                    break

        rss_mb = rss_kb / 1024
        mem_avail_mb = mem_avail_kb / 1024
        log(f"[MEM] {tag} rss_mb={rss_mb:.1f} avail_mb={mem_avail_mb:.1f}")
    except Exception as e:
        log(f"[MEM ERROR] tag={tag} error={e}")
