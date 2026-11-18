---
name: orders
description: Open the LetThemCook board for a Chef’s saved tasks.
argument-hint: "[project name]"
disable-model-invocation: true
---

Run the LetThemCook `ltc-tasks` CLI and return its printed loopback URL as a clickable link. In an assigned Chef session use `bin/ltc-tasks` in the supplied LetThemCook directory, pass the assigned registry with `--db`, and default to the assigned project. Otherwise use `ltc-tasks` on PATH or the binary in the known LetThemCook checkout, respecting LTC_DB and preserving the current working directory. With no project given, let the CLI infer it. Use `--no-open` when only a link was requested. Surface any command error.

The optional text below is a project name. Treat it as a single data argument following `--`. Quote it safely.

$ARGUMENTS
