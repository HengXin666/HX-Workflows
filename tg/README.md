# TG workflow orchestrator

This folder contains a small uv-managed Python runner for composable Telegram-related scheduled tasks.

## Design

GitHub Actions is only the coarse wake-up timer. The real orchestration lives in `tg/tasks.yml`.

That means you can add, disable, reorder, or chain tasks without editing `.github/workflows/tg-orchestrator.yml` every time.

## Files

```text
.github/workflows/tg-orchestrator.yml  # GitHub Actions entry point
tg/pyproject.toml                      # uv project
tg/runner.py                           # task orchestrator
tg/tasks.yml                           # active schedule config
tg/tasks.example.yml                   # extra examples
tg/scripts/example_task.py             # smoke-test task
```

## Supported schedule expressions

```yaml
schedule:
  - every:30       # every 30 minutes
  - hourly:05      # every hour at minute 05
  - daily:08:30    # every day at 08:30 in configured timezone
  - cron:08:30     # alias of daily:08:30
```

The default timezone is `Asia/Tokyo`.

## Manual runs

Open GitHub Actions -> `TG Orchestrator` -> `Run workflow`.

Useful modes:

```text
list     list tasks
due      print tasks due right now
run-due  run tasks due right now
run-all  run all enabled tasks
run      run the selected task id
```

For `mode=run`, set `task_id`, for example:

```text
tg-example
```

## Multi-account pattern

Put one session per line in the GitHub Actions Secret named `TG_SESSION_STRINGS`.

Then enable a task like this:

```yaml
- id: tg-example-multi-account
  enabled: true
  schedule:
    - daily:08:35
  foreach_secret_lines: TG_SESSION_STRINGS
  command: uv run python scripts/example_task.py --account "$TG_ACCOUNT_INDEX"
```

For each line, the runner injects:

```text
TG_SESSION_STRING=<that line>
TG_ACCOUNT_INDEX=1,2,3...
```

## Secrets

Recommended GitHub Secrets:

```text
TG_SESSION_STRINGS       # one Telegram session string per line
TG_PROXY                 # optional proxy
TG_FORWARD_BOT_TOKEN     # optional notification/forwarding bot token
TG_FORWARD_CHAT_ID       # optional target chat id
```

Do not commit Telegram sessions, bot tokens, phone numbers, or verification codes.

## Local test

```bash
cd tg
uv sync
uv run python runner.py list
uv run python runner.py run tg-example
```
