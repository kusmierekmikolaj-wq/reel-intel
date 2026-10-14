# Using reel-intel from an automation (n8n and similar)

reel-intel runs on one computer and is driven by the command line, so any tool that can run a command can call it. **This page describes the idea; it has not been tested inside n8n.** What was tested is the command itself.

## The steps

1. A list of reel links (or a `grid.tsv` made by `reel-intel snippet`) is written to a file.
2. One command does everything (download, speech to text, reading the screen, quality check, pages):

   ```
   reel-intel --data C:\path\to\folder run grid.tsv --lang en --limit 30
   ```

   About 25-35 minutes for 30 reels on a mid-range GPU (speech plus reading the screen).
3. The result for an AI agent is **one file**, `FOR_AI.md` (`DLA_AI.md` with `--lang pl`): the names to check (repositories, skills, commands) first, then every video with what is said, what is written on the screen and the post caption.
4. Hand that file to an AI step with a question such as "which of these can we use in practice, and how?".

## In n8n

- **Execute Command** node: the command from step 2 (the node needs a self-hosted n8n on the same computer).
- **Read/Write Files from Disk** node: read `FOR_AI.md`.
- An AI node (or an HTTP call to a model) with the file as context.
- A **Schedule** or **Webhook** trigger starts it; for a profile that is watched regularly see `docs/MONITORING.md`.

Limits worth knowing: the platforms may rate-limit or block downloads, and a login-gated video needs a `cookies.txt` (see `docs/LEGAL.md` for what is and is not appropriate).
