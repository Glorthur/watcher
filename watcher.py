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
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path
from typing import Any
from calendar import timegm


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
USER_AGENT = "job-watcher/1.0 (+https://github.com/Glorthur/watcher)"
DEFAULT_LOOKBACK_MINUTES = 20
DEFAULT_MAX_ALERTS = 20
ATOM_NAMESPACE = {"atom": "http://www.w3.org/2005/Atom"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "instant_seen_post_ids": [],
            "summary_seen_post_ids": [],
            "summary_buffer": [],
            "last_run_utc": 0,
            "last_summary_utc": 0,
        }

    state = load_json(STATE_PATH)
    state.setdefault("instant_seen_post_ids", [])
    state.setdefault("summary_seen_post_ids", [])
    state.setdefault("summary_buffer", [])
    state.setdefault("last_run_utc", 0)
    state.setdefault("last_summary_utc", 0)
    return state


def build_feed_url(subreddit: str) -> str:
    subreddit_name = subreddit.removeprefix("r/")
    return f"https://www.reddit.com/r/{subreddit_name}/new/.rss"


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def parse_feed(feed_text: str, subreddit: str) -> list[dict[str, Any]]:
    root = ET.fromstring(feed_text)
    posts: list[dict[str, Any]] = []

    for entry in root.findall("atom:entry", ATOM_NAMESPACE):
        title = entry.findtext("atom:title", default="", namespaces=ATOM_NAMESPACE)
        post_id = entry.findtext("atom:id", default="", namespaces=ATOM_NAMESPACE)
        link = ""
        for link_node in entry.findall("atom:link", ATOM_NAMESPACE):
            href = link_node.attrib.get("href")
            if href:
                link = href
                break
        updated = entry.findtext("atom:updated", default="", namespaces=ATOM_NAMESPACE)
        author_name = entry.findtext("atom:author/atom:name", default="[deleted]", namespaces=ATOM_NAMESPACE)
        content = entry.findtext("atom:content", default="", namespaces=ATOM_NAMESPACE)

        created_utc = iso_to_epoch(updated)
        posts.append(
            {
                "id": post_id or link,
                "title": title,
                "selftext": content,
                "link_flair_text": "",
                "author": author_name,
                "created_utc": created_utc,
                "subreddit_name_prefixed": subreddit if subreddit.startswith("r/") else f"r/{subreddit}",
                "permalink": normalize_permalink(link),
            }
        )

    return posts


