---
name: collect-reel-links
description: Collect the reel links and view counts of an Instagram profile from the user's own logged-in Chrome (Claude in Chrome), so they can be scanned. Use when the user gives a profile URL/handle instead of individual reel links.
---

# collect-reel-links

`yt-dlp` cannot list an Instagram profile, so the links come from the browser. This skill only **reads** the profile page (scrolling, no clicks on content, no posting).
Needs the Claude in Chrome tools and the user logged in to Instagram in that Chrome (logged-out Instagram shows only ~12 reels and lower, stale view counts).

## Steps
1. `tabs_context_mcp` (create a tab group if empty), then `navigate` to `https://www.instagram.com/<handle>/reels/`.
2. Run this with `javascript_tool` (adjust `N`, default 30; pinned reels come first, so take a few extra):
```js
await new Promise(r=>setTimeout(r,2500)); const N=40, m={}; let stale=0, prev=0;
for(let i=0;i<40&&stale<4;i++){
  document.querySelectorAll('a[href*="/reel/"]').forEach(a=>{m[a.getAttribute('href')]=a.innerText.replace(/\n/g,' | ')});
  const n=Object.keys(m).length; stale=n===prev?stale+1:0; prev=n; if(n>=N)break;
  window.scrollBy(0,2200); await new Promise(r=>setTimeout(r,1400));
}
({n:Object.keys(m).length, loggedIn:!document.body.innerText.includes('Zaloguj się'), m})
```
3. Write two files in the working folder:
   - `urls.txt`: `https://www.instagram.com/<handle>/reel/<id>/` per line
   - `grid.tsv`: `<id><TAB><first number shown>` per line (e.g. `Ddt4bvQtagT<TAB>1,2 mln`)
4. **Caution:** if a tile shows two numbers (e.g. `23,8 tys. | 7521`), they are likes | comments, **not views**: leave that reel out of `grid.tsv` (views unknown).
5. Close the tab you opened. Then continue with the `scan-reels` skill: `python "${CLAUDE_PLUGIN_ROOT}/scripts/reel.py" scan --grid grid.tsv urls.txt`.

## If a reel cannot be downloaded (yt-dlp: "content isn't available")
Some reels need a login. **What does NOT work (tested):** `--cookies-from-browser chrome|edge` (Chromium encrypts cookies in a way yt-dlp cannot read, and Chrome locks the file while open), recording the page's audio and sending it anywhere (Instagram's CSP blocks `localhost`; Chrome blocks returning base64 data), reading media URLs from the network tab (not exposed).
**What works, in 2 clicks for the user:** install the open-source Chrome extension "Get cookies.txt LOCALLY", open instagram.com while logged in, click the extension, **Export** → save as `reel-data/cookies.txt`. Then set `cookies_file: cookies.txt` in `reel-data/config.yaml` and rescan. The file is a credential: it is in `.gitignore`, never commit or paste it.
Meanwhile, open the reel page in the Chrome tab and read the caption, counts and visible comments with `get_page_text`; save them as `reel-data/data/<id>/page_scrape.json` (`{"id","url","views","likes","comments","date":"dd.mm.yyyy","dur":seconds,"caption","about":"one sentence","cta"}`; `views` null if unknown) and say clearly that no transcript exists. `reel.py page` shows such reels as "tylko opis i liczby". (`report clean` never deletes folders that contain a `page_scrape.json` or `page_scrape.md`.)
