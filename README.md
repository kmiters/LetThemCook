# LetThemCook (LTC)

LetThemCook is a lightweight orchestration workflow for Herdr Desktop and Codex. You talk to the **Chef**; the Chef starts a fresh **Cook** for each implementation task, supervises the work and reviews the result.

## Requirements

- Git, Python 3, Herdr Desktop and `hc`.
- Codex CLI with support for `--no-daemon`.
- Permission for Git to create worktrees and for `hc` to access Herdr's core. The tools do not bypass sandbox or approval settings.

## Run the Chef and manage work

Start your main agent in this repository. `AGENTS.md` directs it to the Chef instructions and the local registry. The Chef keeps track of projects without moving their checkouts; it reads each project's own instructions before working there. For agents that do not load `AGENTS.md` automatically, ask them to read it when starting a session.

The registry is a SQLite database created on first use at `.letthemcook/registry.sqlite3`, ignored by Git. Python's standard library supplies SQLite; no server or dependency is needed. Back up that file to preserve the registry. Set `LTC_DB` or pass `--db /path/to/registry.sqlite3` before the subcommand to use another database.

```sh
bin/ltc-db project add p0werusers /path/to/p0werusers --description 'Project description'
bin/ltc-db project list
bin/ltc-db project show p0werusers
bin/ltc-db project update p0werusers --path /new/path/to/p0werusers
bin/ltc-db task add p0werusers 'Implement feature' --description 'Requirements and acceptance criteria'
bin/ltc-db task list --project p0werusers
bin/ltc-db task list --status todo
bin/ltc-db task show 1
bin/ltc-db task update 1 --status in_progress
bin/ltc-db task update 1 --status done --description 'Outcome and validation'
```

Commands return JSON. Project names and checkout paths are unique; paths must point to an existing Git checkout. Task IDs are stable within the database. Statuses are `todo`, `in_progress`, `blocked`, `done`, and `cancelled`. Updating a description replaces it, so include any earlier context you wish to retain. No records or project files are deleted by these commands. Use `--help` on any command for options.

## Configure models and prompts

Edit `config.json`: each named profile has a description, `model`, and `reasoning_effort`. Chef selects a profile based on the task and explains the choice. `null` means use the Codex default; the supplied profiles deliberately do not assume which models your account can access. `reasoning_effort` can be `low`, `medium`, `high`, `xhigh`, or `null`. Specify actual model IDs supported by your account, for example:

```json
"simple": {"description": "Small and well-scoped tasks", "model": "YOUR_MODEL_ID", "reasoning_effort": "low"}
```

Edit `prompts/chef.md` and `prompts/cook.md` to change their roles. A separate JSON file passed with `--config` can select other profiles or prompt paths; prompt paths are resolved relative to that config file. The repository `AGENTS.md` points the Chef to its prompt; `prompts.chef` is a configuration reference and is not automatically loaded into an already-running agent. The Cook prompt is loaded from the selected config at launch. These instructions do not override higher-priority instructions or tool permissions.

## Launch a Cook

Chef finds the project's space ID with `herdr-app ls`, then runs:

```sh
/path/to/LetThemCook/bin/ltc --project /path/to/project --space SPACE_ID --name task-name --profile simple --task 'Self-contained task, scope, acceptance criteria and checks'
```

The command creates a detached Git worktree at `/path/to/project/.letthemcook/worktrees/task-name` and launches Codex there in a new Herdr terminal. Names must be unique and use 1–32 lowercase letters, digits, hyphens or underscores. It labels the terminal with the space ID for visibility. Check `herdr-app ls`; if the app did not show the terminal, inspect it by the returned address and use `herdr-app do terminal.keep --terminal ADDRESS --place tab` when appropriate.

Worktrees start at `HEAD`, or a committed revision specified by `--base`. They **do not include uncommitted files or changes** from the main checkout; the output flags when those exist. Chef must account for them before delegating. The launcher adds `/.letthemcook/` to the local Git `info/exclude`, not the tracked `.gitignore`. It does not install dependencies or copy ignored files. If starting the Cook fails after worktree creation, the path is reported and preserved; inspect it before retrying. Worktrees and changes are never automatically removed.

Use `hc get ADDRESS`, `hc wait ADDRESS --timeout 60` and `hc read ADDRESS --lines 80` to supervise a Cook. A new conversation limits inherited chat context, not access to files or tools allowed by the Cook's sandbox. Cooks can read project instructions and other permitted files. Do not run simultaneous conflicting tasks without coordination.

## Delivery

No commit until the user explicitly says the work is ready to commit. Push and PR creation or updates each require explicit user authorization. These are instructions to agents, not technical access controls. Chef reviews the actual worktree diff and test evidence, and leaves uncommitted work intact for the user's decision. The launcher never commits, pushes, creates a PR or merges.

Cook role text is adapted from Firstmate; see [third-party license](THIRD_PARTY_LICENSES.md).
