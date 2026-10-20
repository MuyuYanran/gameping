"""Statistical analysis of latency samples: pure functions, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean, median, pstdev
from typing import Iterable, Sequence

# 评级阈值（毫秒）。参考竞技类 FPS 的体感：<40 极佳，<70 良好，<110 可玩。
GRADE_THRESHOLDS = (("S", 40.0), ("A", 70.0), ("B", 110.0))
GRADE_ORDER = ["S", "A", "B", "C", "X"]


@dataclass(slots=True)
class Stat:
    """Aggregated latency statistics for one target over one window."""

    target_id: int = 0
    target: str = ""
    host: str = ""
    proto: str = "tcp"
    sent: int = 0
    recv: int = 0
    loss: float = 0.0
    mn: float | None = None
    avg: float | None = None
    med: float | None = None
    p95: float | None = None
    mx: float | None = None
    jitter: float | None = None
    grade: str = "X"
    samples: int = 0
    spark: list[float] = field(default_factory=list)
    group: str = ""

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile on an already sorted sequence."""
    if not sorted_values:
        raise ValueError("empty sequence")
    idx = int(q * (len(sorted_values) - 1) + 0.5)      # 四舍五入到最近秩
    return sorted_values[max(0, min(len(sorted_values) - 1, idx))]


def grade_of(avg: float | None, loss: float, jitter: float | None) -> str:
    """延迟评级，再按丢包/抖动逐级下调。

    丢包 >= 50% 直接判 X（几乎不可用）；>= 25% 最多只能 C。
    """
    if avg is None or loss >= 50.0:
        return "X"
    letter = "C"
    for name, limit in GRADE_THRESHOLDS:
        if avg <= limit:
            letter = name
            break
    if loss >= 25.0:
        return "C"
    if loss > 0 or (jitter or 0.0) > 50.0:
        letter = {"S": "A", "A": "B", "B": "C", "C": "C"}[letter]
    return letter


def r1(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def summarize(values: Iterable[float], sent: int, *, target_id: int = 0, target: str = "",
              host: str = "", proto: str = "tcp", spark_max: int = 60) -> Stat:
    """Build a Stat from successful RTT values and the number of attempts made."""
    seq = [v for v in values if v is not None]   # 保持时间序，供 spark 用
    vals = sorted(seq)
    sent = max(sent, 0)
    recv = len(vals)
    loss = 100.0 if sent == 0 else round((sent - recv) * 100.0 / sent, 1)
    if not vals:
        return Stat(target_id=target_id, target=target, host=host, proto=proto,
                    sent=sent, recv=0, loss=loss, grade="X")

    avg = mean(vals)
    jitter = pstdev(vals) if len(vals) > 1 else 0.0
    tail = seq[-spark_max:]                       # 未排序，保留时间序
    return Stat(
        target_id=target_id, target=target, host=host, proto=proto,
        sent=sent, recv=recv, loss=loss,
        mn=r1(vals[0]), avg=r1(avg), med=r1(median(vals)),
        p95=r1(percentile(vals, 0.95)), mx=r1(vals[-1]),
        jitter=r1(jitter), grade=grade_of(avg, loss, jitter),
        samples=recv, spark=[r1(v) or 0.0 for v in tail if v is not None],
    )


def bucketize(points: Sequence[tuple[datetime, float | None]], buckets: int = 60
              ) -> list[dict]:
    """Split a time series into equal-width buckets for chart rendering.

    Each point is (timestamp, rtt or None-for-loss). Every bucket also carries the
    raw samples that fell into it (`samples`), so the UI can show the individual
    readings on hover instead of only the bucket average.
    """
    if not points:
        return []
    start, end = points[0][0], points[-1][0]
    span = (end - start).total_seconds()
    if buckets < 1:
        buckets = 1
    if span <= 0:
        vals = [p[1] for p in points if p[1] is not None]
        return [{"t": start.timestamp(),
                 "avg": r1(mean(vals)) if vals else None,
                 "loss": r1(100.0 if not vals else 0.0),
                 "samples": [{"t": p[0].isoformat(timespec="seconds"),
                              "ms": None if p[1] is None else r1(p[1])}
                             for p in points]}]
    width = span / buckets
    acc: list[list[float]] = [[] for _ in range(buckets)]
    lost = [0] * buckets
    raw: list[list[dict]] = [[] for _ in range(buckets)]
    for ts, rtt in points:
        idx = int((ts - start).total_seconds() / width)
        idx = max(0, min(buckets - 1, idx))
        raw[idx].append({"t": ts.isoformat(timespec="seconds"),
                         "ms": None if rtt is None else r1(rtt)})
        if rtt is None:
            lost[idx] += 1
        else:
            acc[idx].append(rtt)
    out = []
    for i in range(buckets):
        vals = acc[i]
        total = len(vals) + lost[i]
        out.append({
            "t": (start.timestamp() + width * i),
            "avg": r1(mean(vals)) if vals else None,
            "loss": r1(lost[i] * 100.0 / total) if total else 0.0,
            # 悬停提示要显示「那一次」的真实读数，而不是这个桶的均值
            "samples": raw[i],
        })
    return out


def trend(points: Sequence[tuple[datetime, float | None]]) -> dict:
    """Compare the last third of a window against the first third."""
    vals = [p[1] for p in points if p[1] is not None]
    if len(vals) < 6:
        return {"direction": "flat", "delta": None, "first": None, "last": None}
    k = max(1, len(vals) // 3)
    head, tail = mean(vals[:k]), mean(vals[-k:])
    delta = tail - head
    pct = abs(delta) * 100.0 / head if head else 0.0
    direction = "flat"
    if pct >= 10.0:
        direction = "up" if delta > 0 else "down"
    return {"direction": direction, "delta": r1(delta),
            "first": r1(head), "last": r1(tail)}


def spikes(points: Sequence[tuple[datetime, float | None]], k: float = 2.5) -> list[dict]:
    """Flag RTT samples far above the median (jitter spikes worth investigating)."""
    vals = [p[1] for p in points if p[1] is not None]
    if len(vals) < 5:
        return []
    med = median(vals)
    deviations = [abs(v - med) for v in vals]
    mad = median(deviations) or 0.0
    threshold = med + max(k * mad, med * 0.5, 5.0)
    out = []
    for ts, rtt in points:
        if rtt is not None and rtt >= threshold:
            out.append({"t": ts.isoformat(timespec="seconds"), "ms": r1(rtt),
                        "over": r1(rtt - med)})
    out.sort(key=lambda d: d["ms"] or 0, reverse=True)
    return out[:20]


def compare(a: Stat, b: Stat) -> dict:
    """Head-to-head comparison between two targets in the same window."""
    def score(s: Stat) -> float:
        if s.avg is None:
            return 1e9
        return s.avg + s.loss * 10 + (s.jitter or 0.0) * 0.5
    sa, sb = score(a), score(b)
    winner = a.target if sa < sb else b.target if sb < sa else "tie"
    return {"a": a.target, "b": b.target, "winner": winner,
            "delta_avg": r1((a.avg or 0) - (b.avg or 0)) if a.avg and b.avg else None,
            "score_a": r1(sa) if sa < 1e8 else None,
            "score_b": r1(sb) if sb < 1e8 else None}


__all__ = ["Stat", "summarize", "percentile", "grade_of", "bucketize",
           "trend", "spikes", "compare", "GRADE_ORDER"]
