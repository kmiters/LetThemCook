# LetThemCook (LTC)

LetThemCook is a lightweight orchestration workflow for Herdr Desktop and Codex. You talk to the **Chef**; the Chef starts a fresh **Cook** for each implementation task, supervises the work and reviews the result.

## Requirements

- Git, Python 3, Herdr Desktop and `hc`.
- Codex CLI with support for `--no-daemon`.
- Permission for Git to create worktrees and for `hc` to access Herdr's core. The tools do not bypass sandbox or approval settings.

## Project Chefs and the registry

Every registered project can have one Chef in its own Herdr Space. The Chef works on that project only, starts Cooks for individual implementation tasks, reviews their reports and closes their terminals after the user accepts the completed task and its work is verified on the local main branch. The corresponding worktree is then removed.

The registry is a SQLite database created on first use at `.letthemcook/registry.sqlite3`, ignored by Git. Python's standard library supplies SQLite. Back up that file to preserve the registry. Set `LTC_DB` or pass `--db /path/to/registry.sqlite3` before the subcommand to use another database.

Find Space IDs with `herdr-app ls`, then link each project explicitly:

```sh
bin/ltc-project p0werusers /path/to/p0werusers SPACE_ID
# For an existing project:
bin/ltc-db project update p0werusers --space SPACE_ID
bin/ltc-chef
```

`ltc-chef` starts a Codex tab named `👨‍🍳 Chef @ p0werusers` in each registered, open Space, passing `--approve-for-me` to Codex. Run it again after opening a Space or registering another project; it leaves an existing tab of that name or the legacy name `Chef @ p0werusers` alone. Existing tabs are not renamed or restarted, so their permission mode is unchanged. To sync one project, use `bin/ltc-chef p0werusers`. It reports missing Spaces and does not assume that a Space's displayed name or folder identifies the checkout. It supports local Spaces only. For continuous synchronization while Herdr is open, run `bin/ltc-chef --watch` in a persistent terminal; it checks every 15 seconds (configurable with `--interval`). Start the watcher again after it stops; it is not installed as a login service. An existing same-named tab prevents a duplicate even if its agent has stopped, so the watcher does not automatically restart that agent. Inspect an existing same-named tab before closing it and re-running the command. Agent startup may require interaction in Herdr (for example a trust prompt).

```sh
bin/ltc-db project list
bin/ltc-db project show p0werusers
bin/ltc-status p0werusers
bin/ltc-db project update p0werusers --path /new/path/to/p0werusers
bin/ltc-db task add p0werusers 'Implement feature' --intent 'User request' --context 'Relevant context' --scope 'Boundaries' --acceptance 'Acceptance criteria' --checks 'Verification'
bin/ltc-db task list --project p0werusers
bin/ltc-db task list --status todo
bin/ltc-db task show 1
bin/ltc-db run list --project p0werusers
bin/ltc-db run list --task 1
bin/ltc-db run show 1
bin/ltc-db task update 1 --status in_progress
bin/ltc-db task update 1 --status done --description 'Outcome and validation'
```

`ltc-status` reports project tasks and recorded Cook launches and checks terminal identity and status in their recorded Herdr sessions. It is read-only; unavailable Herdr, missing terminals, and identity mismatches are reported as unknown, not as proof that a Cook has stopped. At startup the Chef runs it once.

Commands return JSON. Project names, checkout paths and assigned Space IDs are unique; paths must point to an existing Git checkout. Task IDs are stable within the database. Statuses are `todo`, `in_progress`, `blocked`, `done`, and `cancelled`. Brief fields are `--intent`, `--context`, `--scope`, `--acceptance`, and `--checks`; each is optional and can be updated independently with `task update` (pass an empty value to clear one field). `--intent` records the user request; other fields describe the context and execution. `task show` includes the brief. The existing `--description` remains available as additional notes, and updating it replaces its prior value. Legacy tasks without brief fields continue to work. No records or project files are deleted by these commands. Use `--help` for options.

