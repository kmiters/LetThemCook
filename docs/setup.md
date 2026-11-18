# Set up your first project

[LetThemCook home](../README.md) · [Everyday workflow](workflow.md)

## What you need

- Herdr Desktop running with a local Space, plus `herdr-app` and `hc` on your PATH. LTC launches Chefs in local Spaces. Its scripts use Bash and Python 3 on a Unix-like host. The local checks used macOS with Python 3.9.6 and `hc` 0.1.21. [Herdr Desktop](https://github.com/herdrdev/herdr-app/) · [Herdr Core](https://herdr.dev/core/)
- Git and a checkout with a committed `HEAD`. The Cook launcher needs permission to create a worktree in that checkout and update its local Git exclude file.
- A CLI with access to its provider. Codex is the default for both roles and needs a build with `--no-daemon` and `--approve-for-me` (verified with 0.169.0). You can choose Claude Code for either role or Pi for a Cook. If you use Claude as Chef and want to work without Codex, ask it to dispatch Cooks with the Claude harness or use a Cook config that defaults to Claude. Check that the selected executable is available in the relevant Herdr environment. [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference) · [Claude Code quickstart](https://code.claude.com/docs/en/quickstart) · [Pi quickstart](https://docs.pi.dev/latest/quickstart)

LTC passes `--approve-for-me` to Codex Chefs and Cooks. Claude Code and Pi start with their applicable permissions. Worktrees isolate Git changes. Access to files and tools is governed by the harness and host.

## Register your first project

The LetThemCook repository is private. Use a GitHub account with repository access and authenticate Git for the HTTPS clone below. If you already have a checkout, start from its root. [GitHub cloning guide](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)

In Herdr Desktop, create a local Space or select an existing one for the project. Leave it open while you look up its ID.

```sh
git clone https://github.com/kmiters/LetThemCook.git
cd LetThemCook
herdr-app ls
```

Copy the Space ID from the output and replace the example checkout path and `SPACE_ID`. Use a short name such as `my-app` for later board and status commands.

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
