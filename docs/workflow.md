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

## Check progress

Ask the Chef what is running or blocked. From the LetThemCook checkout, use `bin/ltc-status my-app` for tasks and recorded terminal status, or `bin/ltc-status --all my-app` for full history.

If you've enabled the [optional agent shortcuts](configuration.md#optional-agent-shortcuts), use `$tasks` in Codex or `/orders` in Claude Code to open the board. Use `$whatsup` in Codex or `/whatsup` in Claude Code for summarized progress and a board link. The progress summary sends recent terminal excerpts to the configured model provider. [What the progress helper reads](operations.md#progress-evidence)

## Review and accept the dish

For runnable UI or web work, the Chef shares a preview of the Cook's changes, steps to try the scenario, any required setup, and when access ends. The Cook keeps their preview available while the task awaits your acceptance, or through an agreed handoff. If a preview cannot be provided or a server does not apply, the Chef explains why and links the task ticket or project board. If the board is unavailable, use the task ID to follow up.

If you're happy with the result, explicitly accept that task. Acceptance authorizes the final commit of the approved changes and integration into local main. An earlier commit, a push, opening a pull request, updating one, or permanently discarding a worktree each needs its own explicit authorization. A saved report, passing checks, a watcher event, and Chef review leave your acceptance pending.

After acceptance, the Chef verifies the approved work on local main before marking the task Done and running the guarded cleanup. If integration or cleanup is blocked, the Chef explains what needs attention. [Integration and worktree cleanup](operations.md#integration-and-worktree-cleanup)
