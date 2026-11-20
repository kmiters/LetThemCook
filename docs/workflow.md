# Work with your Chef

[LetThemCook home](../README.md) · [Setup](setup.md) · [Run operations](operations.md)

## Give an order and follow up

Tell the Chef what you want, any boundaries, and how you want to check the result. The Chef records the brief, chooses a Cook, reviews the diff and checks, and brings you a result. Mention any edits still in your main checkout. The Cook starts from a committed revision and those edits are outside its worktree.

You can say “Add a keyboard shortcut for search. Keep the existing button and show me how to try both.” If you want changes after review, tell the Chef what to revise so it can follow up on the same ticket.

## Open the task board

Ask the Chef to open the board, or run these from the LetThemCook checkout. Replace `my-app` with the registered project name.

```sh
bin/ltc-tasks my-app
bin/ltc-tasks --no-open my-app
```

Both print the board URL. The first also tries to open your browser. If LetThemCook's `bin` is on PATH, you can run `ltc-tasks` from your registered checkout or a linked worktree. The launcher infers the project when there is a unique match. If it cannot identify one, it opens the project index. A supplied project name takes precedence.

The read-only board has To do, In progress, Blocked, and Done columns. Cancelled tasks count toward the total and remain accessible by ticket URL. The board checks for updates every ten seconds while visible. Open a ticket for the brief, notes, and saved Cook reports. A report can include a review URL and the checks the Cook ran.

The board is served on `127.0.0.1` and is usable from that machine. [Board service and ticket URLs](operations.md#board-service)

## Review a new HTML artifact

Use the registered Chef or Cook terminal to open a self-contained HTML file for review. Inline its local assets first. The command copies only that file into a private review directory and leaves the source and its siblings untouched.

```sh
bin/ltc-lavish html my-app proposal.html
bin/ltc-lavish inbox my-app --wait 30
bin/ltc-lavish read my-app BATCH
bin/ltc-lavish ack my-app BATCH
```

Share the returned URL once the receiver shows `listening`. Keep waiting in the owner's active turn, renewing the wait after a timeout. The Chef's usual `ltc-wait my-app` also returns Chef-owned IDs under `lavish_batches`. Use the returned member name with `--member` for workspace reads and acknowledgments. Read the full batch before acknowledging it. An unanswered ID comes back on the next inbox read, even after the final submission closes the preview. The same workflow applies to new Herdr `open` and `report` reviews.

## Annotate a web preview with Lavish

Ask the Chef to capture a specific HTTP(S) preview for a registered project. From the LetThemCook checkout, run the following with your real project and URL.

```sh
bin/ltc-lavish open my-app http://127.0.0.1:8080/settings
```

From a registered Herdr Chef or Cook, `open` starts a receiver for that review. Share the local `url` when the result shows `receiver.state` as `listening`. Keep the `review` ID. In annotate mode, select a heading, label, or control and queue a note, or write a message in the review panel. **Send to Agent** files one batch as a **To do** task in the selected backlog. The receiver waits again after each delivery. **Send & End** files the final batch and closes the managed preview. Notes still in the browser composer or queue are drafts. The Chef triages the evidence before assigning work or changing a project.

```sh
bin/ltc-lavish status my-app
bin/ltc-lavish status my-app REVIEW_ID
bin/ltc-lavish resume my-app REVIEW_ID
bin/ltc-lavish end my-app REVIEW_ID
```

On return to the project, use `status` to find its unfinished managed reviews. Inspect the receiver for the selected review. Use `resume` from its registered Chef or Cook to verify or restart reception at the existing link. Use `end` to drain submissions, release the server and confirm the receiver exited. It is safe to repeat.

Outside Herdr, `open` and `report` return `foreground_required`. You can also select this mode with `--foreground` in an ordinary Herdr shell. Run `bin/ltc-lavish import my-app REVIEW_ID` in the foreground for each batch. Check `status` for an active foreground receiver before handing someone a monitored link. Run `end` to finish cleanup.

For a workspace, append `--member REPOSITORY_NAME` to every command. Use the same registry throughout. Set `LTC_DB` or pass `--db PATH` before the subcommand. [Lavish requirements, privacy, and recovery](operations.md#lavish-review-runtime)

### Annotate a saved Cook report

To discuss a Cook's result, open the report itself. Supply the registered project and task ID. Append the same `--member REPOSITORY_NAME` to each command for workspace tasks.

```sh
bin/ltc-lavish report my-app 12
bin/ltc-lavish report my-app 12 --report-id 7
```

The first command selects the latest saved report for task 12. The second selects report 7 from that task's history. Open the returned `url`. Select text in the outcome or click a validation line and queue an annotation. **Open review** follows the saved report's optional preview link in a new tab when using Lavish's explore mode.

Report reviews use the same receiver, status, resume and end commands as preview captures. Imported feedback identifies the source project, task, run and saved report.

## Check progress

Ask the Chef what is running or blocked. From the LetThemCook checkout, use `bin/ltc-status my-app` for tasks and recorded terminal status, or `bin/ltc-status --all my-app` for full history.

If you've enabled the [optional agent shortcuts](configuration.md#optional-agent-shortcuts), use `$tasks` in Codex or `/orders` in Claude Code to open the board. Use `$whatsup` in Codex or `/whatsup` in Claude Code for summarized progress and a board link. The progress summary sends recent terminal excerpts to the configured model provider. [What the progress helper reads](operations.md#progress-evidence)

## Review and accept the dish

For runnable UI or web work, the Chef shares a preview of the Cook's changes, steps to try the scenario, any required setup, and when access ends. The Cook keeps their preview available while the task awaits your acceptance, or through an agreed handoff. If a preview cannot be provided or a server does not apply, the Chef explains why and links the task ticket or project board. If the board is unavailable, use the task ID to follow up.

If you're happy with the result, explicitly accept that task. Acceptance authorizes the final commit of the approved changes and integration into local main. An earlier commit, a push, opening a pull request, updating one, or permanently discarding a worktree each needs its own explicit authorization. A saved report, passing checks, a watcher event, and Chef review leave your acceptance pending.

After acceptance, the Chef verifies the approved work on local main before marking the task Done and running the guarded cleanup. If integration or cleanup is blocked, the Chef explains what needs attention. [Integration and worktree cleanup](operations.md#integration-and-worktree-cleanup)