## Configure models and prompts

Edit `config.json`: each named profile has a description, `model`, and `reasoning_effort`. Chef selects a profile based on the task and explains the choice. `null` means use the Codex default; the supplied profiles deliberately do not assume which models your account can access. `reasoning_effort` can be `low`, `medium`, `high`, `xhigh`, or `null`. Specify actual model IDs supported by your account, for example:

```json
"simple": {"description": "Small and well-scoped tasks", "model": "YOUR_MODEL_ID", "reasoning_effort": "low"}
```

Edit `prompts/chef.md` and `prompts/cook.md` to change their roles. A separate JSON file passed with `--config` can select other profiles or prompt paths; prompt paths are resolved relative to that config file. `ltc-chef` loads the default Chef prompt at launch; `prompts.chef` is a configuration reference and is not automatically loaded into an already-running agent. The Cook prompt is loaded from the selected config at launch. These instructions do not override higher-priority instructions or tool permissions.

## Launch a Cook

The Chef records a self-contained brief using the task fields, then launches the Cook from its Herdr terminal:

```sh
/path/to/LetThemCook/bin/ltc-cook p0werusers TASK_ID [WORKTREE_NAME] [PROFILE]
```

Each launch via `ltc-cook` records an attempt in SQLite before creating its worktree, including the task, project, Space, worktree, base commit, hc session and terminal address when known. `run list` and `run show` return these records. Launch states describe the last recorded launch outcome: `starting`, `started`, `delivery_failed`, `failed`, or `unknown`. They are historical records, not live agent status; check Herdr before acting. `unknown` may mean a terminal was created even when the launcher did not receive a usable response, so inspect before retrying. A stopped launcher can leave `starting`. Direct `ltc` calls record a launch only when supplied `--task-id` (and `--db` or `LTC_DB` for a non-default registry).

The Bash wrapper reads the project path, Space and brief from the registry (`LTC_DB` selects a non-default database), checks the task belongs to the project, and passes the Chef terminal address to the launcher. The default worktree name is `task-TASK_ID` and the default profile is `default`; choose a new worktree name when retrying an existing one.

The launcher creates a detached Git worktree at `/path/to/project/.letthemcook/worktrees/task-name` and launches Codex there in a new Herdr terminal. Names must be unique and use 1–32 lowercase letters, digits, hyphens or underscores. It labels the terminal with the space ID for visibility. Check `herdr-app ls`; if the app did not show the terminal, inspect it by the returned address and use `herdr-app do terminal.keep --terminal ADDRESS --place tab` when appropriate.

Worktrees start at `HEAD`, or a committed revision specified by `--base`. They **do not include uncommitted files or changes** from the main checkout; the output flags when those exist. Chef must account for them before delegating. The launcher adds `/.letthemcook/` to the local Git `info/exclude`, not the tracked `.gitignore`. It does not install dependencies or copy ignored files. If starting the Cook fails after worktree creation, the path is reported and preserved; inspect it before retrying. The launcher never removes worktrees; the guarded close operation below can remove them after acceptance and verification.

The launcher gives the Cook the Chef return address from `HC_TERMINAL_ID` (or `--chef ADDRESS`) so it can report with `hc send`. Check `first_message.sent` in the launch result and inspect the session if delivery failed. Use `hc get ADDRESS`, `hc wait ADDRESS --timeout 60` and `hc read ADDRESS --lines 80` to supervise a Cook. Use `bin/ltc-cook-control send RUN_ID MESSAGE` for Cook follow-ups and `bin/ltc-cook-control close RUN_ID` after user acceptance and marking the task `done`. These commands verify registry, run, task, project and Space labels against the recorded terminal in its recorded hc session; they refuse if identity cannot be established. The check and action are separate Herdr calls, not an atomic operation. Old launches without labels cannot be controlled by these commands and require manual inspection. `close` checks and removes the worktree as described below. A new conversation limits inherited chat context, not access to files or tools allowed by the Cook's sandbox. Cooks can read project instructions and other permitted files. Do not run simultaneous conflicting tasks without coordination.

