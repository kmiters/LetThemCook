# Configure LetThemCook

[LetThemCook home](../README.md) · [First project setup](setup.md) · [Run operations](operations.md)

## Chef launch settings

A project stores its choice of `codex` or `claude` for the Chef. The choice applies when creating a new tab. Either Chef can dispatch any supported Cook harness.

```sh
bin/ltc-db project update my-app --chef-harness claude
bin/ltc-chef my-app
```

Run `bin/ltc-chef` to sync all registered projects, or `bin/ltc-chef my-app` for one. Existing Chef tabs are reused by name, including the legacy name. Harness changes apply only when a fresh tab is created. If a stopped tab remains, inspect it before closing it and retrying.

For ongoing Space synchronization, run `bin/ltc-chef --watch` in a persistent terminal. It runs the same sync every 15 seconds. Use `--interval SECONDS` to change the cadence. Restart it if it exits. This loop discovers open Spaces and checks for Chef tabs. Use the [project watcher](operations.md#cook-watcher) for Cook supervision.

By default, new Codex Chefs use `tui.fullscreen_transcript=true` and `tui.collapse_turn_details=true`. Expand a turn summary to inspect its tool output. Run `bin/ltc-chef --use-codex-display-settings my-app` to use your own display settings when a fresh tab is launched. This switch also works with `--watch`.

New Claude Code Chefs use `tui=fullscreen` and `viewMode=focus` for a quieter transcript. The launch applies these settings to that Chef only. Run `bin/ltc-chef --use-claude-display-settings my-app` to keep Claude Code's native display choices. This switch also works with `--watch`. Claude Code 2.1.327 and the current settings reference support these values. Focus view summarizes tool calls. Use `--verbose` or the normal display opt-out when you need expanded detail. [Claude Code settings reference](https://code.claude.com/docs/en/settings-reference#viewmode)

New Codex and Claude Code Chef sessions receive per-launch native hooks. `SessionStart` runs for `startup` and `resume`. `PostToolUse` runs asynchronously after Bash tools and adds a bounded reminder only when the assigned registry records a new task transition to `done`. It reads task IDs and the registry event marker. It does not read raw transcripts or alter instructions. See [the lifecycle hook evidence and safety rules](operations.md#proactive-agent-instruction-maintenance).

Codex receives hook definitions through per-launch `-c` overrides. Codex merges hook entries from active hook sources. It does not change `CODEX_HOME`, project settings, or user settings. On first use, inspect the definitions in `/hooks` and trust them there if you want them active. Never bypass hook trust. Codex hooks were verified with version 0.169.0. If hooks are disabled by an existing feature setting, keep that choice or review the setting deliberately. [Codex hooks reference](https://learn.chatgpt.com/docs/hooks)

Claude Code receives hook definitions and display defaults through per-launch `--settings` JSON. Settings hook entries merge with existing hook sources. No settings file is written. Claude Code still applies workspace trust before running hooks. Review and trust the launch hook when prompted. Hook and display configuration was checked with version 2.1.327. [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)

## Cook profiles and agent CLIs

The shipped [config.json](../config.json) chooses `codex` and the `default` profile when you omit both flags. `--harness` and `--profile` override the corresponding defaults. `default` leaves the model and effort to the selected CLI. The other shipped mappings are below.

| Profile | Intended work | Codex | Pi | Claude Code |
| --- | --- | --- | --- | --- |
| `simple` | Bounded edit with a clear check | `gpt-6-luna`, high | `openai-codex/gpt-6-luna`, high | `haiku` |
| `routine` | Ordinary feature or localized fix | `gpt-6.2-sol`, medium | `openai-codex/gpt-6.1-sol`, medium | `sonnet` |
| `complex` | Cross-component work or diagnosis | `gpt-6.2-astra`, high | `openai-codex/gpt-6-astra`, high | `opus` |

See the Claude profile entries for current effort settings. These mappings are startup requests. Availability depends on account, provider, deployment and CLI version. Check the model picker in Codex or `pi --list-models` in Pi. Alias resolution and supported efforts in Claude Code depend on the deployment. [Shipped config](../config.json) · [Codex models](https://learn.chatgpt.com/docs/models) · [Pi CLI reference](https://docs.pi.dev/latest/cli) · [Claude Code model configuration](https://code.claude.com/docs/en/model-config)

A profile uses flat `model` and `reasoning_effort` fields for Codex, with separate `pi` and `claude` objects for those CLIs. An empty object uses that CLI's defaults. A profile without an object for the selected Pi or Claude harness is unavailable for that harness. Null or omitted model and effort values omit the corresponding flags.

- Codex uses `-m MODEL` and `-c 'model_reasoning_effort="LEVEL"'`.
- Pi requires a `PROVIDER/MODEL` ID for explicit selection and uses `--model` and `--thinking`. Pi can limit thinking to the model's capabilities. [Pi CLI reference](https://docs.pi.dev/latest/cli)
- Claude Code uses `--model` and `--effort`. These flags were checked with Claude Code 2.1.327. [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)

LTC checks that the Cook executable is on PATH and the configuration is valid before allocating a run or worktree. The run records the requested settings. The CLI handles credentials, entitlements and model selection with its provider.

## Prompts and alternate configs

New Chef tabs load [prompts/chef.md](../prompts/chef.md) from the LetThemCook installation. For Cooks, `ltc-cook --config PATH` and `ltc --config PATH` select a JSON file. Its `prompts.cook` path is relative to that file. `ltc-resume --config PATH` likewise selects the current Cook prompt and supplies the profile mapping for historical runs. Changes take effect at launch. Agent and tool permissions still apply.

The optional `--role` flag selects instructions independently of the profile and harness. Use `designer` when success depends on the user's path, layout or interactions. Use `developer` for implementation, diagnosis or tests. Use `generic` (the default) for other work. Generic uses just the configured Cook base. A specialist receives that base and its own short role file before the assigned task brief.

Existing configs can use either shipped role file without changes. To replace one, set `prompts.roles.designer` or `prompts.roles.developer` to a path relative to the config. Supply the same `--config PATH` on resume to reuse custom instructions.

## Optional agent shortcuts

With the supplied skills installed, Codex accepts `$tasks` and `$whatsup`, also available in its `/skills` picker. Claude Code accepts `/orders` and `/whatsup`. Claude reserves `/tasks` for its own session work. The supplied command helpers accept an optional registered project name. [Codex skill discovery](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) · [Claude Code skills](https://code.claude.com/docs/en/skills) · [Claude Code commands](https://code.claude.com/docs/en/commands)

To enable them in one repository, run the commands for your client from that repository's root. Substitute your absolute LetThemCook path and check that each skill destination is unused. Start a fresh session with the same PATH. Assigned Chefs use their launch directory for LetThemCook tools and their assigned registry and project.

```sh
LTC_DIR=/absolute/path/to/LetThemCook
export PATH="$LTC_DIR/bin:$PATH"

# Codex
mkdir -p .agents/skills
ln -s "$LTC_DIR/integrations/codex/tasks" .agents/skills/tasks
ln -s "$LTC_DIR/integrations/codex/whatsup" .agents/skills/whatsup

# Claude Code
mkdir -p .claude/skills
ln -s "$LTC_DIR/integrations/claude/orders" .claude/skills/orders
ln -s "$LTC_DIR/integrations/claude/whatsup" .claude/skills/whatsup
```

For user-wide access, use the matching directories under `$HOME`. The [shell commands](workflow.md#open-the-task-board) work without installing these skills.

## One Chef for a workspace

Register a workspace root outside Git and select its direct child Git checkouts with repeated `--member` flags. Each task belongs to one member for its lifetime. Registration is atomic and rejects duplicate membership, symlink members, paths outside the workspace, and repositories already assigned to another project.

```sh
bin/ltc-project my-workspace /absolute/path/to/workspace SPACE_ID \
  --member design-system --member app-shell
bin/ltc-db project show my-workspace
bin/ltc-db task add my-workspace 'Update the shared button' --member design-system
```

The Chef starts at the workspace root. Dispatch uses the normal `ltc-cook PROJECT TASK_ID` command and creates the worktree in the task's member repository. Run identity checks, status, resume, and close use the same member. The Chef integrates accepted changes into that member's local main.

For changes spanning repositories, trace the affected consumers, record a task per affected member, and specify upstream inputs and verification order. Sibling Cook worktrees have separate committed bases. Coordinate a reproducible shared input for cross-repository checks.

Read the workspace guide, member instructions and dependency manifests. For mobile-prototypes, trace the design system, then shell, then starter, including starter's direct design-system imports. Record related task IDs, checks and the committed inputs each Cook needs.
