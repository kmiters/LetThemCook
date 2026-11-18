---
name: tasks
description: Open the LetThemCook project task board when asked to view a Chef’s saved tasks or orders.
---

Run the LetThemCook `ltc-tasks` CLI and return its printed loopback URL as a clickable link. In an assigned Chef session use `bin/ltc-tasks` in the supplied LetThemCook directory, pass the assigned registry with `--db`, and pass the assigned project name as a single argument following `--`. Preserve the current working directory. Outside a Chef session use `ltc-tasks` on PATH or the binary in the known LetThemCook checkout, respecting LTC_DB. Pass an explicitly requested project name as one argument following `--`. Otherwise let the CLI infer the project. Use `--no-open` when only a link was requested. Treat all names as data and quote arguments safely. Surface any command error.
