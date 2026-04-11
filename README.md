# Reddit job alerts

This project checks selected Reddit communities every 10 minutes and sends Telegram alerts for matching posts.

## How it works

- GitHub Actions runs `watcher.py` on a 10-minute schedule.
- The script fetches subreddit RSS feeds from the configured subreddits.
- Posts are matched against keyword rules in [`config.json`](C:\Users\USER\Desktop\Codex%20Home\config.json).
- Matching posts are sent to Telegram.
- [`state.json`](C:\Users\USER\Desktop\Codex%20Home\state.json) is committed back to the repository so the next run avoids duplicate alerts.

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
5. Edit [`config.json`](C:\Users\USER\Desktop\Codex%20Home\config.json) any time you want to tune subreddits or filters.

## Telegram setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Send a message to your bot from the Telegram account that should receive alerts.
3. Find your chat ID.

You can get the chat ID by opening:

`https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`

Then look for `message.chat.id` in the JSON response.

## Notes

- GitHub Actions is not real-time. Expect delays around the 10-minute schedule plus any GitHub queueing.
- Some listed subreddits are discussion-heavy. Tighten `required_keyword_groups` and `exclude_keywords` if noise gets bad.
- If you want faster alerts later, move the same script to an always-on VPS and change the poll interval.