def normalize_permalink(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("reddit.com"):
        return parsed.path or "/"
    return url


def iso_to_epoch(value: str) -> int:
    if not value:
        return 0
    try:
        struct = time.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        return int(timegm(struct))
    except ValueError:
        return 0


def matches_rule(post: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    title_text = normalize_text(post.get("title", ""))
    body_text = normalize_text(
        " ".join(
            [
                post.get("selftext", ""),
                post.get("link_flair_text", "") or "",
            ]
        )
    )
    haystack = normalize_text(" ".join([title_text, body_text]))

    include_terms = [normalize_text(term) for term in rule.get("include_keywords", []) if term.strip()]
    exclude_terms = [normalize_text(term) for term in rule.get("exclude_keywords", []) if term.strip()]
    required_groups = rule.get("required_keyword_groups", [])

    for term in exclude_terms:
        if term and term in haystack:
            return False, f"excluded by '{term}'"

    if required_groups:
        matched_groups: list[str] = []
        for group in required_groups:
            group_name = group.get("name", "group")
            group_terms = [normalize_text(term) for term in group.get("terms", []) if term.strip()]
            group_source = group.get("source", "all")
            search_text = title_text if group_source == "title" else haystack
            group_matches = [term for term in group_terms if term in search_text]
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
    url = permalink if str(permalink).startswith("http") else f"https://www.reddit.com{permalink}"
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


def collect_posts(config: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    now_utc = int(time.time())
    lookback_minutes = int(config.get("lookback_minutes", DEFAULT_LOOKBACK_MINUTES))
    cutoff_utc = max(now_utc - (lookback_minutes * 60), int(state.get("last_run_utc", 0)) - 60)
    posts: list[dict[str, Any]] = []

    for subreddit in config.get("subreddits", []):
        feed_url = build_feed_url(subreddit)
        feed_text = fetch_text(feed_url)
        feed_posts = parse_feed(feed_text, subreddit)

        for post in feed_posts:
            post_id = post.get("id")
            if not post_id:
                continue
            if not is_recent(post, cutoff_utc):
                continue
            posts.append(post)

    posts.sort(key=lambda item: int(item.get("created_utc", 0)))
    return posts


def collect_rule_matches(
    posts: list[dict[str, Any]],
    rule: dict[str, Any],
    seen_ids: set[str],
    max_items: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    matches: list[dict[str, Any]] = []
    updated_seen = set(seen_ids)

    for post in posts:
        post_id = str(post.get("id", ""))
        if not post_id or post_id in seen_ids:
            continue
        matched, reason = matches_rule(post, rule)
        updated_seen.add(post_id)
        if matched:
            matches.append({"post": post, "reason": reason})

    return matches[:max_items], updated_seen


def prune_seen_post_ids(seen_ids: set[str], recent_post_ids: list[str], max_seen: int) -> list[str]:
    ordered_ids = [post_id for post_id in recent_post_ids if post_id]
    ordered_ids.extend(sorted(seen_ids - set(ordered_ids)))
    return ordered_ids[:max_seen]


def append_summary_buffer(buffer: list[dict[str, Any]], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["post"]["id"]: item for item in buffer if item.get("post", {}).get("id")}
    for item in matches:
        post_id = item["post"].get("id")
        if post_id:
            by_id[post_id] = item
    ordered = sorted(by_id.values(), key=lambda item: int(item["post"].get("created_utc", 0)))
    return ordered


def format_summary_message(summary_matches: list[dict[str, Any]]) -> str:
    lines = [f"Daily Reddit opportunity summary ({len(summary_matches)} matches)"]
    for item in summary_matches[:20]:
        post = item["post"]
        permalink = post.get("permalink", "")
        url = permalink if str(permalink).startswith("http") else f"https://www.reddit.com{permalink}"
        lines.append(
            "\n".join(
                [
                    f"<b>{escape(post.get('title', '').strip())}</b>",
                    f"{escape(post.get('subreddit_name_prefixed', 'r/unknown'))} | u/{escape(post.get('author', '[deleted]'))}",
                    escape(url),
                ]
            )
        )
    if len(summary_matches) > 20:
        lines.append(f"...and {len(summary_matches) - 20} more.")
    return "\n\n".join(lines)


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
    mode = os.getenv("WATCHER_MODE", "instant").strip().lower()

    try:
        posts = collect_posts(config, state)

        instant_rule = config.get("instant_rule", {})
        summary_rule = config.get("summary_rule", {})
        max_alerts = int(config.get("max_alerts_per_run", DEFAULT_MAX_ALERTS))
        max_seen = int(config.get("max_seen_post_ids", 500))
        recent_post_ids = [str(post.get("id", "")) for post in posts if post.get("id")]

        instant_matches, instant_seen = collect_rule_matches(
            posts,
            instant_rule,
            set(state.get("instant_seen_post_ids", [])),
            max_alerts,
        )
        summary_matches, summary_seen = collect_rule_matches(
            posts,
            summary_rule,
            set(state.get("summary_seen_post_ids", [])),
            int(config.get("max_summary_buffer", 200)),
        )

        summary_buffer = append_summary_buffer(state.get("summary_buffer", []), summary_matches)
        sent_count = 0

        if mode == "summary":
            if summary_buffer:
                send_telegram_message(bot_token, chat_id, format_summary_message(summary_buffer))
                sent_count = len(summary_buffer)
                summary_buffer = []
                state["last_summary_utc"] = int(time.time())
            print(f"Sent daily summary with {sent_count} item(s).")
        else:
            for item in instant_matches:
                send_telegram_message(bot_token, chat_id, format_message(item["post"], item["reason"]))
            sent_count = len(instant_matches)
            print(f"Sent {sent_count} instant alert(s).")
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1

    next_state = {
        "last_run_utc": int(time.time()),
        "last_summary_utc": int(state.get("last_summary_utc", 0)),
        "instant_seen_post_ids": prune_seen_post_ids(instant_seen, recent_post_ids, max_seen),
        "summary_seen_post_ids": prune_seen_post_ids(summary_seen, recent_post_ids, max_seen),
        "summary_buffer": summary_buffer[: int(config.get("max_summary_buffer", 200))],
    }
    save_json(STATE_PATH, next_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
