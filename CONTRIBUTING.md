# Contributing

Open an issue for a bug or a proposed change, or submit a pull request.

Before submitting:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile status.py
omarchy plugin validate .
```

The tests use the Python standard library. The manifest check in the test suite
runs without Omarchy; loading QML and visually checking the popup require an
Omarchy session.

Keep the top bar icon-only, preserve the read-only scope, and distinguish
rclone's bounded queue from a full remaining-file count. Monitoring must never
start transfers, alter configuration, or request cloud credentials. Use fake
cloud names, file paths, and log entries in fixtures and screenshots.

For reports, include the plugin, Omarchy, Python, and rclone versions, the job
type, whether it runs as a user systemd service, and whether local RC is enabled.
Redact usernames, remote names, file paths, URLs, credentials, and tokens from
diagnostics. Do not attach an rclone config or full desktop screenshot.
