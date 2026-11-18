# LetThemCook (LTC)

LetThemCook is a lightweight orchestration workflow for Herdr Desktop with a per-project Codex or Claude Code Chef and selectable Cook CLIs. You talk to the **Chef**; the Chef starts a fresh **Cook** for each implementation task, supervises the work and reviews the result.

## Requirements

- Git, Python 3, Herdr Desktop and `hc`.
- Codex CLI with `--no-daemon` and `--approve-for-me` for Codex Chefs and Cooks. To use Pi or Claude Code, install and authenticate `pi` or `claude` on the local Herdr machine.
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

By default, `ltc-chef` starts a Codex tab named `👨‍🍳 Chef @ p0werusers` in each registered, open Space, passing `--approve-for-me` to Codex. Run it again after opening a Space or registering another project; it leaves an existing tab of that name or the legacy name `Chef @ p0werusers` alone. Existing tabs are not renamed or restarted, so their permission mode is unchanged. To sync one project, use `bin/ltc-chef p0werusers`. It reports missing Spaces and does not assume that a Space's displayed name or folder identifies the checkout. It supports local Spaces only. For continuous synchronization while Herdr is open, run `bin/ltc-chef --watch` in a persistent terminal; it checks every 15 seconds (configurable with `--interval`). Start the watcher again after it stops; it is not installed as a login service. An existing same-named tab prevents a duplicate even if its agent has stopped, so the watcher does not automatically restart that agent. Inspect an existing same-named tab before closing it and re-running the command. Agent startup may require interaction in Herdr (for example a trust prompt).

Select Claude at registration with `ltc-project NAME CHECKOUT SPACE_ID --chef-harness claude`, or set the next launch with `ltc-db project update NAME --chef-harness claude`. One-shot sync and `--watch` both use the saved choice. Existing Chef tabs continue running. Claude starts in the project checkout with the Chef assignment and its normal permission settings. Complete any trust or sign-in prompt in the tab. A created tab is awaiting registration until the Chef runs `ltc-watch start`. Either Chef can dispatch any supported Cook harness.

