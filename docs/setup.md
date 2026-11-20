# Set up your first project

[LetThemCook home](../README.md) · [Everyday workflow](workflow.md)

## What you need

- Herdr Desktop running with a local Space, plus `herdr-app` and `hc` on your PATH. LTC launches Chefs in local Spaces. Its scripts use Bash and Python 3 on a Unix-like host. The local checks used macOS with Python 3.9.6 and `hc` 0.1.21. [Herdr Desktop](https://github.com/herdrdev/herdr-app/) · [Herdr Core](https://herdr.dev/core/)
- Git and a checkout with a committed `HEAD`. The Cook launcher needs permission to create a worktree in that checkout and update its local Git exclude file.
- A CLI with access to its provider. Codex is the default for both roles and needs a build with `--no-daemon` and `--approve-for-me` (verified with 0.169.0). You can choose Claude Code for either role or Pi for a Cook. If you use Claude as Chef and want to work without Codex, ask it to dispatch Cooks with the Claude harness or use a Cook config that defaults to Claude. Check that the selected executable is available in the relevant Herdr environment. [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference) · [Claude Code quickstart](https://code.claude.com/docs/en/quickstart) · [Pi quickstart](https://docs.pi.dev/latest/quickstart)

LTC passes `--approve-for-me` to Codex Chefs and Cooks. Claude Code and Pi start with their applicable permissions. Worktrees isolate Git changes. Access to files and tools is governed by the harness and host.

## Register your first project

The LetThemCook repository is private. Use a GitHub account with repository access and authenticate Git for the HTTPS clone below. If you already have a checkout, start from its root. [GitHub cloning guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)

Open Herdr Desktop and start your usual agent from the LetThemCook checkout with one short message.

```sh
git clone https://github.com/kmiters/LetThemCook.git
cd LetThemCook
codex "Set up LetThemCook"
```

For Claude Code, run `claude "Set up LetThemCook"` after changing folders. Complete any sign-in in the CLI and review the checkout path before trusting the folder. If the CLI opens an empty chat after sign-in, say “Set up LetThemCook”. The same message works in an existing conversation in this checkout. Starting either CLI without a message opens its usual conversation.

Your agent opens **Chef @ Let Them Cook** in Herdr. Open that tab and complete any native sign-in or folder trust prompt yourself. Once connected, the Chef asks “What would you like to cook up?” and “Which folder is your project in?” It asks follow-up questions in English too. You can answer in your own language. Give it your goal and the path to an existing Git checkout with a first commit. It finds or opens a local Space and tells you which project Chef tab to open. Your goal travels with the handoff. The project Chef asks whether to begin.

If setup stops partway through, return to the conversation that opened the tab and ask it to retry. Existing registrations and tabs can be reused, including a tab named **Chef @ LetThemCook** from an earlier installation. A tab that's waiting for trust or login becomes ready only after its Chef registers in Herdr.

### For the agent running setup

From this checkout, run `bin/ltc-onboard root --harness codex` or `bin/ltc-onboard root --harness claude`, using the current CLI unless the user chose another. Pass the conversation language with `--language` (for example, `--language Polish`) for the root Chef’s greeting. Use this installation’s default registry or the configured `LTC_DB`. The helper checks the checkout, first commit, local Herdr window and CLI, and registers this installation. Python 3, Git, Bash, Herdr Desktop and its `hc` CLI must be available. If a dependency is missing, explain the specific next step and resume after it is available. Keep sign-in, trust and global settings under the user's control.

Use the returned Space and tab names in the user's language. On `awaiting_registration`, direct them to the tab for startup and any native prompts. On `bound`, relay the handoff status. For ambiguous Spaces, offer their friendly names and folders, then use the selected ID internally with `--space`. The separate root Chef collects the goal and project folder after it binds. Leave watcher binding to that Chef. Setup authorizes local registration and launch only. Git initialization, commits, global configuration changes and Cook dispatch each need their own user request.

### Manual registration

If you already manage Spaces yourself, open the project’s local Space in Herdr Desktop and run `herdr-app ls`. Copy its Space ID and replace the example checkout path and `SPACE_ID`. Use a short name such as `my-app` for later board and status commands.

```sh
bin/ltc-project my-app /absolute/path/to/my-app SPACE_ID
```

To start with Claude Code, use this registration command instead.

```sh
bin/ltc-project my-app /absolute/path/to/my-app SPACE_ID --chef-harness claude
```

Open **Chef @ my-app** in the assigned Space. Complete any trust or sign-in prompt in the tab. A newly created tab is awaiting Chef registration. The Chef registers itself by running `ltc-watch start` and checks the project status. Once it is ready, give it your first order.

## If the Chef tab does not start

Registration can succeed even if opening the tab fails. Check whether the project was saved, then retry the launch after resolving the reported error. An assigned Space must be open and local, and its checkout path must still exist.

```sh
bin/ltc-db project show my-app
bin/ltc-chef my-app
```

If you need to point an existing project at the right Space, update its stored ID and sync again.

```sh
bin/ltc-db project update my-app --space SPACE_ID
bin/ltc-chef my-app
```

The launcher recognizes the current and legacy Chef tab names. A same-named tab counts as present even if the agent inside has stopped. Inspect that tab before closing it and retrying, especially if it was supervising Cooks. See [Chef launch settings](configuration.md#chef-launch-settings) for harness changes and Space synchronization.

## Where your orders are stored

The default registry is `.letthemcook/registry.sqlite3` under the LetThemCook checkout. It is created on first use by the registry CLI and ignored by Git. Keep a SQLite backup if you need to preserve project registrations, tasks, runs, and reports. Set `LTC_DB` for a different registry and keep using that same path across commands and sessions. [Registry commands](operations.md#registry-and-run-history)

## Daily LetThemCook update check

Chef lifecycle hooks in Codex and Claude Code check the GitHub `main` ref for this LetThemCook installation. The check supports a single HTTPS `origin` for `github.com/kmiters/LetThemCook` (with or without `.git`) and uses your existing Git credentials without prompting. It reads the remote commit ID without fetching objects, updating Git refs, changing the checkout, or deploying anything.

The automatic check is eligible on a trusted Chef hook at session startup or resume, or after a Bash tool call in an already registered Chef session, when the hook runs in the Chef's registered checkout. All Chefs launched from the same LetThemCook checkout share `.letthemcook/update-check.sqlite3`, including Chefs using different registries. The check records the attempt before contacting GitHub. The next network attempt is eligible 24 hours after that UTC timestamp, including after a failed attempt. The first eligible hook performs it. If the check is interrupted after recording an attempt, that attempt still counts toward the limit. Without a trusted hook, a running Chef, or a matching event, no automatic check takes place.

From the LetThemCook checkout, inspect the state or explicitly request a check under the same limit.

```sh
bin/ltc-update
bin/ltc-update --check
```

The status includes the last attempt and last success in UTC, when another attempt becomes eligible, and the local and remote commit IDs from the last successful check. The recorded relation is `up_to_date`, `ahead`, `behind`, `diverged`, or `unknown`. Missing local history can make the relation uncertain even when the remote commit ID is known. Network or credential failures appear as `request_failed` or `timeout`. The `origin` field shows whether the current configuration is supported.

An automatic check can add one short note to the Chef's hook context when it first observes a differing remote tip or observes a change from the last successful check. Equal tips, local `main` already containing the remote tip, routine checks, and failures stay quiet. An uncertain relation is reported as such.
