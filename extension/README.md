# reel-intel Collector (Chrome, Edge)

One click on a profile page instead of the browser console: the extension collects the video links and the view counts shown under them and hands them to your local reel-intel app, which starts the scan.

## Install (30 seconds, once)

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Switch **Developer mode** on (top right).
3. Click **Load unpacked** and choose this `extension` folder.
4. Pin the extension (puzzle icon, then the pin) so the button is always visible.

## Use

1. Start the app (`Aplikacja reel-intel.bat`, or `reel-intel serve`).
2. Open a profile in the browser where you are logged in (Instagram, TikTok, Facebook, or the Shorts tab of a YouTube channel).
3. Click the extension, set how many videos you want, click **Zbierz i skanuj** (Collect and scan).
4. Follow the progress in the app. On Instagram the extension opens the profile's **Reels** tab first, because only that tab shows view counts.

## What it can and cannot see

- It runs only on the tab where you click it (`activeTab`), and sends data only to `127.0.0.1` (your own computer). It has no access to your other tabs, cookies or passwords.
- The app accepts data only from a browser extension (the request must carry an extension origin and a custom header), never from a web page.
- Instagram and Facebook change their pages from time to time; if the collector finds nothing, the page layout probably changed.
- It does **not** read cookies, so videos that need a login still need `cookies.txt` (see the main README).

## Status

Version 0.1.0. The collecting code was run on a real Instagram profile and the app's endpoint was tested with the same requests the extension sends; loading it into your browser is a manual step, so please report anything odd the first time you use it.
