#!/usr/bin/env python3
"""Build a deeper, uncommon profile insight graph from public GitHub activity.

Left:  24h chronograph (when you actually push) — not a contribution snake.
Right: Domain pressure map + work-mode metrics from repos + recent events.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "insight-graph.svg"
USERNAME = os.environ.get("GITHUB_USERNAME", "hamzaskhan")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
TZ = timezone(timedelta(hours=5))  # Asia/Karachi


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "hamzaskhan-insight-graph")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for page in range(1, 4):
        chunk = http_json(
            f"https://api.github.com/users/{USERNAME}/events/public?per_page=100&page={page}"
        )
        if not chunk:
            break
        events.extend(chunk)
        if len(chunk) < 100:
            break
    return events


def fetch_repos() -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    for page in range(1, 3):
        chunk = http_json(
            f"https://api.github.com/users/{USERNAME}/repos?per_page=100&sort=updated&page={page}"
        )
        if not chunk:
            break
        repos.extend(chunk)
        if len(chunk) < 100:
            break
    return [r for r in repos if not r.get("fork")]


DOMAIN_RULES: list[tuple[str, list[str]]] = [
    ("Backend", ["api", "fastapi", "nestjs", "auth", "mongo", "redis", "backend", "server", "docker"]),
    ("AI / LLMs", ["ai", "llm", "gemini", "gpt", "whisper", "rag", "agent", "subtitle", "embed"]),
    ("Robotics", ["robot", "isaac", "jetbot", "sim", "control", "yolo", "pixel", "3d"]),
    ("Systems", ["distributed", "queue", "sqs", "pipeline", "infra", "aws", "cache", "event"]),
    ("Notes", ["notes", "research", "architecture", "solved", "engineering"]),
]


def classify_repo(name: str, desc: str, topics: list[str], language: str | None) -> list[str]:
    blob = " ".join([name, desc or "", " ".join(topics), language or ""]).lower()
    hits = []
    for domain, keys in DOMAIN_RULES:
        if any(k in blob for k in keys):
            hits.append(domain)
    if not hits and language:
        hits.append("Backend" if language in {"Python", "TypeScript", "JavaScript", "Go"} else "Other")
    return hits or ["Other"]


def analyze(events: list[dict[str, Any]], repos: list[dict[str, Any]]) -> dict[str, Any]:
    hour_counts = Counter()
    weekday_counts = Counter()
    push_commits = 0
    push_events = 0
    repos_touched: Counter[str] = Counter()
    event_types = Counter()

    for e in events:
        event_types[e.get("type", "?")] += 1
        created = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(TZ)
        if e.get("type") == "PushEvent":
            push_events += 1
            n = max(1, len(e.get("payload", {}).get("commits") or []))
            push_commits += n
            hour_counts[created.hour] += n
            weekday_counts[created.weekday()] += n
            repo_name = (e.get("repo") or {}).get("name", "").split("/")[-1]
            if repo_name:
                repos_touched[repo_name] += n

    domain_scores: Counter[str] = Counter()
    lang_bytes: Counter[str] = Counter()
    active_days = set()
    longevity = []

    now = datetime.now(timezone.utc)
    for r in repos:
        lang = r.get("language") or "Other"
        # approximate weight: size + recent activity boost
        weight = max(1, int((r.get("size") or 1) ** 0.5))
        pushed = r.get("pushed_at")
        if pushed:
            pushed_dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
            days_ago = max(0, (now - pushed_dt).days)
            recency = 2.5 if days_ago < 30 else 1.5 if days_ago < 90 else 1.0
            longevity.append((now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).days)
        else:
            recency = 1.0
        lang_bytes[lang] += weight
        for d in classify_repo(r.get("name", ""), r.get("description") or "", r.get("topics") or [], lang):
            domain_scores[d] += weight * recency

    # Work mode metrics
    night = sum(hour_counts[h] for h in range(0, 5)) + sum(hour_counts[h] for h in range(22, 24))
    day = sum(hour_counts[h] for h in range(9, 18))
    weekend = weekday_counts[5] + weekday_counts[6]
    weekday = sum(weekday_counts[i] for i in range(5))
    total_h = sum(hour_counts.values()) or 1

    # Focus index: 1 - normalized entropy of repos touched (higher = more focused)
    touch_total = sum(repos_touched.values()) or 1
    entropy = 0.0
    for c in repos_touched.values():
        p = c / touch_total
        entropy -= p * math.log(p + 1e-12)
    max_ent = math.log(max(2, len(repos_touched)))
    focus = 1.0 - (entropy / max_ent if max_ent else 0)

    # Depth: multi-domain coverage vs single-language monoculture
    domain_n = len([d for d, v in domain_scores.items() if v > 0 and d != "Other"])
    lang_n = len([l for l, v in lang_bytes.items() if v > 0])

    peak_hour = hour_counts.most_common(1)[0][0] if hour_counts else 0
    mode = "night forge" if night > day else "daylight siege"
    if weekend > weekday * 0.55:
        mode = "weekend raider"

    return {
        "hour_counts": hour_counts,
        "weekday_counts": weekday_counts,
        "domain_scores": domain_scores,
        "lang_bytes": lang_bytes,
        "push_commits": push_commits,
        "push_events": push_events,
        "repos_touched": len(repos_touched),
        "focus": focus,
        "domain_n": domain_n,
        "lang_n": lang_n,
        "peak_hour": peak_hour,
        "mode": mode,
        "night_ratio": night / total_h,
        "weekend_ratio": weekend / max(1, weekend + weekday),
        "repo_count": len(repos),
        "avg_longevity_days": int(sum(longevity) / len(longevity)) if longevity else 0,
        "event_types": event_types,
    }


def polar(cx: float, cy: float, r: float, angle_deg: float) -> tuple[float, float]:
    # 0 at top, clockwise
    a = math.radians(angle_deg - 90)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def ring_slice(cx: float, cy: float, r0: float, r1: float, a0: float, a1: float) -> str:
    p0 = polar(cx, cy, r1, a0)
    p1 = polar(cx, cy, r1, a1)
    p2 = polar(cx, cy, r0, a1)
    p3 = polar(cx, cy, r0, a0)
    large = 1 if (a1 - a0) % 360 > 180 else 0
    return (
        f"M {p0[0]:.1f} {p0[1]:.1f} "
        f"A {r1} {r1} 0 {large} 1 {p1[0]:.1f} {p1[1]:.1f} "
        f"L {p2[0]:.1f} {p2[1]:.1f} "
        f"A {r0} {r0} 0 {large} 0 {p3[0]:.1f} {p3[1]:.1f} Z"
    )


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render(data: dict[str, Any]) -> str:
    w, h = 1600, 520
    cx, cy, r_out, r_in = 340, 280, 175, 70
    hour_counts: Counter = data["hour_counts"]
    max_h = max(hour_counts.values()) if hour_counts else 1

    slices = []
    for hour in range(24):
        a0 = hour * 15
        a1 = (hour + 1) * 15
        intensity = hour_counts.get(hour, 0) / max_h
        # green early / red late — evil forge palette
        if 6 <= hour < 18:
            fill = f"rgb({int(20 + 40 * intensity)},{int(80 + 100 * intensity)},{int(40 + 40 * intensity)})"
        else:
            fill = f"rgb({int(90 + 140 * intensity)},{int(20 + 30 * intensity)},{int(30 + 20 * intensity)})"
        opacity = 0.15 + 0.85 * intensity
        if hour_counts.get(hour, 0) == 0:
            fill = "#1f2937"
            opacity = 0.35
        d = ring_slice(cx, cy, r_in, r_out, a0, a1 - 0.6)
        slices.append(f'<path d="{d}" fill="{fill}" fill-opacity="{opacity:.2f}" stroke="#0b1410" stroke-width="1"/>')

    # hour ticks
    ticks = []
    for hour in (0, 6, 12, 18):
        x1, y1 = polar(cx, cy, r_out + 8, hour * 15)
        x2, y2 = polar(cx, cy, r_out + 22, hour * 15)
        label = {0: "00", 6: "06", 12: "12", 18: "18"}[hour]
        lx, ly = polar(cx, cy, r_out + 40, hour * 15)
        ticks.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#9ca3af" stroke-width="2"/>'
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" dominant-baseline="middle" '
            f'font-family="Arial, Helvetica, sans-serif" font-size="14" fill="#d1d5db">{label}</text>'
        )

    # peak marker
    peak = data["peak_hour"]
    px, py = polar(cx, cy, r_out + 8, peak * 15 + 7.5)
    peak_spark = (
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="#ef4444">'
        f'<animate attributeName="opacity" values="0.3;1;0.3" dur="1.6s" repeatCount="indefinite"/>'
        f"</circle>"
    )

    # domain bars
    domains = data["domain_scores"]
    ordered = sorted(domains.items(), key=lambda kv: kv[1], reverse=True)[:5]
    max_d = max((v for _, v in ordered), default=1)
    bars = []
    bx, by = 780, 150
    for i, (name, val) in enumerate(ordered):
        y = by + i * 48
        bw = 520 * (val / max_d)
        color = "#22c55e" if i % 2 == 0 else "#ef4444"
        bars.append(
            f'<text x="{bx}" y="{y}" font-family="Arial, Helvetica, sans-serif" font-size="16" fill="#e5e7eb">{esc(name)}</text>'
            f'<rect x="{bx}" y="{y + 8}" width="{bw:.1f}" height="16" rx="4" fill="{color}" opacity="0.85"/>'
            f'<rect x="{bx}" y="{y + 8}" width="520" height="16" rx="4" fill="none" stroke="#374151" stroke-width="1"/>'
        )

    # metrics panel
    metrics = [
        ("work mode", data["mode"]),
        ("peak hour", f"{data['peak_hour']:02d}:00 PKT"),
        ("focus index", f"{data['focus']*100:.0f}/100"),
        ("night share", f"{data['night_ratio']*100:.0f}%"),
        ("domains live", str(data["domain_n"])),
        ("repos active", f"{data['repos_touched']} touched / {data['repo_count']} owned"),
        ("recent pushes", f"{data['push_commits']} commits in window"),
        ("avg repo age", f"{data['avg_longevity_days']} days"),
    ]
    metric_svg = []
    for i, (k, v) in enumerate(metrics):
        col = i % 2
        row = i // 2
        x = 780 + col * 340
        y = 400 + row * 28
        metric_svg.append(
            f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" font-size="14" fill="#9ca3af">{esc(k)}</text>'
            f'<text x="{x + 120}" y="{y}" font-family="Arial, Helvetica, sans-serif" font-size="14" font-weight="700" fill="#fef2f2">{esc(str(v))}</text>'
        )

    generated = datetime.now(TZ).strftime("%d %b %Y %H:%M PKT")

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#06140c"/>
      <stop offset="55%" stop-color="#0b1410"/>
      <stop offset="100%" stop-color="#140608"/>
    </linearGradient>
    <linearGradient id="edge" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#166534"/>
      <stop offset="100%" stop-color="#991b1b"/>
    </linearGradient>
  </defs>
  <rect width="{w}" height="{h}" rx="16" fill="url(#bg)"/>
  <rect x="3" y="3" width="{w-6}" height="{h-6}" rx="14" fill="none" stroke="url(#edge)" stroke-width="2"/>

  <text x="40" y="48" font-family="Arial, Helvetica, sans-serif" font-size="26" font-weight="700" fill="#fef2f2">Work signature</text>
  <text x="40" y="78" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#86efac">24h chronograph</text>
  <text x="220" y="78" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#6b7280">|</text>
  <text x="240" y="78" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#fca5a5">not a contribution snake</text>

  <text x="780" y="48" font-family="Arial, Helvetica, sans-serif" font-size="26" font-weight="700" fill="#fef2f2">Domain pressure</text>
  <text x="780" y="78" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#d1d5db">where effort actually concentrates</text>

  {"".join(slices)}
  <circle cx="{cx}" cy="{cy}" r="{r_in-8}" fill="#06140c" stroke="#374151" stroke-width="2"/>
  <text x="{cx}" y="{cy - 8}" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="18" font-weight="700" fill="#e5e7eb">PKT</text>
  <text x="{cx}" y="{cy + 18}" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="13" fill="#9ca3af">push clock</text>
  {"".join(ticks)}
  {peak_spark}

  {"".join(bars)}
  {"".join(metric_svg)}

  <text x="40" y="500" font-family="Arial, Helvetica, sans-serif" font-size="12" fill="#6b7280">generated {esc(generated)} from public events + repos · green = daylight hours · red = night hours</text>
</svg>
'''


def main() -> None:
    try:
        events = fetch_events()
        repos = fetch_repos()
        print(f"Fetched {len(events)} events, {len(repos)} non-fork repos")
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"Fetch failed: {exc}")
        events, repos = [], []

    data = analyze(events, repos)
    svg = render(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
