# Operate and troubleshoot runs

[LetThemCook home](../README.md) · [Everyday workflow](workflow.md) · [Configuration](configuration.md)

## Registry and run history

Use `LTC_DB` to select a registry across commands. Where supported, `--db PATH` overrides it. Pass the flag before subcommands. Run the examples from the LetThemCook checkout, substituting real project names and IDs.

```sh
bin/ltc-db project list
bin/ltc-db project show my-app
bin/ltc-db task list --project my-app
bin/ltc-db task show 12
bin/ltc-db run list --task 12
bin/ltc-db run show 7
bin/ltc-status --all my-app
```

Registry commands return JSON. Project names, checkout paths and assigned Space IDs are unique. Task statuses are `todo`, `in_progress`, `blocked`, `done`, and `cancelled`. Task and run IDs are separate. The brief flags are `--intent`, `--context`, `--scope`, `--acceptance`, and `--checks`. Update fields independently with `task update` and pass an empty value to clear a field. The separate `--description` field replaces the existing notes on update. Legacy tasks without brief fields remain readable.

Run launch states (`starting`, `started`, `delivery_failed`, `failed`, and `unknown`) describe a recorded attempt. Use `ltc-status` for live observations. It shows active tasks and their latest runs, plus other runs whose terminals are live or unverified. Use `--all` for complete history, including finished runs and verified closures. Missing terminals or mismatched identities can appear as unknown. Confirm what happened before retrying or cleaning up. Status and the watcher flag a start without a recorded terminal after five minutes.

## Dispatch a Cook

The Chef records a self-contained brief and launches the Cook from its verified terminal in the assigned Herdr Space. Use the Chef's assigned registry via `LTC_DB`.

```sh
bin/ltc-db task add my-app 'Fix the settings save button' \
  --intent 'Make settings save successfully' \
  --context 'The settings page currently ignores Save' \
  --scope 'Settings page' --acceptance 'Changes survive reload' \
  --checks 'Reopen settings after saving'
bin/ltc-cook my-app 12 --profile routine
```

