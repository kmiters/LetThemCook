# LetThemCook

![The Boss gives an Order to the project's Chef, who coordinates Cooks to prepare a Dish.](docs/let-them-cook.png)

Give a coding task to your project's **Chef** in Herdr Desktop. The Chef keeps track of the **Order**, assigns a **Cook** to work in a separate working copy, and brings the **Dish** back for review. You’re the **Boss**. You decide when the result is ready to land.

## Place your first order

1. Open a local Space in Herdr Desktop and [connect your Git checkout](docs/setup.md#register-your-first-project). You can choose a Codex or Claude Code Chef.
2. Open the **Chef @ your-project** tab and complete any startup prompts. Tell the Chef what you want and how you'll know it works.
3. Open the review link from the Chef. Ask for revisions in the same conversation or explicitly accept the result for integration into local main.

Here are a few things you can say to your Chef.

- “Fix the settings save button. Show me how to verify the fix.”
- “What’s cooking, and is anything blocked?”
- “The preview looks good. I accept this task for local integration.”

## Find your orders and results

Ask the Chef for the project board, or run `bin/ltc-tasks your-project` from your LetThemCook checkout. Open a ticket to see the brief, saved Cook reports, and any review links. For terminal status, run `bin/ltc-status your-project` or ask the Chef for a progress update.

- [Review a new HTML artifact](docs/workflow.md#review-a-new-html-artifact), [annotate a web preview](docs/workflow.md#annotate-a-web-preview-with-lavish) or [review a saved Cook report](docs/workflow.md#annotate-a-saved-cook-report) and collect Lavish feedback in the chosen backlog.
- [Set up a project](docs/setup.md) for requirements, registration, and startup help.
- [Work with your Chef](docs/workflow.md) for boards, progress, previews, and acceptance.
- [Configure profiles and workspaces](docs/configuration.md) for agent settings and projects with multiple repositories.
- [Operate and troubleshoot runs](docs/operations.md) for dispatch, reports, monitoring, recovery, and cleanup.

The Cook role is adapted from Firstmate. [Third-party license](THIRD_PARTY_LICENSES.md)
