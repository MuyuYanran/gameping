"""Low-level latency probing.

Four modes:
  tcp   — TCP three-way handshake (default; works against any HTTPS port, no ICMP needed)
  icmp  — system `ping` echo request
  dns   — fresh (uncached) DNS resolution time of the target host
  http  — HTTP(S) time-to-first-byte via a HEAD request
"""
from __future__ import annotations

import http.client
import os
import re
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass

MODES = ("tcp", "icmp", "dns", "http")

_IP_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 300.0

_TIME_RE = re.compile(r"(?:time|时间)[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.I)


def resolve(host: str, ttl: float = CACHE_TTL) -> str:
    """Resolve a host to an IPv4 address, caching the result for `ttl` seconds."""
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _IP_CACHE.get(host)
        if hit and now - hit[1] < ttl:
            return hit[0]
    try:
        ip = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        try:
            ip = socket.getaddrinfo(host, None, socket.AF_INET6, socket.SOCK_STREAM)[0][4][0]
        except OSError:
            return "-"
    with _CACHE_LOCK:
        _IP_CACHE[host] = (ip, now)
    return ip


def looks_like_ip(host: str) -> bool:
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return ":" in host                       # IPv6 literal


def tcp_ping(host: str, port: int, timeout: float = 1.0,
             ip: str | None = None) -> tuple[bool, float | None]:
    """Measure TCP three-way handshake. Equivalent to application-layer RTT."""
    target = ip if ip and ip != "-" else host
    t0 = time.perf_counter()
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True, (time.perf_counter() - t0) * 1000.0
    except OSError:
        return False, None


def icmp_ping(host: str, timeout: float = 1.0) -> tuple[bool, float | None]:
    """System ping; falls back gracefully when raw sockets are unavailable."""
    win = os.name == "nt"
    wait_ms = max(200, int(timeout * 1000))
    cmd = (["ping", "-n", "1", "-w", str(wait_ms), host] if win
           else ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host])
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout + 4,
            creationflags=0x08000000 if win else 0,          # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    if proc.returncode != 0:
        return False, None
    out = proc.stdout.decode("gbk", "ignore") if win else proc.stdout.decode("utf-8", "ignore")
    match = _TIME_RE.search(out)
    return True, (float(match.group(1)) if match else (time.perf_counter() - t0) * 1000.0)


def dns_ping(host: str, timeout: float = 1.0) -> tuple[bool, float | None]:
    """Measure a fresh (uncached) DNS resolution of `host`.

    getaddrinfo has no timeout knob, so the lookup runs in a worker thread and
    the sample is reported as lost if it overruns the budget.
    """
    if looks_like_ip(host):
        return True, 0.0                          # 纯 IP 没有解析环节
    box: dict[str, object] = {}

    def work() -> None:
        t0 = time.perf_counter()
        try:
            socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            box["ms"] = (time.perf_counter() - t0) * 1000.0
        except OSError:
            box["err"] = True

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(max(timeout, 0.1) + 3.0)
    if "err" in box or "ms" not in box:
        return False, None
    return True, float(box["ms"])                 # type: ignore[arg-type]


def http_ping(host: str, port: int = 443, timeout: float = 1.0,
              path: str = "/") -> tuple[bool, float | None]:
    """Time-to-first-byte of a HEAD request (TTFB)."""
    tls = int(port) in (443, 8443)
    t0 = time.perf_counter()
    try:
        if tls:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, int(port), timeout=timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
        try:
            conn.request("HEAD", path, headers={"User-Agent": "GamePing/1.0"})
            resp = conn.getresponse()
            ms = (time.perf_counter() - t0) * 1000.0
            if resp.status >= 400 and resp.status not in (403, 405, 429):
                return False, None
            return True, ms
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return False, None


@dataclass(slots=True)
class ProbeTarget:
    id: int
    name: str
    host: str
    port: int
    proto: str = "tcp"
    group: str = ""
    ip: str = "-"

    def probe(self, timeout: float) -> tuple[bool, float | None]:
        mode = self.proto if self.proto in MODES else "tcp"
        if mode == "icmp":
            return icmp_ping(self.host, timeout)
        if mode == "dns":
            return dns_ping(self.host, timeout)
        if mode == "http":
            return http_ping(self.host, self.port, timeout)
        return tcp_ping(self.host, self.port, timeout, self.ip)

    def refresh_ip(self, ttl: float = CACHE_TTL) -> str:
        self.ip = resolve(self.host, ttl)
        return self.ip


__all__ = ["ProbeTarget", "resolve", "tcp_ping", "icmp_ping", "dns_ping",
           "http_ping", "MODES", "looks_like_ip"]
