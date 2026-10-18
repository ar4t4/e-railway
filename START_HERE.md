# Start Railway Companion on this PC

1. Double-click **Launch Companion.cmd** and sign in at **http://127.0.0.1:8787**. Create a dashboard password on first use.
2. In **Settings**, click **Connect Railway**. This opens your regular Brave or Chrome browser with your normal profile, without browser automation.
3. On first use, enable the session extension: open `brave://extensions` (or `chrome://extensions`), turn on **Developer mode**, click **Load unpacked**, and select `E:\railway-companion\data\browser-extension`. The app prepares this folder when you click Connect Railway.
4. Refresh the Railway tab and sign in normally, completing any CAPTCHA or OTP. The extension syncs your session automatically for monitoring. Check the monitor status to confirm the railway accepts it. This normal-browser connection does not automate checkout.
5. Add a watchlist with stations, future dates, classes and passenger count. Enable **Start watching** and **Telegram alerts**.
6. Use **Away** for monitoring and Telegram alerts. Buy independently in Rail Sheba. Select **At PC** yourself when ready for desktop checkout preparation.

Closing the dashboard does not stop monitoring. Keep this PC powered on and connected to the internet.

## Prepare checkout while you are at the PC

1. In **Settings → Passenger details**, save the passenger names and contact information.
2. Edit the watchlist and choose **At PC: advance toward payment, then beep** (or seat selection only).
3. Turn on **Sound at PC** in the watchlist and **Desktop sound enabled** in Settings. Quiet hours suppress sound. The browser sound test is separate from native desktop beeps.
4. Ensure the desktop is **Ready**, then select **At PC** in Overview. Automation starts only when a fresh matching train has enough seats for the full group.
5. After a beep, inspect the railway Chrome window and complete any verification and payment yourself. A payment screen is not an issued ticket.
6. Check the official ticket confirmation before marking the booking completed in Companion. If you bought through Rail Sheba, use **I booked in Rail Sheba** on the watchlist to stop its alerts.

## Each time you use it

- Launch **Launch Companion.cmd** and click **Connect Railway** in Settings. With the extension enabled, sign in normally and the session syncs automatically. A service restart begins in Away mode.
- Keep this PC powered on and connected to the internet. The monitor launcher prevents ordinary sleep, but shutdown, hibernation, and internet loss interrupt monitoring.
- Closing the dashboard does not stop monitoring. Keep the agent's railway browser open for At PC automation.
- Telegram is configured separately in Settings. Use **Send test** to verify delivery; both the global switch and each watchlist's Telegram checkbox must be enabled for availability alerts.
- To stop both background programs, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Stop-Companion.ps1` from this project folder.

## Monitoring when login works only in your ordinary browser

Use **Away** mode. Sign in manually to the official railway website in your ordinary Brave window. In that signed-in tab, open Developer Tools with F12, choose Console, and run:

```js
copy(JSON.stringify({token:localStorage.token,uudid:localStorage.uudid,ssdk:localStorage.ssdk,user_agent:navigator.userAgent}))
```

This copies your railway session credentials. Paste them only into **Companion → Settings → Railway session · manual option → Session JSON**, then click **Save encrypted session**. Do not send the copied result in a chat. The monitor will try your active watchlists; check for a successful search rather than assuming that saving a session means it works. Repeat the manual connection if the session expires.

This option connects monitoring. It does not sign the desktop booking browser in or establish that the railway accepts automated checkout. Continue buying manually in Rail Sheba or your normal browser while the desktop remains unverified.

## Troubleshooting

To change browsers, first quit the desktop agent using its tray menu, then run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-Desktop.ps1 -Browser Brave` (or `-Browser Chrome`). This preference is remembered for future agent and dashboard launches.

If CAPTCHA fails in the agent-controlled browser, try the official website manually in your ordinary browser. Brave's lion icon controls Shields for that site. A working normal-browser login does not prove that the site accepts automation; the app cannot guarantee passage through the railway's verification. Do not repeatedly retry automated login. Monitoring is not verified until the app records a successful search.

Run `.runtime\python\python.exe tools/diagnose.py` from this folder for connection status without displaying credentials.

| What you see | What to do |
| --- | --- |
| Desktop offline | Click Connect Railway in Settings. Local startup and pairing are automatic. |
| Railway browser opened; sign in | Sign in inside the railway window. The app detects and syncs your session automatically. |
| Railway session needs attention | Sign in again if needed and sync. Read the latest monitor status to confirm it works. |
| Watchlist will not save | Read the error next to Save. Select stations, date and class; stay within the displayed search-combination limit. |
| Monitoring paused | Click Resume monitoring in Overview. |
| No notifications | Check the latest successful search and whether a full group matches; check both Telegram switches and recent delivery status in Settings. |
| Railway requested a cooldown | Leave the monitor running; it backs off and retries. |
| Desktop connection stays offline | Inspect data/desktop/agent-error.log locally. If you changed pairing keys, quit the tray agent and pair manually using Start-Desktop.ps1 -Pair. |
| Booking needs attention | Inspect the official browser. Finish or release the existing selection before starting another purchase. |

The railway website remains the authority on availability and issued tickets. Real seat selection and checkout require live verification; local tests alone cannot establish that every current website screen is supported.

If Windows cannot decrypt an old desktop pairing file, the local launcher repairs it automatically. Remote pairings still use the manual pairing flow. If a browser cannot open, the app shows a retry message; check connectivity and close an old Railway browser window before retrying.

For optional automated checkout, use **Settings ? Advanced desktop booking options ? Open automated booking browser**. That separate browser may be rejected by website verification; normal-browser monitoring and Rail Sheba purchases remain available.
