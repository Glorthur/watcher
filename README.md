# Job alert watchers

This repo now has two separate watchers:

- `watcher.py` checks selected Reddit communities every 30 minutes for strict instant alerts and sends one broader daily Telegram summary.
- `job_watcher.py` polls public job feeds every hour and sends Telegram alerts for fresh remote executive support, HR, operations, and CRM roles that look open to EMEA, including Africa.

## Reddit watcher

- GitHub Actions runs `watcher.py` every 30 minutes for strict alerts and once daily for a broader digest.
- The script fetches subreddit RSS feeds from the configured subreddits.
- Posts are matched against two rule sets in [`config.json`](C:\Users\USER\Desktop\Codex%20Home\config.json):
  - `instant_rule` for stricter, high-signal alerts
  - `summary_rule` for broader daily opportunities
- Matching posts are sent to Telegram.
- [`state.json`](C:\Users\USER\Desktop\Codex%20Home\state.json) is committed back to the repository so the next run avoids duplicate alerts and can build the daily digest.

## Files

- [`watcher.py`](C:\Users\USER\Desktop\Codex%20Home\watcher.py): Reddit polling and Telegram delivery.
- [`config.json`](C:\Users\USER\Desktop\Codex%20Home\config.json): subreddits and keyword filters.
- [`.github/workflows/reddit-job-alerts.yml`](C:\Users\USER\Desktop\Codex%20Home\.github\workflows\reddit-job-alerts.yml): scheduled GitHub Actions workflow.
- [`state.json`](C:\Users\USER\Desktop\Codex%20Home\state.json): seen-post state.

## Setup

1. Create a GitHub repository and push these files.
2. In the repository settings, enable GitHub Actions read/write access for workflows.
3. Add repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Run the workflow manually once with `workflow_dispatch` to verify Telegram delivery.
5. Edit [`config.json`](C:\Users\USER\Desktop\Codex%20Home\config.json) any time you want to tune subreddits or either filter tier.

## Direct job-board watcher

The direct watcher avoids stale search-engine results by polling company ATS feeds and public remote-job APIs directly:

- Greenhouse: `boards-api.greenhouse.io`
- Ashby: `api.ashbyhq.com/posting-api/job-board/...`
- Lever: `api.lever.co/v0/postings/...`
- Remotive: `remotive.com/api/remote-jobs`
- Remote OK: `remoteok.com/api`
- Jobicy: `jobicy.com/api/v2/remote-jobs`

It filters for:

- target titles such as `founder's associate`, `executive assistant`, and `chief of staff`
- HR, people ops, payroll, operations coordinator, operations analyst, and Salesforce / CRM roles
- remote roles
- EMEA/Africa eligibility keywords in the location or description
- fresh jobs only when the source exposes publish timestamps

For sources like Lever that do not expose a publish timestamp in the public postings API, the watcher treats "new since the last poll" as fresh and seeds existing listings on the first run so you do not get spammed with old roles.

Wellfound is not in the live poller. Their public jobs pages currently return a browser challenge / `403` to plain GitHub Actions requests, so pretending that source works would be fake. If you want Wellfound too, the real options are browser automation with a logged-in session or native Wellfound email alerts forwarded into your alert flow.

Files:

- [`job_watcher.py`](C:\Users\USER\Desktop\Codex%20Home\job_watcher.py): ATS polling and Telegram delivery.
- [`job_config.json`](C:\Users\USER\Desktop\Codex%20Home\job_config.json): sources and job filters.
- [`job_state.json`](C:\Users\USER\Desktop\Codex%20Home\job_state.json): dedupe state for direct job alerts.
- [`.github/workflows/job-board-alerts.yml`](C:\Users\USER\Desktop\Codex%20Home\.github\workflows\job-board-alerts.yml): hourly GitHub Actions workflow.

How to tune it:

1. Edit [`job_config.json`](C:\Users\USER\Desktop\Codex%20Home\job_config.json) if you want to swap out the starter list of 15 curated sources.
2. Add the same Telegram secrets used by the Reddit watcher:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Run the workflow once with `workflow_dispatch`.
4. The first run seeds current listings and sends nothing. After that, you only get pings for new matches.

How to find source IDs:

- Greenhouse: the token is the segment after `job-boards.greenhouse.io/`
- Ashby: the board is the segment after `jobs.ashbyhq.com/`
- Lever: the site is the segment after `jobs.lever.co/` or `jobs.eu.lever.co/`

Local smoke test:

```powershell
python job_watcher.py --dry-run
```

## Telegram setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Send a message to your bot from the Telegram account that should receive alerts.
3. Find your chat ID.

You can get the chat ID by opening:

`https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`

Then look for `message.chat.id` in the JSON response.

## Notes

- GitHub Actions is not real-time. Expect delays around the 10-minute schedule plus any GitHub queueing.
- GitHub Actions is not real-time. Expect delays around the 30-minute schedule plus any GitHub queueing.
- The daily summary currently runs at 13:00 UTC. Change the second cron line in [`.github/workflows/reddit-job-alerts.yml`](C:\Users\USER\Desktop\Codex%20Home\.github\workflows\reddit-job-alerts.yml) if you want a different time.
- Some listed subreddits are discussion-heavy. Tighten `instant_rule` if alerts get noisy, or widen `summary_rule` if the daily digest feels too narrow.
- If you want faster alerts later, move the same script to an always-on VPS and change the poll interval.
