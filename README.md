# Railway Companion

A personal Bangladesh Railway monitor with a web dashboard and a Windows desktop booking agent.

**At PC:** the desktop agent selects a complete matching group and advances toward payment, then beeps for you to finish. If the website requires verification or an uncertain step occurs, it stops and asks you to inspect the browser.

**Away:** the service watches your routes and sends optional Telegram availability alerts. Buy independently in Rail Sheba. Away mode never starts a reservation.

The application is implemented and locally tested. Live railway sessions, Telegram delivery with your bot, and real checkout compatibility must be verified with your own account. See [START_HERE.md](START_HERE.md) for the complete startup and troubleshooting guide. No purchase or live reservation was made during development. A payment-ready screen is not an issued ticket.

## Start on this Windows PC

1. Double-click **Launch Companion.cmd**. The portable runtime is already installed in this workspace. On another machine, the launcher runs `Setup.ps1` when needed.
2. Create a dashboard password (any non-empty password).
3. In **Settings**, click **Connect Railway** to open your regular Brave or Chrome profile without browser automation.
4. Enable the Companion browser extension once, following the app's **Browser extension setup** instructions. Use **Developer mode ? Load unpacked** and select `data/browser-extension` in this project.
5. Refresh the Railway tab and sign in normally. The extension syncs your session automatically, encrypted at rest. Check the actual monitor status after adding a watchlist. Website verification remains manual.
6. Normal-browser connection supports monitoring and Telegram alerts. Optional automated checkout uses the separate browser under **Advanced desktop booking options**; it may be rejected by website verification. Remote desktop pairing remains available there.
7. Create watchlists using the searchable From and To station dropdowns, selected dates/classes and your passenger count. Stations are added as numbered, removable chips in selection order. Fare is never a matching condition.
8. To prepare checkout, save passenger names/contact details, choose a watchlist's automatic action, and explicitly enable **At PC**. You remain responsible for payment and website-required verification.
9. To monitor while away, select **Away** and optionally configure Telegram. Open Rail Sheba yourself when a suitable alert arrives.

The dashboard is at **http://127.0.0.1:8787**. Chrome opens it in a dedicated app window; otherwise the default browser opens it. The desktop agent has a Windows tray icon with dashboard, browser, session-sync and Away controls.

Closing the dashboard does not stop monitoring. `Start-Companion.ps1` prevents Windows system sleep while the service runs; the screen may still turn off. Use `-AllowSleep` to opt out. Shutdown, hibernation, loss of internet, and some lid-close policies still interrupt monitoring. This PC must remain on when it hosts the service.

Run `Stop-Companion.ps1` to stop the service and agent. The official booking browser is left available for outstanding checkout. Restart always returns to Away mode and preserves unresolved attempts for review.

## Included

- Multiple departure/arrival stations, dates and classes per watchlist; up to 300 combinations per watchlist.
- Full passenger-count matching within one train and class, with no fare cap.
- Optional train filters and overnight-capable departure time windows.
- Priorities and deterministic date/route/class ordering among fresh candidates.
- Watchlist edit, pause/resume, duplicate, delete, and non-secret export/import. Imports start paused.
- Fresh/stale availability labels, displayed fares, observation timestamps, activity history and booking status.
- Independent optional Telegram and sound settings, cooldowns, Bangladesh-time quiet hours, and test controls.
- Telegram private-chat `/status`, `/pause`, `/resume`, `/mute` commands and status/pause buttons. Resume starts monitoring only, not booking.
- Encrypted railway/Telegram/passenger settings, password authentication, protected desktop pairing and local browser profile.
- Request deduplication, sequential polling, error backoff, durable notification queue and retention limits.
- One app-controlled booking attempt at a time; per-action mode/revision checks; restart and interrupted-attempt reconciliation.
- Local Chrome seat and passenger-form automation with explicit verification interruptions and manual payment handoff.
- Responsive interface, dark/light themes, tray controls, double-click Windows launchers and optional Docker hosting.

## Telegram setup

Open **@BotFather** in Telegram, send `/newbot`, and follow its instructions to choose a name and username ending in `bot`. Paste the token into **Settings → Telegram** and click **Connect Telegram**. Open the provided bot link, press **Start**, then return and click **I pressed Start — check connection**. The app finds your private chat ID, saves the connection encrypted, enables Telegram delivery, and queues a confirmation message. The private setup link expires after 10 minutes. Group chats are not supported.

Enable Telegram on the relevant watchlists. Global delivery is enabled by setup and can be toggled in Preferences. **Send test** explicitly queues a test even if normal delivery is disabled. Delivery status distinguishes pending, sent, failed, cancelled and expired messages. “Sent” means accepted by Telegram, not read by you.

