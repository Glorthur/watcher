#!/usr/bin/env python3
"""Poll Reddit subreddit feeds and alert matching posts to Telegram."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
USER_AGENT = "job-watcher/1.0 (+https://github.com/)"
DEFAULT_LOOKBACK_MINUTES = 20
DEFAULT_POST_LIMIT = 25
DEFAULT_MAX_ALERTS = 20


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"seen_post_ids": [], "last_run_utc": 0}

    state = load_json(STATE_PATH)
    state.setdefault("seen_post_ids", [])
    state.setdefault("last_run_utc", 0)
    return state


def build_listing_url(subreddit: str, limit: int) -> str:
    subreddit_name = subreddit.removeprefix("r/")
    query = urllib.parse.urlencode({"limit": limit, "raw_json": 1})
    return f"https://www.reddit.com/r/{subreddit_name}/new.json?{query}"


def fetch_listing(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def matches_keywords(post: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    haystack = normalize_text(
        " ".join(
            [
                post.get("title", ""),
                post.get("selftext", ""),
                post.get("link_flair_text", "") or "",
            ]
        )
    )

    include_terms = [normalize_text(term) for term in config.get("include_keywords", []) if term.strip()]
    exclude_terms = [normalize_text(term) for term in config.get("exclude_keywords", []) if term.strip()]
    required_groups = config.get("required_keyword_groups", [])

    for term in exclude_terms:
        if term and term in haystack:
            return False, f"excluded by '{term}'"

    if required_groups:
        matched_groups: list[str] = []
        for group in required_groups:
            group_name = group.get("name", "group")
            group_terms = [normalize_text(term) for term in group.get("terms", []) if term.strip()]
            group_matches = [term for term in group_terms if term in haystack]
            if not group_matches:
                return False, f"missing required group '{group_name}'"
            matched_groups.append(f"{group_name}: {', '.join(group_matches[:3])}")
        return True, "matched required groups: " + "; ".join(matched_groups)

    if not include_terms:
        return True, "matched (no include terms configured)"

    matches = [term for term in include_terms if term in haystack]
    if matches:
        return True, f"matched include terms: {', '.join(matches[:5])}"

    return False, "no include terms matched"


def is_recent(post: dict[str, Any], cutoff_utc: int) -> bool:
    created = int(post.get("created_utc", 0))
    return created >= cutoff_utc


def format_message(post: dict[str, Any], reason: str) -> str:
    title = escape(post.get("title", "").strip())
    subreddit = escape(post.get("subreddit_name_prefixed", "r/unknown"))
    author = escape(post.get("author", "[deleted]"))
    permalink = post.get("permalink", "")
    url = f"https://www.reddit.com{permalink}"
    flair = post.get("link_flair_text")
    flair_text = f"\n<b>Flair:</b> {escape(flair)}" if flair else ""

    lines = [
        "New Reddit opportunity match",
        f"<b>Subreddit:</b> {subreddit}",
        f"<b>Author:</b> u/{author}",
        f"<b>Reason:</b> {escape(reason)}{flair_text}",
        f"<b>Title:</b> {title}",
        f"<b>Link:</b> {escape(url)}",
    ]
    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram send failed: {payload}")


def collect_matches(config: dict[str, Any], state: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    now_utc = int(time.time())
    lookback_minutes = int(config.get("lookback_minutes", DEFAULT_LOOKBACK_MINUTES))
    limit = int(config.get("post_limit", DEFAULT_POST_LIMIT))
    cutoff_utc = max(now_utc - (lookback_minutes * 60), int(state.get("last_run_utc", 0)) - 60)
    seen_ids = set(state.get("seen_post_ids", []))

    matches: list[dict[str, Any]] = []
    updated_seen = set(seen_ids)

    for subreddit in config.get("subreddits", []):
        url = build_listing_url(subreddit, limit)
        payload = fetch_listing(url)
        posts = payload.get("data", {}).get("children", [])

        for item in posts:
            post = item.get("data", {})
            post_id = post.get("id")
            if not post_id or post_id in seen_ids:
                continue
            if not is_recent(post, cutoff_utc):
                continue

            matched, reason = matches_keywords(post, config)
            updated_seen.add(post_id)
            if matched:
                matches.append({"post": post, "reason": reason})

    matches.sort(key=lambda item: int(item["post"].get("created_utc", 0)))
    return matches[: int(config.get("max_alerts_per_run", DEFAULT_MAX_ALERTS))], updated_seen


def prune_seen_post_ids(seen_ids: set[str], matches: list[dict[str, Any]], max_seen: int) -> list[str]:
    ordered_ids = [item["post"]["id"] for item in matches if item["post"].get("id")]
    ordered_ids.extend(sorted(seen_ids - set(ordered_ids)))
    return ordered_ids[:max_seen]


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"Missing config file: {CONFIG_PATH}", file=sys.stderr)
        return 1

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1

    config = load_json(CONFIG_PATH)
    state = load_state()

    try:
        matches, seen_ids = collect_matches(config, state)
        for item in matches:
            send_telegram_message(bot_token, chat_id, format_message(item["post"], item["reason"]))
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1

    max_seen = int(config.get("max_seen_post_ids", 500))
    next_state = {
        "last_run_utc": int(time.time()),
        "seen_post_ids": prune_seen_post_ids(seen_ids, matches, max_seen),
    }
    save_json(STATE_PATH, next_state)

    print(f"Sent {len(matches)} alert(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