Replace `12` with the ID returned by task creation. Select `--harness claude` or `--harness pi` as needed. `ltc-cook PROJECT TASK_ID [WORKTREE_NAME] [PROFILE]` also accepts the legacy positional profile. Use either that position or `--profile`. `--config PATH` chooses a [Cook configuration](configuration.md#prompts-and-alternate-configs).

The default worktree is `.letthemcook/worktrees/task-TASK_ID` under the project checkout or workspace member. Custom names use 1–32 lowercase letters, digits, hyphens or underscores. The new worktree starts detached from committed `HEAD`. The lower-level `ltc` accepts `--base` for another committed revision and records a launch only with `--task-id`. Uncommitted edits and local files such as an untracked `.env` stay in the original checkout. Set up required inputs and dependencies in the worktree. LTC adds `/.letthemcook/` to the local Git `info/exclude`.

A registered launch records the attempt before creating the worktree. Check its route, worktree, terminal, and first-message status when present. If delivery fails, inspect the terminal and the run before retrying. An unknown response can conceal a created terminal. Failed startup may leave the worktree in place. Inspect and preserve it before choosing a distinct worktree for a new launch.

Use `hc get ADDRESS`, `hc read ADDRESS --lines 80` or `hc wait ADDRESS --timeout 60` in the recorded Herdr session to inspect progress. If a Cook is missing from the visible layout, inspect its address and Space first. Check the app's command list for `terminal.keep` if you need to attach that terminal as a tab. Send follow-ups to a live Cook by run ID using the assigned registry.

```sh
bin/ltc-cook-control send 7 'Please verify the value survives a reload.'
```

Control commands verify terminal identity in the recorded session against the registry, project, task, run and Space, including the member for workspace runs. Older launches without matching labels require manual inspection. Identity checks and Herdr actions are separate calls.

## Durable Cook reports

From the verified live Cook terminal, save the result before notifying the Chef. Use the assigned database path and replace the example URL with the verified preview or task ticket. Omit `--review-url` when unavailable.

```sh
bin/ltc-report --db /absolute/path/to/registry.sqlite3 \
  --outcome 'Fixed the settings save button in the assigned worktree' \
  --validation 'Verified persistence after reload. Cross-browser checks were skipped.' \
  --review-url http://127.0.0.1:8080 --notify
```

Include the worktree, changed files, actual checks and omissions, blockers, and preview lifetime as applicable. Outcome and validation each support up to 4,000 characters. The optional URL must be absolute HTTP(S) and at most 2,048 characters. Each run can save up to 20 distinct reports.

If notification fails after saving, retry the identical report from the Cook terminal. It reuses the report ID and retries delivery when needed. A lost acknowledgement or concurrent notifications can produce duplicates. The board card shows the latest outcome. The ticket detail and `ltc-db task show` include up to 20 recent reports. The Chef still needs explicit user acceptance before final integration.

Use the verified Chef return address with `hc send ADDRESS MESSAGE` for questions. A report can target the currently bound Chef through `--notify`.

## Cook watcher

At startup the Chef runs `bin/ltc-watch start my-app` and reads `bin/ltc-status my-app`. The watcher launch binds the live Chef terminal and starts or reuses a separate process in Herdr. Use the assigned registry and session with no `HC_SOCKET` override. A new Chef can register itself as the destination.

The watcher polls every 15 seconds by default, without calling a model. It checks up to eight Cook terminals concurrently and records events for observed done, blocked, error, finished or unknown states and launch problems. A Cook's saved report and its notification come from `ltc-report`. A separate watcher event can prompt the Chef to inspect the same result.

Watchers suppress repeated observations of unchanged states and retry pending events in batches of up to 20 after verifying the Chef destination. Delivery can repeat after a lost acknowledgement. Short transitions between checks can be missed. One active watcher holds the project and registry lock. If the watcher dies, inspect its terminal. The Chef tab sync loop does not restart it. When the normal watcher is stopped, use `bin/ltc-watch run my-app --once` to observe and deliver one cycle for diagnosis.

## Board service

Run `bin/ltc-tasks --db /absolute/path/to/registry.sqlite3 --no-open -- my-app` to get the actual project URL. Append `/tasks/TASK_ID` with the real task ID for a ticket URL, and verify it before sharing. The board needs an existing registry and serves read-only views on loopback. Other local processes can access it.

The default port is stable for a resolved registry path. If occupied by another service, choose a free `--port NUMBER` and reuse that port for links. The launcher reuses its registry's listener and prints its PID to stderr. The detached listener lasts until stopped or the machine restarts. Run the launcher again to restore the URL at the same port. For an owned foreground listener, start `bin/ltc-tasks --serve my-app` and stop it with Ctrl-C when finished.

## Progress evidence

```sh
bin/ltc-whatsup -- my-app
bin/ltc-whatsup --summarize codex --language Polish -- my-app
bin/ltc-whatsup --summarize claude -- my-app
```

The first command prints read-only JSON. The others send bounded excerpts to the selected CLI's configured provider for a one-shot summary. Codex requests `gpt-6-luna` at low effort. Claude Code requests `haiku`, and the Claude skill also requests that alias for its invoking turn. Failed synthesis falls back to verified status with an explanation. The summary includes the board link when available, starting or reusing the board without opening a browser.

The snapshot uses the same run scoping as `ltc-status`. It verifies each terminal identity before and after reading, requests up to 24 lines of history plus the visible screen, and caps the returned excerpt at 45 lines and 5,000 characters. The read budget is five seconds and 256 KiB. A report or blocker outside that excerpt can be missing. An idle agent may have a response ready. Inspect the ticket and worktree before drawing conclusions.

## Resume a Cook

If the Cook has stopped and the task still needs work, update the brief with what the next session needs. From the verified Chef terminal, in the same registry and recorded Herdr session, run `bin/ltc-resume RUN_ID` using the latest run for that worktree. Keep the previous terminal record available. Resume verifies labels, worktree ownership and a `finished` state before recording a new run in the same worktree. It refuses an unknown or missing terminal, a newer run, or an `HC_SOCKET` override. Check any intentionally detached processes separately.

Newer runs reuse the recorded harness, profile, model and effort even if the profile changes. Null settings use the CLI defaults. Historical Codex-only runs resolve their saved profile against the selected config. Supply the original `--config PATH` if the mapping changed. Every resumed Cook gets the current task brief.

Codex, Pi and legacy Claude runs without a recorded conversation UUID start a fresh conversation. New registered Claude runs get a UUID via `--session-id`. By default resume requests that exact UUID with `claude --resume`. Use `bin/ltc-resume RUN_ID --native` to require native recovery. LTC checks the previous terminal's UUID label and a matching local transcript for the worktree. Use the same `CLAUDE_CONFIG_DIR` as the Cook if configured. Missing, ambiguous or unsupported transcript data blocks native recovery. [Claude Code CLI flags](https://code.claude.com/docs/en/cli-reference) · [Claude Code session storage](https://code.claude.com/docs/en/sessions)

A result of `native_requested` confirms Herdr returned a terminal for the request. Inspect its first-message status and terminal to confirm Claude opened the conversation and handle any trust or resume prompts. To deliberately start fresh in that worktree, use `bin/ltc-resume RUN_ID --fresh` with the latest verified finished run. A fresh Claude session receives a new UUID.

## Temporary processes and review previews

Record an identifiable owner for processes started for the task. Stop owned test processes through their owning tools when checks end, including failed or interrupted checks, and verify they exited. Leave shared and user-owned processes alone. For Chrome tests with `chrome-devtools-axi`, use a unique session name and a short idle timeout.

```sh
CHROME_DEVTOOLS_AXI_SESSION=my-app-task-12 CHROME_DEVTOOLS_AXI_IDLE_TIMEOUT_MS=120000 \
  chrome-devtools-axi open URL
CHROME_DEVTOOLS_AXI_SESSION=my-app-task-12 chrome-devtools-axi stop
```

Verify the owned browser and helpers exited. Keep the preview server separate, with a recorded owner and stop point. Use a free port when needed. Open the URL and exercise the changed scenario from the review environment. Report the worktree, host and port, review steps and setup, what was verified, and how long the server will be available. Keep the preview running until acceptance or the agreed handoff. Report access changes, then stop the owned server and verify it exited when it is no longer needed.

## Integration and worktree cleanup

Leave changes uncommitted until explicit acceptance unless the user explicitly authorizes an earlier commit. The Chef reviews the diff and checks before asking for acceptance. Once accepted, the Chef coordinates a final commit and local integration of the approved changes in the assigned repository. A Cook may do this only if the Chef relays acceptance and authorizes the exact task and action. Preserve unrelated work. If integration is blocked, report it and preserve the worktree. Pushes and PR creation or updates each need separate authorization.

Verify local main, record the outcome, mark the accepted task `done`, and inspect the cleanup report before closing the run.

```sh
bin/ltc-cook-control check RUN_ID
bin/ltc-cook-control close RUN_ID
```

`check` returns JSON. Inspect `safe_to_remove` even when the command exits successfully. The target is the local branch named by `origin/HEAD` if configured, otherwise the single existing local `main` or `master`. Cleanup checks registered worktree ownership, a clean worktree including ignored and untracked files, base and commit ancestry, and matching content on local main for every path changed since the base. Cherry-picks or later edits to a changed path can block automated cleanup.

`close` checks terminal identities again and requires other recorded Cook terminals for that worktree to be verified finished, except failed launches with no terminal. It refuses a selected live Cook reported as working or blocked. The checks and the Herdr and Git operations are separate. Avoid concurrent changes during cleanup. A verified closure is recorded after worktree and selected terminal removal. Inspect failures before retrying, especially if the worktree was already removed and only terminal cleanup remains.

If the checks cannot confirm the work is integrated, retain the worktree and explain the blocker. Only explicit user authorization to permanently discard that specific worktree permits `bin/ltc-cook-control close RUN_ID --discard-changes` on a `done` or `cancelled` task. This can delete local files, including ignored files, and discard unlanded commits. Worktree ownership, terminal identity and branch resolution checks still apply.
