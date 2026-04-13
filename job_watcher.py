#!/usr/bin/env python3
"""Poll public job feeds for fresh remote operations, HR, CRM, and executive support roles."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "job_config.json"
STATE_PATH = ROOT / "job_state.json"
USER_AGENT = "direct-job-watcher/1.0 (+https://github.com/Glorthur/watcher)"
DEFAULT_LOOKBACK_HOURS = 36
DEFAULT_MAX_ALERTS = 10
DEFAULT_MAX_SEEN_IDS = 5000
REMOTE_TERMS = (
    "remote",
    "distributed",
    "work from home",
    "work-from-home",
    "work from anywhere",
    "wfh",
)
GLOBAL_REMOTE_TERMS = (
    "global",
    "worldwide",
    "international",
    "anywhere",
    "work from anywhere",
)
REGION_PRESETS = {
    "emea": [
        "emea",
        "europe",
        "european union",
        "european economic area",
        "eea",
        "eu",
        "united kingdom",
        "uk",
        "england",
        "scotland",
        "wales",
        "ireland",
        "northern ireland",
        "portugal",
        "spain",
        "france",
        "belgium",
        "netherlands",
        "luxembourg",
        "germany",
        "austria",
        "switzerland",
        "italy",
        "malta",
        "denmark",
        "sweden",
        "norway",
        "finland",
        "iceland",
        "poland",
        "czechia",
        "czech republic",
        "slovakia",
        "hungary",
        "romania",
        "bulgaria",
        "greece",
        "cyprus",
        "slovenia",
        "croatia",
        "serbia",
        "estonia",
        "latvia",
        "lithuania",
        "ukraine",
        "middle east",
        "mea",
        "uae",
        "united arab emirates",
        "saudi arabia",
        "qatar",
        "bahrain",
        "kuwait",
        "oman",
        "jordan",
        "lebanon",
        "israel",
        "turkey",
        "egypt",
        "africa",
        "south africa",
        "nigeria",
        "kenya",
        "ghana",
        "uganda",
        "rwanda",
        "tanzania",
        "morocco",
        "tunisia",
        "algeria",
        "ethiopia",
        "senegal",
        "botswana",
        "namibia",
        "mauritius",
    ]
}
DISALLOWED_REGION_TERMS = (
    "united states only",
    "us only",
    "u.s. only",
    "canada only",
    "north america only",
    "north america",
    "latam only",
    "latin america only",
    "apac only",
    "asia only",
    "australia only",
    "new zealand only",
    "india only",
    "philippines only",
)


@dataclass(slots=True)
class JobPosting:
    dedupe_id: str
    source_key: str
    source_label: str
    source_kind: str
    company: str
    title: str
    location: str
    description: str
    url: str
    remote: bool
    published_utc: int
    freshness_known: bool


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def strip_html(value: str) -> str:
    text = unescape(value or "")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(text.split())


def parse_timestamp(value: str) -> int:
    if not value:
        return 0

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return 0


def format_timestamp(epoch_seconds: int) -> str:
    if epoch_seconds <= 0:
        return "unknown"
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_location_fragment(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = [str(value.get("location") or "").strip()]
        address = (value.get("address") or {}).get("postalAddress") or {}
        parts.extend(
            [
                str(address.get("addressLocality") or "").strip(),
                str(address.get("addressRegion") or "").strip(),
                str(address.get("addressCountry") or "").strip(),
            ]
        )
        deduped: list[str] = []
        for part in parts:
            if part and part not in deduped:
                deduped.append(part)
        return ", ".join(deduped)
    return str(value).strip()


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain;q=0.8, */*;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "last_run_utc": 0,
            "seen_ids": [],
        }

    state = load_json(STATE_PATH)
    state.setdefault("last_run_utc", 0)
    state.setdefault("seen_ids", [])
    return state


def build_source_key(source: dict[str, Any]) -> str:
    kind = source.get("kind", "").strip().lower()
    identifier = (
        source.get("board_token")
        or source.get("board")
        or source.get("site")
        or source.get("search_url")
        or source.get("label")
        or "unknown"
    )
    return f"{kind}:{identifier}"


def greenhouse_jobs(source: dict[str, Any]) -> list[JobPosting]:
    board_token = str(source["board_token"]).strip()
    payload = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true")
    jobs = payload.get("jobs", [])
    postings: list[JobPosting] = []

    for job in jobs:
        location = (job.get("location") or {}).get("name", "")
        description = strip_html(str(job.get("content", "")))
        published_utc = parse_timestamp(str(job.get("first_published") or job.get("updated_at") or ""))
        postings.append(
            JobPosting(
                dedupe_id=f"greenhouse:{board_token}:{job.get('id')}",
                source_key=build_source_key(source),
                source_label=str(source.get("label") or job.get("company_name") or board_token),
                source_kind="greenhouse",
                company=str(job.get("company_name") or source.get("label") or board_token),
                title=str(job.get("title") or ""),
                location=location,
                description=description,
                url=str(job.get("absolute_url") or ""),
                remote=is_remote_text(" ".join([location, description])),
                published_utc=published_utc,
                freshness_known=published_utc > 0,
            )
        )

    return postings


def ashby_jobs(source: dict[str, Any]) -> list[JobPosting]:
    board = str(source["board"]).strip()
    payload = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true")
    jobs = payload.get("jobs", [])
    postings: list[JobPosting] = []

    for job in jobs:
        address = ((job.get("address") or {}).get("postalAddress") or {})
        location_parts = [
            format_location_fragment(job.get("location") or ""),
            format_location_fragment(address.get("addressLocality") or ""),
            format_location_fragment(address.get("addressRegion") or ""),
            format_location_fragment(address.get("addressCountry") or ""),
        ]
        secondary_locations = [format_location_fragment(item) for item in job.get("secondaryLocations", []) if item]
        description = str(job.get("descriptionPlain") or strip_html(str(job.get("descriptionHtml") or "")))
        workplace_type = str(job.get("workplaceType") or "")
        location = ", ".join(part for part in location_parts + secondary_locations if part)
        published_utc = parse_timestamp(str(job.get("publishedAt") or ""))
        remote = bool(job.get("isRemote")) or workplace_type.lower() == "remote" or is_remote_text(location)
        postings.append(
            JobPosting(
                dedupe_id=f"ashby:{board}:{job.get('id')}",
                source_key=build_source_key(source),
                source_label=str(source.get("label") or board),
                source_kind="ashby",
                company=str(source.get("label") or board),
                title=str(job.get("title") or ""),
                location=location,
                description=description,
                url=str(job.get("jobUrl") or ""),
                remote=remote,
                published_utc=published_utc,
                freshness_known=published_utc > 0,
            )
        )

    return postings


def lever_jobs(source: dict[str, Any]) -> list[JobPosting]:
    site = str(source["site"]).strip()
    instance = str(source.get("instance", "global")).strip().lower()
    host = "api.eu.lever.co" if instance == "eu" else "api.lever.co"
    payload = fetch_json(f"https://{host}/v0/postings/{site}?mode=json")
    postings: list[JobPosting] = []

    for job in payload:
        categories = job.get("categories") or {}
        all_locations = categories.get("allLocations") or []
        location_parts = [str(categories.get("location") or "")]
        location_parts.extend(str(item) for item in all_locations if item)
        description = " ".join(
            part
            for part in [
                str(job.get("openingPlain") or ""),
                str(job.get("descriptionPlain") or ""),
                str(job.get("additionalPlain") or ""),
            ]
            if part
        )
        workplace_type = str(job.get("workplaceType") or "")
        location = ", ".join(part for part in location_parts if part)
        postings.append(
            JobPosting(
                dedupe_id=f"lever:{site}:{job.get('id')}",
                source_key=build_source_key(source),
                source_label=str(source.get("label") or site),
                source_kind="lever",
                company=str(source.get("label") or site),
                title=str(job.get("text") or ""),
                location=location,
                description=description,
                url=str(job.get("hostedUrl") or ""),
                remote=workplace_type.lower() == "remote" or is_remote_text(location),
                published_utc=0,
                freshness_known=False,
            )
        )

    return postings


def remotive_jobs(source: dict[str, Any]) -> list[JobPosting]:
    payload = fetch_json("https://remotive.com/api/remote-jobs")
    jobs = payload.get("jobs", [])
    postings: list[JobPosting] = []

    for job in jobs:
        location = str(job.get("candidate_required_location") or "")
        description = strip_html(str(job.get("description") or ""))
        published_utc = parse_timestamp(str(job.get("publication_date") or ""))
        postings.append(
            JobPosting(
                dedupe_id=f"remotive:{job.get('id')}",
                source_key=build_source_key(source),
                source_label=str(source.get("label") or "Remotive"),
                source_kind="remotive",
                company=str(job.get("company_name") or "Unknown"),
                title=str(job.get("title") or ""),
                location=location,
                description=description,
                url=str(job.get("url") or ""),
                remote=True,
                published_utc=published_utc,
                freshness_known=published_utc > 0,
            )
        )

    return postings


def remoteok_jobs(source: dict[str, Any]) -> list[JobPosting]:
    payload = fetch_json("https://remoteok.com/api")
    postings: list[JobPosting] = []

    for job in payload:
        if not isinstance(job, dict) or "id" not in job:
            continue
        location = str(job.get("location") or "")
        description = strip_html(str(job.get("description") or ""))
        published_utc = 0
        if job.get("epoch"):
            try:
                published_utc = int(job["epoch"])
            except (TypeError, ValueError):
                published_utc = 0
        if not published_utc:
            published_utc = parse_timestamp(str(job.get("date") or ""))
        postings.append(
            JobPosting(
                dedupe_id=f"remoteok:{job.get('id')}",
                source_key=build_source_key(source),
                source_label=str(source.get("label") or "Remote OK"),
                source_kind="remoteok",
                company=str(job.get("company") or "Unknown"),
                title=str(job.get("position") or ""),
                location=location or "Worldwide",
                description=description,
                url=str(job.get("url") or job.get("apply_url") or ""),
                remote=True,
                published_utc=published_utc,
                freshness_known=published_utc > 0,
            )
        )

    return postings


def jobicy_jobs(source: dict[str, Any]) -> list[JobPosting]:
    payload = fetch_json("https://jobicy.com/api/v2/remote-jobs")
    jobs = payload.get("jobs", [])
    postings: list[JobPosting] = []

    for job in jobs:
        location = str(job.get("jobGeo") or "")
        description = strip_html(str(job.get("jobDescription") or job.get("jobExcerpt") or ""))
        published_utc = parse_timestamp(
            str(job.get("pubDate") or job.get("publishedAt") or payload.get("lastUpdate") or "")
        )
        postings.append(
            JobPosting(
                dedupe_id=f"jobicy:{job.get('id')}",
                source_key=build_source_key(source),
                source_label=str(source.get("label") or "Jobicy"),
                source_kind="jobicy",
                company=str(job.get("companyName") or "Unknown"),
                title=str(job.get("jobTitle") or ""),
                location=location,
                description=description,
                url=str(job.get("url") or ""),
                remote=True,
                published_utc=published_utc,
                freshness_known=published_utc > 0,
            )
        )

    return postings


def wellfound_jobs(source: dict[str, Any]) -> list[JobPosting]:
    search_url = str(source.get("search_url") or "https://wellfound.com/jobs")
    html = fetch_text(search_url)
    if "wellfound.com" in search_url and "Please enable JS and disable any ad blocker" in html:
        raise urllib.error.HTTPError(search_url, 403, "Wellfound requires a browser challenge", None, None)
    return []


def collect_jobs(config: dict[str, Any]) -> list[JobPosting]:
    handlers = {
        "greenhouse": greenhouse_jobs,
        "ashby": ashby_jobs,
        "lever": lever_jobs,
        "remotive": remotive_jobs,
        "remoteok": remoteok_jobs,
        "jobicy": jobicy_jobs,
        "wellfound": wellfound_jobs,
    }
    postings: list[JobPosting] = []

    for source in config.get("sources", []):
        if not bool(source.get("enabled", True)):
            continue
        kind = str(source.get("kind", "")).strip().lower()
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"Unsupported source kind: {kind}")
        try:
            postings.extend(handler(source))
        except urllib.error.HTTPError as exc:
            label = source.get("label") or build_source_key(source)
            print(f"Skipping source {label}: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        except urllib.error.URLError as exc:
            label = source.get("label") or build_source_key(source)
            print(f"Skipping source {label}: network error {exc.reason}", file=sys.stderr)

    postings.sort(key=lambda job: (job.published_utc, job.company, job.title))
    return postings


def is_remote_text(value: str) -> bool:
    haystack = normalize_text(value)
    return any(term in haystack for term in REMOTE_TERMS)


def expand_region_terms(filters: dict[str, Any]) -> list[str]:
    terms = [normalize_text(str(term)) for term in filters.get("region_keywords", []) if str(term).strip()]
    for preset_name in filters.get("region_presets", []):
        preset = REGION_PRESETS.get(str(preset_name).strip().lower(), [])
        terms.extend(normalize_text(term) for term in preset)
    return sorted(set(term for term in terms if term))


def matches_filters(job: JobPosting, filters: dict[str, Any], cutoff_utc: int) -> tuple[bool, str]:
    title_text = normalize_text(job.title)
    search_text = normalize_text(" ".join([job.title, job.location, job.description]))

    title_keywords = [normalize_text(str(term)) for term in filters.get("title_keywords", []) if str(term).strip()]
    body_keywords = [normalize_text(str(term)) for term in filters.get("body_keywords", []) if str(term).strip()]
    exclude_keywords = [normalize_text(str(term)) for term in filters.get("exclude_keywords", []) if str(term).strip()]
    disallowed_region_terms = [
        normalize_text(str(term))
        for term in filters.get("disallowed_region_keywords", DISALLOWED_REGION_TERMS)
        if str(term).strip()
    ]
    region_terms = expand_region_terms(filters)
    allow_global_remote = bool(filters.get("allow_global_remote", True))
    require_region_match = bool(filters.get("require_region_match", True))
    remote_required = bool(filters.get("remote_required", True))
    title_matches = [term for term in title_keywords if term in title_text]
    body_matches = [term for term in body_keywords if term in search_text]

    for term in exclude_keywords:
        if term in search_text:
            return False, f"excluded by '{term}'"

    if title_keywords and not title_matches:
        return False, "title did not match target roles"

    if body_keywords and not body_matches:
        return False, "body did not match supporting keywords"

    if remote_required and not job.remote and not is_remote_text(search_text):
        return False, "role is not marked remote"

    for term in disallowed_region_terms:
        if term in search_text:
            return False, f"excluded by region restriction '{term}'"

    region_match = ""
    if require_region_match:
        global_match = allow_global_remote and any(term in search_text for term in GLOBAL_REMOTE_TERMS)
        if global_match:
            region_match = "global remote"
        else:
            matched_term = next((term for term in region_terms if term in search_text), "")
            if not matched_term:
                return False, "location does not show EMEA/Africa eligibility"
            region_match = matched_term

    if job.freshness_known and job.published_utc and job.published_utc < cutoff_utc:
        return False, "posting is outside the freshness window"

    reasons = []
    if title_matches:
        reasons.append(f"title matched {', '.join(title_matches[:2])}")
    if body_matches:
        reasons.append(f"supporting text matched {', '.join(body_matches[:2])}")
    if remote_required:
        reasons.append("remote")
    if region_match:
        reasons.append(f"region matched {region_match}")
    if job.freshness_known and job.published_utc:
        reasons.append(f"published {format_timestamp(job.published_utc)}")
    else:
        reasons.append("new since last poll")
    return True, "; ".join(reasons)


def collect_matches(config: dict[str, Any], state: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], int, int]:
    now_utc = int(time.time())
    lookback_hours = int(config.get("lookback_hours", DEFAULT_LOOKBACK_HOURS))
    cutoff_utc = now_utc - (lookback_hours * 3600)
    max_alerts = int(config.get("max_alerts_per_run", DEFAULT_MAX_ALERTS))
    seen_ids = set(str(item) for item in state.get("seen_ids", []))
    first_run = int(state.get("last_run_utc", 0)) == 0
    seed_on_first_run = bool(config.get("seed_on_first_run", True))
    all_jobs = collect_jobs(config)
    updated_seen = set(seen_ids)
    matches: list[dict[str, Any]] = []
    seeded_count = 0

    for job in all_jobs:
        if not job.url:
            continue
        if first_run and seed_on_first_run:
            updated_seen.add(job.dedupe_id)
            seeded_count += 1
            continue
        if job.dedupe_id in seen_ids:
            continue

        matched, reason = matches_filters(job, config.get("filters", {}), cutoff_utc)
        updated_seen.add(job.dedupe_id)
        if matched:
            matches.append({"job": job, "reason": reason})

    matches.sort(key=lambda item: item["job"].published_utc, reverse=True)
    return matches[:max_alerts], updated_seen, now_utc, seeded_count


def prune_seen_ids(seen_ids: set[str], max_seen_ids: int) -> list[str]:
    ordered = sorted(seen_ids)
    if len(ordered) <= max_seen_ids:
        return ordered
    return ordered[-max_seen_ids:]


def format_message(job: JobPosting, reason: str) -> str:
    lines = [
        "New job-board match",
        f"<b>Company:</b> {escape(job.company)}",
        f"<b>Source:</b> {escape(job.source_label)} ({escape(job.source_kind)})",
        f"<b>Title:</b> {escape(job.title)}",
        f"<b>Location:</b> {escape(job.location or 'Unknown')}",
        f"<b>Freshness:</b> {escape(format_timestamp(job.published_utc) if job.published_utc else 'new since last poll')}",
        f"<b>Why:</b> {escape(reason)}",
        f"<b>Link:</b> {escape(job.url)}",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print matches instead of sending Telegram alerts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not CONFIG_PATH.exists():
        print(f"Missing config file: {CONFIG_PATH}", file=sys.stderr)
        return 1

    config = load_json(CONFIG_PATH)
    state = load_state()

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not args.dry_run and (not bot_token or not chat_id):
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1

    try:
        matches, updated_seen, now_utc, seeded_count = collect_matches(config, state)
        if args.dry_run:
            if seeded_count:
                print(f"Seeded {seeded_count} existing job(s); no alerts on a first run.")
            elif not matches:
                print("No matches.")
            else:
                for item in matches:
                    print("-" * 80)
                    print(format_message(item["job"], item["reason"]))
        else:
            for item in matches:
                send_telegram_message(bot_token, chat_id, format_message(item["job"], item["reason"]))
            print(f"Sent {len(matches)} job alert(s).")
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not args.dry_run:
        next_state = {
            "last_run_utc": now_utc,
            "seen_ids": prune_seen_ids(updated_seen, int(config.get("max_seen_ids", DEFAULT_MAX_SEEN_IDS))),
        }
        save_json(STATE_PATH, next_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
