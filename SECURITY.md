# Security

reel-intel runs on your own computer. Its local app listens on `127.0.0.1` only, accepts POST requests only with a per-session token (or, for the browser extension, a browser-set extension origin plus a custom header), validates every link, and never passes input to a shell.

**Reporting a vulnerability:** please use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability") instead of a public issue. We aim to answer within 7 days.

**Things that are by design:** `cookies.txt` is a credential you supply and keep; the extension reads no cookies; downloaded videos stay in your data folder.