## Supervise Cooks without continuous model use

At session startup, each Chef runs:

```sh
bin/ltc-watch start PROJECT
```

This registers the current Chef terminal and starts a project watcher in Herdr. It checks recorded Cook sessions every 15 seconds without calling a model, and sends the Chef events for a Cook report, blocker, error, finished terminal, uncertain terminal state or failed launch. It checks terminal identity before sending, retains undelivered events in SQLite, and suppresses repeated reports of an unchanged state. The Chef must inspect the actual result; an event never means the user accepted a task.

The watcher runs as a separate Herdr process, with one active watcher per project and database. Starting a new Chef registers its terminal as the destination. If that terminal is blocked, unavailable or fails identity checks, events stay pending and are retried. Delivery is at least once: if a send succeeds but its confirmation is lost, an event can be sent again. Transitions that occur entirely between checks or while the watcher is stopped cannot be reconstructed. Chef startup starts or reuses the watcher, but the system does not automatically restart it if its process dies while the Chef remains running; inspect the watcher terminal if supervision appears silent. `ltc-watch run PROJECT --once` performs one observation cycle for diagnosis. Use the same `LTC_DB` and Herdr session as the Chef, without an `HC_SOCKET` override.

## Resume a Cook

If a Cook session ends before the task is accepted, the Chef can resume the task in the existing worktree:

```sh
bin/ltc-resume RUN_ID
```

Run this from the Chef terminal in the recorded `HC_SESSION`, with the same `LTC_DB`. The command checks that the run is the latest for the worktree, its terminal is still present, has matching labels, and is reported `finished`. It also checks the worktree belongs to the project. If a terminal was removed, the session is unreachable, its identity is unknown, or a newer run exists, it refuses; inspect the situation manually. It preserves all worktree files and records a new launch. The new Cook starts a fresh conversation, not the old chat history: the Chef should first add relevant context to the task brief. The terminal state is not proof that a deliberately detached process has stopped; inspect such processes separately if present. `HC_SOCKET` overrides are refused because the Chef's session cannot be verified reliably.

## Close a Cook and remove its worktree

After the user accepts a task, its changes should be integrated into the local main branch with the necessary commit/merge authorization. Mark the task `done`, then inspect and close:

```sh
bin/ltc-cook-control check RUN_ID
bin/ltc-cook-control close RUN_ID
```

The check identifies the local main branch using `origin/HEAD` when configured, otherwise an unambiguous local `main` or `master`. It verifies the worktree is registered to the project, is clean (including untracked and ignored files), its commit is an ancestor of main, and every path changed since its base still matches main. This is deliberately conservative: cherry-picked commits or later edits to the same files may require manual inspection even if the relevant changes are present. It does not check a remote branch or automatically commit, merge or copy changes. All known Cook terminals for the worktree must be verified stopped, except the Cook being closed. The checks and terminal operations are separate commands, so avoid concurrent changes during cleanup.

If the check cannot prove the work is on main, normal close refuses without deleting anything. The Chef must explain the report and ask whether to retain or integrate the work, or permanently discard that specific worktree. Only after the user explicitly chooses permanent deletion for that worktree may the Chef use `bin/ltc-cook-control close RUN_ID --discard-changes` (with the task marked `done` or `cancelled`). This deletes uncommitted, untracked and ignored files and may discard unlanded commits. It never bypasses terminal identity or worktree ownership checks. If the target branch cannot be determined, or another terminal cannot be verified stopped, the command refuses even with this flag.

## Delivery

No commit until the user explicitly says the work is ready to commit. Push and PR creation or updates each require explicit user authorization. These are instructions to agents, not technical access controls. Chef reviews the actual worktree diff and test evidence, and leaves uncommitted work intact for the user's decision. The launcher never commits, pushes, creates a PR or merges.

Cook role text is adapted from Firstmate; see [third-party license](THIRD_PARTY_LICENSES.md).
