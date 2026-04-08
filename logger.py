import os
from datetime import datetime, timezone
import psutil

_process = psutil.Process(os.getpid())

def log_mem_full(tag: str):
    try:
        mem = _process.memory_info()
        rss_mb = mem.rss / 1024 / 1024

        children = _process.children(recursive=True)
        children_mem = sum(c.memory_info().rss for c in children) / 1024 / 1024

        print(
            f"[MEM FULL] {tag} "
            f"rss_mb={rss_mb:.1f} "
            f"children_mb={children_mem:.1f} "
            f"total_mb={rss_mb + children_mem:.1f}"
        )
    except Exception as e:
        print(f"[MEM FULL ERROR] {e}")

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
