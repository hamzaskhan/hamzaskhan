#!/usr/bin/env python3
"""Weekly Gemini summary of notes + public work → WEEKLY.md + README."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_PATH = ROOT / "WEEKLY.md"
README_PATH = ROOT / "README.md"
# Keep TODAY.md in sync briefly for old links; prefer WEEKLY.md
TODAY_PATH = ROOT / "TODAY.md"

USERNAME = os.environ.get("GITHUB_USERNAME", "hamzaskhan")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or os.environ.get("GOOGLE_AI_API_KEY")
    or None
)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
TZ_NAME = os.environ.get("LOG_TIMEZONE") or "Asia/Karachi"

NOTE_REPOS = [
    "engineering-notes",
    "Research-Notes",
    "Architectural-Systems",
    "How-I-solved-it",
]


def now_local() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(TZ_NAME))
    except Exception:
        return datetime.now(timezone(timedelta(hours=5)))


def http_json(url: str, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "hamzaskhan-weekly-summary")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if GITHUB_TOKEN and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
        req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_week_commits(since: datetime) -> list[dict[str, str]]:
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


def fetch_note_file_hints(since: datetime) -> list[str]:
    """Recent file paths touched in note repos (public commits API)."""
    hints: list[str] = []
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    for repo in NOTE_REPOS:
        try:
            commits = http_json(
                f"https://api.github.com/repos/{USERNAME}/{repo}/commits?since={since_iso}&per_page=10"
            )
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
        if not isinstance(commits, list):
            continue
        for c in commits[:5]:
            sha = c.get("sha")
            msg = (c.get("commit", {}).get("message") or "").split("\n", 1)[0].strip()
            files: list[str] = []
            if sha:
                try:
                    detail = http_json(f"https://api.github.com/repos/{USERNAME}/{repo}/commits/{sha}")
                    files = [f.get("filename", "") for f in detail.get("files") or [] if f.get("filename")]
                except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError):
                    files = []
            file_bit = ", ".join(files[:6]) if files else "(files n/a)"
            hints.append(f"[{repo}] {msg} — {file_bit}")
    return hints


def fallback_log(label: str, commits: list[dict[str, str]], notes: list[str]) -> str:
    lines = [f"### {label}", ""]
    if notes:
        lines.append("**Notes touched**")
        lines.extend(f"- {n}" for n in notes[:8])
        lines.append("")
    if commits:
        lines.append("**Other public commits**")
        for c in commits[:10]:
            lines.append(f"- `{c['repo']}` — {c['message']}")
        lines.append("")
    if not notes and not commits:
        lines.append("- Quiet week on public GitHub / notes.")
        lines.append("")
    lines.extend(["Next:", "Add one production note worth summarizing next week.", ""])
    return "\n".join(lines)


def llm_prompt(label: str, commits: list[dict[str, str]], notes: list[str]) -> str:
    note_block = "\n".join(f"- {n}" for n in notes) or "- (no note-repo commits this week)"
    commit_block = "\n".join(f"- [{c['repo']}] {c['message']}" for c in commits[:20]) or "- (none)"
    return f"""Write Hamza Khan's weekly GitHub profile summary ({label}).

Hamza is a backend-focused full stack engineer (Python, Node.js) working on distributed systems,
AI infrastructure, and cloud-native backends. Site: hamzakhan.dev.
Do NOT repeat CV metrics (latency %, RPS, image sizes, dollar figures).
Tone: plain, understated. Output markdown only.

Structure:
### {label}

- Prefer summarizing **notes** (engineering notes, research notes, architecture, how-I-solved-it)
- Then at most 2 bullets on other public work if useful
- End with "Next:" and one concrete follow-up

Notes / note-repo activity this week:
{note_block}

Other public commits this week:
{commit_block}
"""


def call_gemini(prompt: str) -> str:
    model = GEMINI_MODEL
    qs = urllib.parse.urlencode({"key": GEMINI_API_KEY})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?{qs}"
    result = http_json(
        url,
        body={
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Summarize engineering notes for a GitHub profile. "
                            "Plain, short, no hype, no CV metric spam."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 600},
        },
    )
    candidates = result.get("candidates") or []
    if not candidates:
        raise KeyError(f"no candidates: {result}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise KeyError("empty Gemini text")
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def generate_log(label: str, commits: list[dict[str, str]], notes: list[str]) -> str:
    prompt = llm_prompt(label, commits, notes)
    try:
        if GEMINI_API_KEY:
            print(f"Using Gemini model={GEMINI_MODEL}")
            return call_gemini(prompt)
        print("No GEMINI_API_KEY; using fallback.")
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = str(exc)
            print(f"LLM failed HTTP {exc.code}: {detail}")
        else:
            print(f"LLM failed: {exc}")
    return fallback_log(label, commits, notes)


def sync_readme(log_md: str) -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"<!-- WEEK:START -->.*?<!-- WEEK:END -->", re.DOTALL)
    replacement = f"<!-- WEEK:START -->\n{log_md.strip()}\n<!-- WEEK:END -->"
    if not pattern.search(readme):
        raise SystemExit("README.md missing <!-- WEEK:START/END --> markers")
    README_PATH.write_text(pattern.sub(replacement, readme), encoding="utf-8")


def main() -> None:
    local = now_local()
    # ISO week label
    label = f"Week of {local.strftime('%d %b %Y')}"
    since = datetime.now(timezone.utc) - timedelta(days=7)

    try:
        commits = fetch_week_commits(since)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"Commit fetch failed: {exc}")
        commits = []

    try:
        notes = fetch_note_file_hints(since)
    except Exception as exc:  # noqa: BLE001
        print(f"Note hints failed: {exc}")
        notes = []

    # Prefer showing note-repo commits inside notes section; keep others separate
    note_repo_suffixes = tuple(f"/{r}" for r in NOTE_REPOS) + tuple(NOTE_REPOS)
    other = [c for c in commits if not any(c["repo"].endswith(s) or c["repo"] == s for s in note_repo_suffixes)]

    print(f"Notes hints: {len(notes)}; other commits: {len(other)}")
    log_md = generate_log(label, other, notes)
    WEEKLY_PATH.write_text(log_md.strip() + "\n", encoding="utf-8")
    TODAY_PATH.write_text(log_md.strip() + "\n", encoding="utf-8")
    sync_readme(log_md)
    print("Updated WEEKLY.md, TODAY.md, README.md")


if __name__ == "__main__":
    main()
