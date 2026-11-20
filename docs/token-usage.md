# Local token usage dashboard

From your LetThemCook checkout, run `bin/ltc-usage` and open the printed link on the same computer. The default is [the local token dashboard](http://127.0.0.1:30169/). The command starts a detached Python 3 listener or reuses the matching one and prints its PID. It never opens a browser. The task board and registry run independently.

Bookmark the link. After a reboot or a stop, run the same command from the checkout that contains the dashboard. The fixed loopback port preserves the URL. If another application already owns port 30169, the launcher leaves it alone. Choose a free port with `bin/ltc-usage --port NUMBER` and keep using that number. For a foreground listener, run `bin/ltc-usage --serve` and stop it with Ctrl-C. To stop a detached listener, check its printed PID with `ps -p PID -o command=`, confirm the checkout path and `ltc-usage --serve --port NUMBER`, then run `kill PID`.

## Read the comparison

The first number is a daily average of the selected metric, initially the estimated API Standard cost in USD. By default, the panel compares the last seven complete local calendar days with the preceding seven. With less shared history it automatically selects the longest pair of equal windows available, up to seven days per window. The averaging control displays the actual length. You can explicitly select 1, 7, 14 or 30 days. A missing baseline is labeled and a zero baseline has no percentage. Today is labeled on the date axis and stays outside both averages.

The chart uses one time axis. Bars show tokens on the left scale. The line shows estimated USD on the right. A missing price breaks the line, while a day with zero recorded usage inside the available history has a zero value. Hover for both numbers. Click or tap to select the day. Focus the chart and use the arrow keys, Home or End to select a day and read the same tooltip. Escape dismisses the keyboard tooltip.

**Źródło i model** and **Zakres** filter the chart and model list. Expand **Ustawienia średniej i tokenów** to change the averaging window, metric or token category. The default bars sum all token categories. Selecting a category changes the bars and the average while the line continues to show the full priced subtotal. Select **Szczegóły dnia** or expand a model name for the precise input, cache and output breakdown.

An empty day within recorded history contributes zero. Days before the first observation have unknown coverage. For a combined trend, the later start among the contributing sources sets the beginning of comparable history. Deleted logs, missing counters, unavailable sources, cloud-only accounts and other devices can all lower the recorded totals. The source footer shows detected files, read errors and skipped malformed accounting records.

## Counters and privacy

- Codex reads JSONL under `~/.codex/sessions` and `~/.codex/archived_sessions`. `CODEX_HOME` overrides the parent. A structured response is counted once by `response_id`, using the event timestamp and its turn's model. Response input includes cache reads and writes. Fresh input subtracts both. Reasoning is part of output.
- For older snapshot-only Codex history, the index uses successive cumulative counters in the same session. It ignores unchanged totals, establishes a baseline at inherited history or a discontinuity, and counts the first unambiguous local response. Explicit response records take over from snapshots when present. A snapshot delta belongs to the timestamp of the observation. With sparse older snapshots the exact timing or model of every underlying response may be unavailable.
- Claude Code reads JSONL recursively under `~/.claude/projects`, or `projects` under `CLAUDE_CONFIG_DIR`. Assistant blocks share a `message.id`. Repeated blocks and files contribute once, keeping the earliest timestamp and the largest reported counters for the response. Input, cache creation and cache reads are disjoint. Reported thinking tokens are part of output.
- The total adds fresh input, cache reads, cache writes and output. Missing model identifiers appear as **Model nieznany**. Counts measure volume.

The first visit indexes the local history. An open visible page requests new aggregate data about once a minute. After a minute, the next request checks file metadata, reads appended complete lines, and rebuilds changed or replaced files when needed. A partial last line waits for its newline. Files removed from the source trees drop out of the next aggregate. Timestamps use the system timezone and its daylight-saving transitions.

The index is private to the process and disappears when it stops. The launcher writes no index to disk. The HTTP API serves only daily per-model counters and source status. It excludes conversations, paths and response IDs. Assets and fonts are local. The dashboard makes no provider or external network requests. Any local process with access to the loopback URL can read the aggregates.

## API Standard estimate

All dates use the same published global USD rate snapshot from November 19, 2026. The [versioned rate table](../bin/ltc_usage_prices.py) lists exact model IDs and separate fresh input, read, short write, long write and output rates per million tokens. Each response is priced after deduplication and before daily aggregation. Reasoning is already included in output. These are hypothetical API Standard amounts, independent of subscription credits, actual service tier, regional processing, tool fees, or taxes. [OpenAI pricing](https://developers.openai.com/api/docs/pricing) · [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)

For all six listed GPT models, a prompt **over 272,000** tokens doubles input and cache rates and multiplies output by 1.5 for the whole request. For Haiku 5.5 a prompt **over 100,000** tokens uses its higher rates for the whole request. The thresholds include reads and writes. Opus 5.5 and Sonnet 5.5 use their Standard rates throughout. [GPT-6.2 Astra](https://developers.openai.com/api/docs/models/gpt-6.2-astra) · [GPT-6.2 Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol) · [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) · [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) · [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) · [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)

Claude's explicit 5-minute and 1-hour cache creation counters take precedence. OpenAI writes use an explicit 30-minute or 12-hour retention when present. Unspecified writes use the shorter rate, so long-lived writes can cost more. Daily details flag the affected token count. The dashboard quotes only exact model matches and identifiable requests. Unlisted models such as codex-auto-review, unknown model IDs and multi-response legacy deltas contribute to token totals only. Coverage is the share of recorded tokens included in the estimate. Partially priced periods compare their priced subtotals. Fully unpriced activity shows “Brak wyceny”. A covered empty day contributes zero.

## Rebuilding the local chart

The launcher serves the included production bundle. Node and internet access are needed only to install the locked build dependencies. To reproduce the bundle and license notices, use Node 24 and npm 11 from the checkout:

```sh
cd usage/chart
npm ci --ignore-scripts --registry=https://registry.npmjs.org --no-audit --no-fund
npm run build
```

Only the chart mounts React. The upstream EvilComposedChart supplies its Bar, Line, Grid, XAxis and YAxis components. The local adapter provides keyboard selection, a shared tooltip and the two named scales. No Tailwind build, Next.js app, remote fonts or runtime registry is required. [Evil Charts documentation](https://evilcharts.com/docs/recharts/composed-chart/static) · [Adapter](../usage/chart/dashboard.jsx) · [Build pins](../usage/chart/package-lock.json)

The local `evilcharts/registry` files are unmodified copies of `src/registry/charts/recharts-composed-chart.tsx` and `src/registry/ui/recharts-{chart,tooltip,legend,brush,dot}.tsx` at upstream revision `4c2dd7c9bae780703244265c9a4b509d40c055ad`. The `evilcharts/lib/utils.ts` file contains the upstream `cn` helper only. The build maps the upstream import alias to this local directory. Evil Charts is copyright Gurbinder, 2026, MIT. React, React DOM, Recharts and other pinned dependencies retain their individual licenses. The build includes the upstream text and package notices, including individual d3 notices. The optional esbuild platform binary uses the esbuild MIT license. [Pinned upstream tree](https://github.com/legions-developer/evilcharts/tree/4c2dd7c9bae780703244265c9a4b509d40c055ad/src/registry) · [Evil Charts license](../usage/chart/evilcharts/LICENSE) · [Dependency licenses](../usage/chart-LICENSES.txt)

The model list uses local copies of the OpenAI Blossom from OpenAI's Agents SDK documentation and the Claude symbol from Anthropic's published SVG. Both marks belong to their respective providers and identify the model provider. Unidentified models have a text label only. [OpenAI SVG source](https://github.com/openai/openai-agents-python/blob/aaec57a426b35079480d96cef473a7494c95b0ad/docs/assets/logo.svg) · [OpenAI repository license](../usage/marks/OpenAI-LICENSE) · [OpenAI brand terms](https://openai.com/brand/) · [Claude SVG source](https://platform.claude.com/docs/en/manage-claude/oauth-apps/branding-samples) · [Anthropic trademark guidelines](https://assets.claude.com/f7874b435aabdaee5f836e888b4439b9b56a934b.pdf)

The dashboard uses the locally served upright Inter Variable 4.1 throughout, including controls and chart labels. The browser selects the optical size automatically; measurements use tabular figures. The unmodified WOFF2 and SIL Open Font License 1.1 come from the official release. [Inter 4.1 download](https://rsms.me/inter/download/) · [Included license](../usage/fonts/OFL.txt)
