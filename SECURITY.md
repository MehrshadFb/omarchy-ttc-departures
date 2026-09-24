# Security

This plugin runs inside the Omarchy shell as unsandboxed code, like every Omarchy plugin. These are the boundaries it keeps.

- **Processes.** The shell starts `python3 ttc.py` with an argument list. User input (search text, stop numbers) is passed as arguments, never interpolated into a shell string. The only shell command is the right-click notification, whose text is escaped before it reaches `omarchy-notification-send`.
- **Network.** Only the hosts listed in the README are contacted, always over HTTPS, with a 10 second timeout (20 for trip planning) and an 8 MB response cap. No request carries anything from your machine beyond the stop coordinates needed for a trip plan.
- **Parsing.** Feed bodies are decoded by a bounded protobuf reader that skips unknown fields and raises on truncation. JSON from the helper is size-capped and parsed inside try/catch in QML. Every text shown in the panel uses `Text.PlainText`.
- **Files.** The helper writes only under `~/.local/state/omarchy/ttc-departures/`, created with mode 0700, using temporary files and atomic renames. Settings go through the shell's own `updateEntryInline`, which is limited to this plugin's entry.
- **Dependencies.** None beyond Python 3, which Omarchy ships. `data/stops.json` and `data/trips.json` are generated from public GTFS by a script in this repository.

To report a problem, open a private security advisory on the GitHub repository.
