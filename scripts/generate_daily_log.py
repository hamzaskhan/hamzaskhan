#!/usr/bin/env python3
"""Generate TODAY.md from the last ~24h of public GitHub activity, then sync README."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TODAY_PATH = ROOT / "TODAY.md"
README_PATH = ROOT / "README.md"
USERNAME = os.environ.get("GITHUB_USERNAME", "hamzaskhan")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or None
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-haiku-latest"
TZ_NAME = os.environ.get("LOG_TIMEZONE") or "Asia/Karachi"


def now_local() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(TZ_NAME))
    except Exception:
        return datetime.now(timezone(timedelta(hours=5)))


def http_json(url: str, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "hamzaskhan-daily-log")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if GITHUB_TOKEN and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_recent_commits(since: datetime) -> list[dict[str, str]]:
    """Use public events (PushEvent) — works for public activity with GITHUB_TOKEN."""
    events = http_json(f"https://api.github.com/users/{USERNAME}/events/public?per_page=100")
    commits: list[dict[str, str]] = []
    for event in events:
        created = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        if created < since:
            continue
        if event.get("type") != "PushEvent":
            continue
        repo = event.get("repo", {}).get("name", "unknown")
        for c in event.get("payload", {}).get("commits", []):
            commits.append(
                {
                    "repo": repo,
                    "message": (c.get("message") or "").split("\n", 1)[0].strip(),
                    "sha": (c.get("sha") or "")[:7],
                }
            )
    return commits


def fallback_log(date_label: str, commits: list[dict[str, str]]) -> str:
    if not commits:
        return (
            f"### {date_label}\n\n"
            "- Quiet day on public GitHub.\n"
            "- Next: add one real note when there is something worth writing down.\n"
        )
    lines = [f"### {date_label}", "", "**Raw activity (no LLM key configured)**", ""]
    for c in commits[:12]:
        lines.append(f"- `{c['repo']}` — {c['message']}")
    lines.extend(["", "Next:", "Turn one of these into a short note if useful.", ""])
    return "\n".join(lines)


def llm_prompt(date_label: str, commits: list[dict[str, str]]) -> str:
    commit_block = "\n".join(f"- [{c['repo']}] {c['message']} ({c['sha']})" for c in commits) or "- (no public commits)"
    return f"""Write a short, understated engineering log for Hamza's GitHub profile ({date_label}).

Tone: plain and honest. No hype, no CV metrics (RPS, latency brags), no overselling.
Output markdown only. Structure:

### {date_label}

- 2–5 short bullets of what was built / tried / noted
- End with "Next:" and one concrete follow-up

If there is little activity, say so simply.

Commits from the last day:
{commit_block}
"""


def call_openai(prompt: str) -> str:
    result = http_json(
        f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        body={
            "model": OPENAI_MODEL,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": "Write short, plain engineering logs. Do not hype or oversell."},
                {"role": "user", "content": prompt},
            ],
        },
    )
    return result["choices"][0]["message"]["content"].strip()


def call_anthropic(prompt: str) -> str:
    result = http_json(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY or "",
            "anthropic-version": "2023-06-01",
        },
        body={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    parts = result.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return text.strip()


def generate_log(date_label: str, commits: list[dict[str, str]]) -> str:
    prompt = llm_prompt(date_label, commits)
    try:
        if OPENAI_API_KEY:
            return call_openai(prompt)
        if ANTHROPIC_API_KEY:
            return call_anthropic(prompt)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
        print(f"LLM call failed ({exc}); using fallback.")
    return fallback_log(date_label, commits)


def sync_readme(log_md: str) -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"<!-- TODAY:START -->.*?<!-- TODAY:END -->", re.DOTALL)
    replacement = f"<!-- TODAY:START -->\n{log_md.strip()}\n<!-- TODAY:END -->"
    if not pattern.search(readme):
        raise SystemExit("README.md is missing <!-- TODAY:START/END --> markers")
    README_PATH.write_text(pattern.sub(replacement, readme), encoding="utf-8")


def main() -> None:
    local = now_local()
    date_label = local.strftime("%d %b %Y")
    since = datetime.now(timezone.utc) - timedelta(hours=30)
    try:
        commits = fetch_recent_commits(since)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"Commit fetch failed ({exc}); continuing with empty list.")
        commits = []

    print(f"Found {len(commits)} commit messages in public events.")
    log_md = generate_log(date_label, commits)
    TODAY_PATH.write_text(log_md.strip() + "\n", encoding="utf-8")
    sync_readme(log_md)
    print("Updated TODAY.md and README.md")


if __name__ == "__main__":
    main()
