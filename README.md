# LetThemCook (LTC)

LetThemCook is a lightweight orchestration workflow for Herdr Desktop and Codex. You talk to the **Chef**; the Chef starts a fresh **Cook** for each implementation task, supervises the work and reviews the result.

## Requirements

- Git, Python 3, Herdr Desktop and `hc`.
- Codex CLI with support for `--no-daemon`.
- Permission for Git to create worktrees and for `hc` to access Herdr's core. The tools do not bypass sandbox or approval settings.

## Project Chefs and the registry

Every registered project can have one Chef in its own Herdr Space. The Chef works on that project only, starts Cooks for individual implementation tasks, reviews their reports and closes their terminals after the user accepts the completed task. Worktrees and changes remain available after a Cook is closed.

The registry is a SQLite database created on first use at `.letthemcook/registry.sqlite3`, ignored by Git. Python's standard library supplies SQLite. Back up that file to preserve the registry. Set `LTC_DB` or pass `--db /path/to/registry.sqlite3` before the subcommand to use another database.

Find Space IDs with `herdr-app ls`, then link each project explicitly:

```sh
bin/ltc-project p0werusers /path/to/p0werusers SPACE_ID
# For an existing project:
bin/ltc-db project update p0werusers --space SPACE_ID
bin/ltc-chef
```

`ltc-chef` starts a Codex tab named `Chef @ p0werusers` in each registered, open Space. Run it again after opening a Space or registering another project; it leaves an existing tab of that name alone. To sync one project, use `bin/ltc-chef p0werusers`. It reports missing Spaces and does not assume that a Space's displayed name or folder identifies the checkout. It supports local Spaces only. For continuous synchronization while Herdr is open, run `bin/ltc-chef --watch` in a persistent terminal; it checks every 15 seconds (configurable with `--interval`). Start the watcher again after it stops; it is not installed as a login service. An existing same-named tab prevents a duplicate even if its agent has stopped, so the watcher does not automatically restart that agent. Inspect an existing same-named tab before closing it and re-running the command. Agent startup may require interaction in Herdr (for example a trust prompt).

```sh
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

Commands return JSON. Project names, checkout paths and assigned Space IDs are unique; paths must point to an existing Git checkout. Task IDs are stable within the database. Statuses are `todo`, `in_progress`, `blocked`, `done`, and `cancelled`. Updating a description replaces it, so include earlier context you wish to retain. No records or project files are deleted by these commands. Use `--help` for options.

## Configure models and prompts

Edit `config.json`: each named profile has a description, `model`, and `reasoning_effort`. Chef selects a profile based on the task and explains the choice. `null` means use the Codex default; the supplied profiles deliberately do not assume which models your account can access. `reasoning_effort` can be `low`, `medium`, `high`, `xhigh`, or `null`. Specify actual model IDs supported by your account, for example:

```json
"simple": {"description": "Small and well-scoped tasks", "model": "YOUR_MODEL_ID", "reasoning_effort": "low"}
```

Edit `prompts/chef.md` and `prompts/cook.md` to change their roles. A separate JSON file passed with `--config` can select other profiles or prompt paths; prompt paths are resolved relative to that config file. `ltc-chef` loads the default Chef prompt at launch; `prompts.chef` is a configuration reference and is not automatically loaded into an already-running agent. The Cook prompt is loaded from the selected config at launch. These instructions do not override higher-priority instructions or tool permissions.

## Launch a Cook

The Chef records a self-contained brief and acceptance criteria in the task description, then launches the Cook from its Herdr terminal:

```sh
/path/to/LetThemCook/bin/ltc-cook p0werusers TASK_ID [WORKTREE_NAME] [PROFILE]
```

The Bash wrapper reads the project path, Space and brief from the registry (`LTC_DB` selects a non-default database), checks the task belongs to the project, and passes the Chef terminal address to the launcher. The default worktree name is `task-TASK_ID` and the default profile is `default`; choose a new worktree name when retrying an existing one.

The launcher creates a detached Git worktree at `/path/to/project/.letthemcook/worktrees/task-name` and launches Codex there in a new Herdr terminal. Names must be unique and use 1–32 lowercase letters, digits, hyphens or underscores. It labels the terminal with the space ID for visibility. Check `herdr-app ls`; if the app did not show the terminal, inspect it by the returned address and use `herdr-app do terminal.keep --terminal ADDRESS --place tab` when appropriate.

Worktrees start at `HEAD`, or a committed revision specified by `--base`. They **do not include uncommitted files or changes** from the main checkout; the output flags when those exist. Chef must account for them before delegating. The launcher adds `/.letthemcook/` to the local Git `info/exclude`, not the tracked `.gitignore`. It does not install dependencies or copy ignored files. If starting the Cook fails after worktree creation, the path is reported and preserved; inspect it before retrying. Worktrees and changes are never automatically removed.

The launcher gives the Cook the Chef return address from `HC_TERMINAL_ID` (or `--chef ADDRESS`) so it can report with `hc send`. Check `first_message.sent` in the launch result and inspect the session if delivery failed. Use `hc get ADDRESS`, `hc wait ADDRESS --timeout 60` and `hc read ADDRESS --lines 80` to supervise a Cook. A new conversation limits inherited chat context, not access to files or tools allowed by the Cook's sandbox. Cooks can read project instructions and other permitted files. Do not run simultaneous conflicting tasks without coordination.

## Delivery

No commit until the user explicitly says the work is ready to commit. Push and PR creation or updates each require explicit user authorization. These are instructions to agents, not technical access controls. Chef reviews the actual worktree diff and test evidence, and leaves uncommitted work intact for the user's decision. The launcher never commits, pushes, creates a PR or merges.

Cook role text is adapted from Firstmate; see [third-party license](THIRD_PARTY_LICENSES.md).