New Codex Chef tabs open the Codex fullscreen transcript with **Collapse turn details** enabled (verified on Codex CLI 0.169.0). During a turn, Codex summarizes tool activity. Once the turn finishes, it folds the activity beneath the prompt and keeps the final answer visible. Expand a summary or search the transcript to inspect command output. The launcher sets both display options for each new Chef process. [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) · [Codex tool summaries](https://github.com/openai/codex/pull/60554)

In a fullscreen Chef launched with your own Codex settings, open `/config` → **Sessions** → **Collapse turn details** to turn folding on or off immediately. In scrollback mode, choose **Fullscreen** in `/tui` for the next Codex launch. Leave a Chef supervising active Cooks running until their work is complete. [Codex terminal settings](https://learn.chatgpt.com/docs/developer-settings) · [Codex terminal commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

In a Chef launched with the new defaults, you can expand a summary at any time. The two command-line overrides take precedence over saved menu choices for that process. To use your own display preferences at the next fresh launch, run `bin/ltc-chef --use-codex-display-settings PROJECT`. The same option works with `--watch`.

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

`ltc-status` reports active tasks and their latest runs, plus any other runs that are live, unknown, or have launch problems. It reports omitted counts; use `ltc-status --all PROJECT` for complete history. It checks every recorded terminal identity and status in its recorded Herdr session, except runs with a recorded successful verified close; `--all` shows those as `closed`. Records predating this mechanism or an interrupted close remain unknown if the terminal is missing, and require inspection. It is read-only; unavailable Herdr, missing terminals, and identity mismatches are reported as unknown, not as proof that a Cook has stopped. At startup the Chef runs it once.

Commands return JSON. Project names, checkout paths and assigned Space IDs are unique; paths identify a Git checkout or an explicitly registered workspace. Task IDs are stable within the database. Statuses are `todo`, `in_progress`, `blocked`, `done`, and `cancelled`. Brief fields are `--intent`, `--context`, `--scope`, `--acceptance`, and `--checks`; each is optional and can be updated independently with `task update` (pass an empty value to clear one field). `--intent` records the user request; other fields describe the context and execution. `task show` includes the brief. The existing `--description` remains available as additional notes, and updating it replaces its prior value. Legacy tasks without brief fields continue to work. No records or project files are deleted by these commands. Use `--help` for options.

## Project task boards

From any registered checkout, run `ltc-tasks` to open that Chef’s kanban in your browser. The command always prints the URL. From the LetThemCook checkout you can also run:

```sh
bin/ltc-tasks p0werusers
bin/ltc-tasks --no-open p0werusers
bin/ltc-tasks --db /path/to/registry.sqlite3 --no-open
```

The board shows four columns: To do, In progress, Blocked and Done. Cancelled tasks count toward the project total and remain accessible by ticket URL, but have no board column. Open a ticket to read its brief and notes, or **All projects** to switch boards. The board checks for changes every ten seconds while visible. All views are read-only.

The launcher reads the registry selected by `--db`, then `LTC_DB`, then the default beside the LetThemCook installation. It matches the current Git checkout or a unique registered checkout sharing its Git worktrees. An unknown or ambiguous checkout opens the project index. An explicit project name takes priority. A missing registry produces an error.

URLs use `http://127.0.0.1:PORT/projects/ID`. The default port is stable for the resolved registry path. Subsequent calls reuse the service. If a different service occupies its port, pass the same alternate `--port NUMBER` on each call. The local service can be read by other processes on this computer. It runs detached until its printed PID is stopped or the computer restarts. Run the launcher again to restore saved links. Use `--serve` in a persistent terminal for foreground operation and stop it with Ctrl-C.

### Open a board from an agent

**Codex CLI** accepts `$tasks` (or `/skills`, then select **tasks**). **Claude Code** accepts `/orders`. Claude reserves `/tasks` for session background work. Append a registered project name to either invocation to select its board. An assigned Chef uses its launch registry and project. [Codex skill discovery](https://developers.openai.com/codex/skills/) · [Claude Code skills](https://code.claude.com/docs/en/skills) · [Claude built-in commands](https://code.claude.com/docs/en/commands)

To opt in, run the following from the LetThemCook installation you intend to use. Set the checkout path in your shell environment before starting an agent outside a Chef session. Use the matching skill link for each client you use:

```sh
LTC_ROOT="$(pwd -P)"
export PATH="$LTC_ROOT/bin:$PATH"
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills"
ln -s "$LTC_ROOT/integrations/codex/tasks" "$HOME/.agents/skills/tasks"
ln -s "$LTC_ROOT/integrations/claude/orders" "$HOME/.claude/skills/orders"
```

For one repository only, create the same links under its `.agents/skills` and `.claude/skills` directories instead. Choose unused destination names as shown and check for an existing skill before linking. Start a fresh client session to discover the skills. The shell command also works directly without skill installation.

## Configure models and prompts

Choose the profile from the work in the brief. For Codex Cooks, the following settings were checked on **2026-11-18**.

| Profile | Codex model | Effort | When to dispatch |
| --- | --- | --- | --- |
| `simple` | `gpt-6-luna` | `high` | Mechanical changes with a clear check, such as updating a repeated configuration value. OpenAI recommends High as the starting effort for Luna. |
| `routine` | `gpt-6.2-sol` | `medium` | A scoped feature or bug fix with tests. Sol handles everyday coding and uses fewer credits per token than Astra at standard speed. |
| `complex` | `gpt-6.2-astra` | `high` | A failure spanning components, a migration with uncertain dependencies, or a change that needs several hypotheses tested. |
| `default` | Harness default | Harness default | Preserve the account or CLI settings. This is the shipped profile when the command omits a profile. |

OpenAI documents Luna, Sol and Astra for Codex and announced the 6.2 rollout on November 17. The table maps their documented roles to Cook tasks. Medium for Sol and High for Astra are project choices. No Cook latency, cost or quality benchmark was run. [OpenAI model and effort guide](https://learn.chatgpt.com/docs/models) · [November 17 Codex changelog](https://learn.chatgpt.com/docs/changelog)

Locally, Codex CLI 0.169.0's model cache on November 18 listed all three IDs with the selected efforts. Its offline bundled catalog listed only Luna of the three. Access depends on account, workspace, client and rollout. Check `/model` in your own Codex session or inspect the catalog with `codex debug models` on a CLI version that provides it.

In `config.json`, `default_harness` is `codex`. A command-line `--harness` takes precedence. Each profile's flat `model` and `reasoning_effort` fields configure Codex, including in older configuration files. The optional `pi` and `claude` objects configure the other CLIs independently. A missing harness entry makes that profile unavailable for that CLI. An empty harness entry requests its startup defaults. A routine profile can use these settings for the three CLIs.

```json
{
  "model": "gpt-6.2-sol",
  "reasoning_effort": "medium",
  "pi": {"model": "openai-codex/gpt-6.1-sol", "reasoning_effort": "medium"},
  "claude": {"model": "sonnet", "reasoning_effort": "medium"}
}
```

- **Codex** receives `-m MODEL` and `-c 'model_reasoning_effort="LEVEL"'`. A null or omitted value leaves that flag out.
- **Pi** receives `--model PROVIDER/ID` and `--thinking LEVEL`. The shipped Pi mappings use exact IDs from the `openai-codex` provider catalog bundled with Pi 1.5.2. That catalog contained Luna, 6.1 Sol and 6 Astra. Configure the provider in Pi and check available IDs with `pi --list-models`. Pi can limit thinking to the selected model's capabilities. [Pi CLI reference](https://docs.pi.dev/latest/cli)
- **Claude Code** receives `--model MODEL` and, when configured, `--effort LEVEL`. The shipped simple, routine and complex profiles select `haiku`, `sonnet` and `opus`. Simple leaves effort to Claude. Alias resolution and supported efforts depend on the deployment. The flags were checked with Claude Code 2.1.327. [Claude Code model configuration](https://code.claude.com/docs/en/model-config)

LTC checks the selected executable on PATH and validates route configuration before allocating the worktree or recording a new run. Provider credentials, entitlement and the model actually served are determined by the CLI. Run metadata records the settings passed at startup.

Edit `prompts/chef.md` and `prompts/cook.md` to change their roles. A separate JSON file passed with `--config` can select other profiles or prompt paths; prompt paths are resolved relative to that config file. `ltc-chef` loads the default Chef prompt at launch; `prompts.chef` is a configuration reference and is not automatically loaded into an already-running agent. The Cook prompt is loaded from the selected config at launch. These instructions do not override higher-priority instructions or tool permissions.

## Launch a Cook

The Chef records a self-contained brief using the task fields, then launches the Cook from its Herdr terminal:

```sh
bin/ltc-cook p0werusers 12                           # Codex with account defaults
bin/ltc-cook p0werusers 13 --profile routine          # Codex, Sol at medium
bin/ltc-cook p0werusers 14 fix-parser complex         # Positional profile still works
bin/ltc-cook p0werusers 15 --harness pi --profile simple
bin/ltc-cook p0werusers 16 --harness claude --profile complex
```

Each launch via `ltc-cook` records an attempt in SQLite before creating its worktree, including the task, project, Space, worktree, base commit, hc session and terminal address when known. `run list` and `run show` return these records. Launch states describe the last recorded launch outcome: `starting`, `started`, `delivery_failed`, `failed`, or `unknown`. They are historical records, not live agent status; check Herdr before acting. `unknown` may mean a terminal was created even when the launcher did not receive a usable response, so inspect before retrying. After five minutes without a recorded terminal, status and the watcher flag a stale start for inspection. The process state is still unknown. Direct `ltc` calls record a launch only when supplied `--task-id` (and `--db` or `LTC_DB` for a non-default registry).

The wrapper reads the project path, Space and brief from the registry (`LTC_DB` selects a non-default database), checks the task belongs to the project, and passes the Chef terminal address to the launcher. The default worktree name is `task-TASK_ID` and the default profile is `default`; choose a new worktree name when retrying an existing one.

Both `ltc-cook` and the underlying `ltc` accept `--config PATH`, `--harness` and `--profile`. The launcher creates a detached Git worktree at `/path/to/project/.letthemcook/worktrees/task-name` and launches the selected CLI there in a new Herdr terminal. Names must be unique and use 1–32 lowercase letters, digits, hyphens or underscores. It labels the terminal with the space ID for visibility. Check `herdr-app ls`; if the app did not show the terminal, inspect it by the returned address and use `herdr-app do terminal.keep --terminal ADDRESS --place tab` when appropriate.

Worktrees start at `HEAD`, or a committed revision specified by `--base`. They **do not include uncommitted files or changes** from the main checkout; the output flags when those exist. Chef must account for them before delegating. The launcher adds `/.letthemcook/` to the local Git `info/exclude`, not the tracked `.gitignore`. It does not install dependencies or copy ignored files. If starting the Cook fails after worktree creation, the path is reported and preserved; inspect it before retrying. The launcher never removes worktrees; the guarded close operation below can remove them after acceptance and verification.

The launcher gives the Cook the Chef return address from `HC_TERMINAL_ID` (or `--chef ADDRESS`) so it can report with `hc send`. Check `first_message.sent` in the launch result and inspect the session if delivery failed. Use `hc get ADDRESS`, `hc wait ADDRESS --timeout 60` and `hc read ADDRESS --lines 80` to supervise a Cook. Use `bin/ltc-cook-control send RUN_ID MESSAGE` for Cook follow-ups and `bin/ltc-cook-control close RUN_ID` after user acceptance and marking the task `done`. These commands verify registry, run, task, project and Space labels against the recorded terminal in its recorded hc session; they refuse if identity cannot be established. The check and action are separate Herdr calls, not an atomic operation. Old launches without labels cannot be controlled by these commands and require manual inspection. `close` checks and removes the worktree as described below. After successful terminal removal it records a verified close; if removal or recording fails, status does not claim a verified close. A new conversation limits inherited chat context, not access to files or tools allowed by the Cook's sandbox. Cooks can read project instructions and other permitted files. Do not run simultaneous conflicting tasks without coordination.

## Supervise Cooks without continuous model use

At session startup, each Chef runs:

```sh
bin/ltc-watch start PROJECT
```

This registers the current Chef terminal and starts a project watcher in Herdr. It checks recorded Cook sessions concurrently (up to eight at once) every 15 seconds without calling a model, and sends the Chef events for a Cook report, blocker, error, finished terminal, uncertain terminal state or failed launch. It checks terminal identity before sending, retains undelivered events in SQLite, and suppresses repeated reports of an unchanged state. A direct Cook report and a subsequent agent_done event can both notify the Chef: direct `hc send` provides no registry acknowledgement the watcher can safely correlate, so suppressing the event could hide a failed or absent report. The Chef must inspect the actual result; an event never means the user accepted a task.

The watcher runs as a separate Herdr process, with one active watcher per project and database. Starting a new Chef registers its terminal as the destination. Pending events are sent in batches of up to 20. If that terminal is blocked, unavailable or fails identity checks, events stay pending and are retried. Delivery is at least once: if a send succeeds but its confirmation is lost, a batch can be sent again. Transitions that occur entirely between checks or while the watcher is stopped cannot be reconstructed. Chef startup starts or reuses the watcher, but the system does not automatically restart it if its process dies while the Chef remains running; inspect the watcher terminal if supervision appears silent. `ltc-watch run PROJECT --once` performs one observation cycle for diagnosis. Use the same `LTC_DB` and Herdr session as the Chef, without an `HC_SOCKET` override.

## Check Cook progress with whatsup

Use **`$whatsup`** in Codex CLI (or select `whatsup` in `/skills`) and **`/whatsup`** in Claude Code. Add a registered project name after the command to select it explicitly. A Chef can use its assigned project and registry. In a regular agent session the helper resolves the current Git checkout or linked worktree. If resolution fails or is ambiguous, supply the registered project name. [Codex CLI skills](https://learn.chatgpt.com/docs/developer-commands?surface=cli#use-skills-with-skills) · [Claude Code skills](https://code.claude.com/docs/en/skills)

To opt in, install the skill for your agent in the repository where you start the agent. Run the corresponding link command from that repository's root, then start a new session from a shell with the LetThemCook `bin` directory on PATH.

```sh
LTC_DIR=/absolute/path/to/LetThemCook
export PATH="$LTC_DIR/bin:$PATH"
# Codex CLI
mkdir -p .agents/skills
ln -s "$LTC_DIR/integrations/codex/whatsup" .agents/skills/whatsup
# Claude Code
mkdir -p .claude/skills
ln -s "$LTC_DIR/integrations/claude/whatsup" .claude/skills/whatsup
```

From a shell, `ltc-whatsup` prints the evidence as JSON. It defaults to the registry in the LetThemCook checkout that provides the command. Use `LTC_DB` or `--db` for a different registry, including when running the helper from a LetThemCook source worktree.

```sh
ltc-whatsup
ltc-whatsup --db /path/to/registry.sqlite3 -- p0werusers
ltc-whatsup --summarize codex --language Polish -- p0werusers
ltc-whatsup --summarize claude -- p0werusers
```

The helper uses the same project and run scoping rules as `ltc-status`. It verifies each recorded terminal in its recorded Herdr session and checks its identity again after reading the screen. Workspace installs share project resolution and include member repository identity in those checks. Each read requests up to 24 lines of history plus the visible screen. It has a five-second timeout, a 256 KiB response limit, and returns at most 45 lines and 5,000 characters of recent text. Identity mismatches, failed reads, and runs without a recorded address are visible in the snapshot. The snapshot can miss text outside the excerpt or activity after the read.

Summary mode sends bounded terminal excerpts to the selected CLI's configured provider and generates the progress text in one temporary invocation. Codex requests `gpt-6-luna` at low reasoning effort. Claude Code requests its provider's `haiku` alias. The Claude skill also requests Haiku for the invoking turn through native skill metadata. Model selection is scoped to those invocations. If the summary call fails, the command shows verified status and explains that model synthesis is unavailable. [Codex CLI options](https://learn.chatgpt.com/docs/cli/reference) · [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Claude model aliases](https://code.claude.com/docs/en/model-config)

The summary includes visible Cook reports and blockers, test evidence, and next actions. An idle agent can have a response ready even when it has not reported to the Chef. Terminal exit and Cook reports leave Chef review and user acceptance pending unless those are independently established. The command reads the registry and terminal evidence without sending messages or changing task or Cook state.

Below the run list, the command links the selected project's kanban. It uses `ltc-tasks --no-open` with the same registry and project, which starts or reuses the loopback board without opening a browser. If the board launcher or its URL is unavailable, the summary stays visible with a clear message.

## Resume a Cook

If a Cook session ends before the task is accepted, the Chef can resume the task in the existing worktree:

```sh
bin/ltc-resume RUN_ID
```

New runs record the harness, profile, model and effort in SQLite. Resume reuses those values even after a profile is edited or removed. Null settings again use the CLI defaults, and an alias such as `opus` resolves at startup. Historical Codex-only runs recorded just the profile name. Resume resolves that name against the current Codex configuration. Supply the original `--config PATH` if its mapping has changed. The selected configuration supplies the current Cook prompt.

A resumed Cook receives the complete current brief directly. Identical long sections are represented by a reference to the earlier section; other requirements are preserved verbatim. This saves space only when the brief contains exact repetition, and does not assume a fresh session remembers the earlier brief.

Run this from the Chef terminal in the recorded `HC_SESSION`, with the same `LTC_DB`. The command checks that the run is the latest for the worktree, its terminal is still present, has matching labels, and is reported `finished`. It also checks the worktree belongs to the project. If a terminal was removed, the session is unreachable, its identity is unknown, or a newer run exists, it refuses; inspect the situation manually. It preserves all worktree files and records a new launch. The new Cook starts a fresh conversation, not the old chat history: the Chef should first add relevant context to the task brief. The terminal state is not proof that a deliberately detached process has stopped; inspect such processes separately if present. `HC_SOCKET` overrides are refused because the Chef's session cannot be verified reliably.

## Close a Cook and remove its worktree

Explicit user acceptance of the task result authorizes its final commit and local integration. Cook reports, passing checks, watcher events and Chef review never constitute acceptance. The Chef coordinates the final commit of the approved changes and integrates them into the project's local main branch, using the assigned member repository for a workspace task. Inspect the worktree and target first and preserve unrelated changes, user work and unaccepted task changes. For a final commit or integration by a Cook, the Chef must relay the user's explicit acceptance and authorize the exact task and action.

If integration cannot safely proceed, the Chef reports the specific blocker and preserves the work. Never use force, reset or discard to get past a blocker. Verify the accepted changes on local main before recording the outcome and marking the task `done`. Then inspect and close:

```sh
bin/ltc-cook-control check RUN_ID
bin/ltc-cook-control close RUN_ID
```

The check identifies the local main branch using `origin/HEAD` when configured, otherwise an unambiguous local `main` or `master`. It verifies the worktree is registered to the project, is clean (including untracked and ignored files), its commit is an ancestor of main, and every path changed since its base still matches main. This is deliberately conservative: cherry-picked commits or later edits to the same files may require manual inspection even if the relevant changes are present. It does not check a remote branch or automatically commit, merge or copy changes. All known Cook terminals for the worktree must be verified stopped, except the Cook being closed. The checks and terminal operations are separate commands, so avoid concurrent changes during cleanup.

If the check cannot prove the work is on main, normal close refuses without deleting anything. The Chef must explain the report and ask whether to retain or integrate the work, or permanently discard that specific worktree. Only after the user explicitly chooses permanent deletion for that worktree may the Chef use `bin/ltc-cook-control close RUN_ID --discard-changes` (with the task marked `done` or `cancelled`). This deletes uncommitted, untracked and ignored files and may discard unlanded commits. It never bypasses terminal identity or worktree ownership checks. If the target branch cannot be determined, or another terminal cannot be verified stopped, the command refuses even with this flag.

## Delivery

Every Chef delivery includes a clickable review link. For runnable UI or web work, the Cook starts or reuses a preview they control from the changed worktree, verifies the scenario, and keeps the preview available while the task awaits explicit user acceptance. The Cook preserves user-owned servers and reports the URL, host and port, how to open the scenario, required setup, what was verified and how long the preview will remain available. The Chef checks that the preview serves the changed scenario and is reachable from the user's review environment, or explicitly attributes that verification to the Cook, then shares the link and review details.

If there is no runnable server or a usable preview is blocked, the Chef explains why and links the task ticket or project kanban as the review fallback. Run `bin/ltc-tasks --db DATABASE --no-open -- PROJECT` to obtain the actual project board URL, and append `/tasks/TASK_ID` for the ticket. Verify the link before sharing it. If the board is unavailable, report the blocker and include the task ID.

Before acceptance, leave changes uncommitted unless the user explicitly authorizes an earlier commit. Push, PR creation and PR updates each require separate explicit user authorization. The Chef reviews the actual worktree diff and test evidence before presenting the result for acceptance. The launcher never commits, pushes, creates a PR or merges.

Cook role text is adapted from Firstmate; see [third-party license](THIRD_PARTY_LICENSES.md).

## One Chef for a workspace

A workspace registers a directory outside Git and a list of direct child checkouts. Its Chef opens at the workspace root in the assigned Space. Each task belongs to one member for its entire lifetime. Use repeated `--member` flags to select repositories by directory name. Registration is atomic and rejects duplicates, symlinks, paths outside the workspace, and repositories already assigned to another project.

For example, register the Mobile Prototypes workspace, inspect the membership, then start its Chef from the LetThemCook installation.

```sh
export LTC_DB=/Users/pkmita/Dev/LetThemCook/.letthemcook/registry.sqlite3
bin/ltc-db project add mobile-prototypes /Users/pkmita/Dev/mobile-prototypes \
  --space sp27f778fdeffae2e18c5efb6b049eacfb \
  --member xtb-proto-design-system \
  --member xtb-prototype-shell \
  --member xtb-prototype-starter
bin/ltc-db project show mobile-prototypes
bin/ltc-chef mobile-prototypes
```

For a new registration, `ltc-project NAME ROOT SPACE_ID [DESCRIPTION] --member NAME ...` combines registration and launch. Once registered, use `ltc-chef NAME` to retry startup.

The Chef supplies `--member NAME` when adding each workspace task. `ltc-cook PROJECT TASK_ID` looks up that member, creates a detached worktree from its committed HEAD, and records its identity with the run. Status, resume, check and close use that same member. Registration and Chef startup leave the member checkouts untouched. Cook dispatch creates the normal local worktree and Git exclude entry in the assigned repository.

Assess changes to the design system against the shell and the starter. Record the consumer tasks and their upstream inputs when changes are needed. A Cook's worktree does not contain a sibling Cook's edits. The Chef coordinates any shared build inputs and the order of verification.
