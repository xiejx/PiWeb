from __future__ import annotations

import time

from .config import Settings
from .executor import Executor, format_bytes
from .script_store import pins
from .system import (
    active_users,
    block_devices,
    clock_info,
    cpu_count,
    cpu_temperature,
    cpu_usage,
    dashboard_service_states,
    disk_info,
    list_processes,
    load_average,
    memory_info,
    network_interfaces,
    network_rates,
    process_count,
    system_info,
    throttled_status,
    top_partitions,
    usb_devices,
)


def collect(settings: Settings, executor: Executor) -> dict:
    memory = memory_info()
    disk = disk_info("/")
    temp = cpu_temperature(executor)
    processes = list_processes(executor)
    top = sorted(processes, key=lambda p: float(p["mem"] or 0), reverse=True)[:10]
    rates = network_rates(executor)
    network = network_interfaces(executor)
    for net in network:
        rate = rates.get(net["name"])
        net["rx_rate_h"] = format_rate(rate["rx"]) if rate else "—"
        net["tx_rate_h"] = format_rate(rate["tx"]) if rate else "—"
    return {
        "hostname": system_info(executor)["hostname"],
        "temp": temp if temp is not None else 0.0,
        "cpu": cpu_usage(),
        "load": load_average(),
        "memory": memory,
        "disk": disk,
        "swap": {
            "total": memory["swap_total"],
            "total_h": memory["swap_total_h"],
            "used": memory["swap_used"],
            "used_h": memory["swap_used_h"],
            "percent": memory["swap_percent"],
        },
        "network": network,
        "throttled": throttled_status(executor),
        "services": dashboard_service_states(settings, executor),
        "info": system_info(executor),
        "clock": clock_info(executor),
        "processes": process_count(executor),
        "top": top,
        "partitions": top_partitions(executor),
        "blocks": block_devices(executor),
        "usb": usb_devices(executor),
        "users": active_users(executor),
        "pinned": pins(settings),
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def format_rate(value: float) -> str:
    return f"{format_bytes(value)}/s"


def summary(settings: Settings, executor: Executor) -> dict:
    """Compact payload for the server-sent events live dashboard."""
    memory = memory_info()
    disk = disk_info("/")
    temperature = cpu_temperature(executor)
    rates = network_rates(executor)
    interfaces = [
        {"name": name, "rx": round(rate["rx"], 1), "tx": round(rate["tx"], 1)}
        for name, rate in sorted(rates.items())
        if name != "lo"
    ]
    return {
        "temp": round(float(temperature), 1) if temperature is not None else 0.0,
        "cpu": cpu_usage(),
        "load": load_average(),
        "cores": cpu_count(),
        "mem_used_h": memory["used_h"],
        "mem_total_h": memory["total_h"],
        "mem_percent": memory["percent"],
        "disk_used_h": disk["used_h"],
        "disk_total_h": disk["total_h"],
        "disk_percent": disk["percent"],
        "net": interfaces,
        "now": time.strftime("%H:%M:%S"),
    }