The bot must not already be consumed by another polling application or have a webhook configured. Telegram control uses long polling. Availability messages expire rather than being delivered much later as if fresh. Quiet hours deliver messages silently.

## How desktop preparation works

The agent uses normal Selenium with an isolated persistent Chrome profile. Selenium Manager obtains a compatible driver on first use. No browser debugging port is exposed externally and no CAPTCHA-solving service is used.

The official browser's own search response must match the requested origin, destination, date and class. The agent uniquely identifies the train card and class row, reads selectable seats and verifies each becomes explicitly selected. Someone else's held seat is not treated as your selection. The full group is checked again before continuing.

For payment mode, known passenger fields are filled and the passenger confirmation control is clicked once. A visible official payment-method control establishes payment readiness. Unknown fields, OTP, uncertain selection, changed selectors or disconnection produce an attention state. The browser stays open for manual completion. The agent never clicks a payment-method control or submits a payment.

The adapter is based on structures inspected in the reference source; it has **not** been validated against a signed-in live railway session here. The current supported automatic stop is the visible `#bKash_button` payment-option control. If the site changes, it deliberately stops for review instead of claiming success.

After a handoff, resolve the attempt from **Bookings** only after checking the official site. “I completed payment” records your report and pauses that watchlist; it does not claim independent railway verification. If you bought in Rail Sheba, edit the watchlist and use **I booked in Rail Sheba**. Avoid a new independent purchase while an earlier desktop order remains unresolved.

If the app cannot recognize a train card or confirmed seat-selection state, start with **Notify only** and continue manually. The railway remains the authority on current availability and issued tickets.

## Optional always-on server

No server has been provisioned. For now, run the service on your PC. A free static-site host is not a replacement for this persistent service; a host must support a continuously running process and durable storage.

For a server with Docker and a domain pointed at it:

1. Copy `.env.example` to `.env`.
2. Set `DOMAIN`, a long random `RC_SETUP_TOKEN`, and a generated `RC_ENCRYPTION_KEY`.
3. Generate an encryption key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` in a trusted terminal. Keep it private and separate from backups.
4. Run `docker compose up -d --build`. Caddy provides HTTPS on ports 80/443; the application port is not published.
5. Open the HTTPS address and create the owner account using the setup token.
6. Pair the desktop with that HTTPS service. The desktop makes outbound authenticated requests. Monitoring continues on the server when the PC is off; automatic booking remains local-only.

Run one service process/container per data volume. SQLite transactions serialize claims; the scheduler is designed for one owner and one service process, not a horizontally scaled deployment. Container configuration is supplied but was not run here because Docker is not installed.

## Storage, maintenance and limits

- `data/companion.sqlite`: durable settings, watchlists, results, attempts, events and delivery state.
- `data/encryption.key`: local encryption key, unless `RC_ENCRYPTION_KEY` is configured.
- `data/desktop/agent.dpapi`: pairing configuration encrypted for the current Windows user.
- `data/desktop/railway-profile/`: the local railway Chrome profile; treat it as sensitive.
- `data/desktop/pending-report.json`: an unsent attempt report for recovery; contains selected seat labels, not credentials.
- Results retain up to 1,500 latest observations within 24 hours; activity retains 300 events; attempts retain 100 records; notification state retains 300 entries.

Back up the database using SQLite's backup API or while the service is stopped. Keep encryption keys separate and protected; losing the key makes stored connections unreadable. Do not share `data/`, browser profiles, runtime logs or pairing keys.

Station dropdowns use a bundled snapshot of the railway's public station catalogue. Search with spaces or underscores; readable labels preserve exact railway values when saved. Each side supports up to 10 stations in selection order. Previously saved stations remain editable even if absent from a newer catalogue. Refresh the bundled list with `.runtime\python\python.exe tools/update_stations.py` (or your environment's Python). The UI displays search coverage and cycle time. Many combinations take longer to revisit; a 60-second freshness window prevents old matches from starting automatic booking.

The app does not guarantee tickets, retain seats indefinitely, automatically verify independent Rail Sheba purchases, or detect a complete server outage through that same dead server. External uptime alerts, richer seat-layout preferences, automatic final ticket extraction and Bangla localization remain future work.

## Development and verification

Python 3.12+ is supported. With a system Python:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python tests/ui_smoke.py
.venv\Scripts\python run_server.py
```

On this workspace use `.runtime\python\python.exe` instead. `requirements-tested.txt` records the exact dependency versions used for local verification; production and desktop dependency lists are separate.

The automated suite covers matching, concurrent claims, fresh-result checks, session/permission boundaries, mode changes, restart recovery, notification deduplication, uncertain seat selection, passenger/payment transitions and OTP interruptions. UI smoke checks use an isolated temporary database with background railway requests disabled. Screenshot artifacts contain test watchlist configuration, not live ticket inventory.

No other repository in the parent folder is required by this project.
