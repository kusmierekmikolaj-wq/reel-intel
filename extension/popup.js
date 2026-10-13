// reel-intel Collector: reads the video links (and view counts, where the page shows them) of the profile in the active tab and hands them to
// the local reel-intel app. It needs no access to any website except the tab you click it on (activeTab), and talks only to 127.0.0.1.
const T = {
  pl: { conn_on: "Połączono z aplikacją", conn_off: "Aplikacja nie działa", conn_wait: "Szukam aplikacji…", limit: "Ile filmów", lang: "Język wyniku", go: "Zbierz i skanuj",
    notSupported: "Otwórz profil", notSupportedWhat: "Na Instagramie, TikToku, Facebooku lub w zakładce Shorts kanału YouTube, a potem kliknij wtyczkę.",
    what: "Wtyczka zbierze filmy z tej strony i wyśle je do aplikacji na tym komputerze.", noApp: "Uruchom „Aplikacja reel-intel” (plik .bat) i otwórz to okno ponownie.",
    toReels: "Otwieram zakładkę Reels (tam widać wyświetlenia)…", collecting: "Zbieram linki, przewijam stronę…", sending: "Wysyłam do aplikacji…",
    none: "Nie znaleziono filmów na tej stronie. Przewiń profil lub otwórz zakładkę z reelami.", doneT: (n) => `Wysłano ${n} ${n === 1 ? "film" : "filmów"}`, doneS: "Skanowanie trwa w aplikacji.",
    open: "Otwórz aplikację ›", err: "Błąd", foot: "Wszystko zostaje na tym komputerze." },
  en: { conn_on: "Connected to the app", conn_off: "The app is not running", conn_wait: "Looking for the app…", limit: "How many videos", lang: "Result language", go: "Collect and scan",
    notSupported: "Open a profile", notSupportedWhat: "On Instagram, TikTok, Facebook or the Shorts tab of a YouTube channel, then click the extension.",
    what: "The extension collects the videos on this page and sends them to the app on this computer.", noApp: "Start \"Aplikacja reel-intel\" (the .bat file) and open this window again.",
    toReels: "Opening the Reels tab (that is where views are shown)…", collecting: "Collecting links, scrolling the page…", sending: "Sending to the app…",
    none: "No videos found on this page. Scroll the profile or open the reels tab.", doneT: (n) => `Sent ${n} ${n === 1 ? "video" : "videos"}`, doneS: "Scanning continues in the app.",
    open: "Open the app ›", err: "Error", foot: "Everything stays on this computer." },
};
const L = (navigator.language || "en").startsWith("pl") ? "pl" : "en", t = T[L];
const $ = (id) => document.getElementById(id);
const SITES = [["instagram.com", "Instagram"], ["tiktok.com", "TikTok"], ["facebook.com", "Facebook"], ["youtube.com", "YouTube"]];
let APP = null, TAB = null, N = 30, LANG = L, SITE = null;

// runs INSIDE the page (serialised by chrome.scripting): scrolls the profile and returns the links with the number written under each video
async function collect(limit) {
  const host = location.hostname;
  const sel = host.includes("tiktok") ? 'a[href*="/video/"]' : host.includes("youtube") ? 'a[href*="/shorts/"]' : host.includes("facebook") ? 'a[href*="/reel/"],a[href*="/videos/"]' : 'a[href*="/reel/"]';
  const found = new Map(); let stale = 0, prev = 0;
  for (let i = 0; i < 80 && stale < 4; i++) {
    document.querySelectorAll(sel).forEach((a) => { const u = a.href.split("?")[0]; if (!found.get(u)) found.set(u, ((a.innerText || "").split("\n")[0] || "").trim()); });
    const n = found.size; stale = n === prev ? stale + 1 : 0; prev = n;
    if (n >= limit) break;
    window.scrollBy(0, 2200); await new Promise((r) => setTimeout(r, 1400));
  }
  const first = location.pathname.split("/").filter(Boolean)[0] || host;
  return { profile: first.replace(/^@/, ""), items: [...found].slice(0, limit).map(([url, views]) => ({ url, views })) };
}

function profileName(url) {
  try {
    const u = new URL(url), parts = u.pathname.split("/").filter(Boolean);
    if (!parts.length) return null;
    if (u.hostname.includes("youtube")) return (parts[0].startsWith("@") || parts[0] === "channel" || parts[0] === "c") ? decodeURIComponent(parts[0] === "channel" || parts[0] === "c" ? parts[1] || parts[0] : parts[0]) : null;
    const p = parts[0];
    return ["reel", "reels", "p", "explore", "stories", "watch", "video"].includes(p) ? null : decodeURIComponent(p);
  } catch (e) { return null; }
}

async function findApp() {
  for (let port = 8765; port < 8775; port++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/ping`, { signal: AbortSignal.timeout(700) }); if ((await r.json()).app === "reel-intel") return `http://127.0.0.1:${port}`; } catch (e) { /* next port */ }
  }
  return null;
}
function setConn(state) { $("conn").className = "conn " + state; $("conn_t").textContent = state === "on" ? t.conn_on : state === "off" ? t.conn_off : t.conn_wait; }
function pick(groupId, value, onPick) {
  const btns = [...$(groupId).querySelectorAll("button")];
  const mark = (v) => btns.forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === String(v))));
  btns.forEach((b) => b.addEventListener("click", () => { mark(b.dataset.v); onPick(b.dataset.v); })); mark(value);
}
function busy(on) { $("bar").hidden = !on; $("go").setAttribute("aria-disabled", String(on || !SITE || !APP)); }

async function init() {
  $("l_n").textContent = t.limit; $("l_lang").textContent = t.lang; $("go").textContent = t.go; $("foot").textContent = t.foot; document.documentElement.lang = L;
  pick("seg_n", N, (v) => { N = +v; }); pick("seg_lang", LANG, (v) => { LANG = v; });
  setConn("wait");
  [TAB] = await chrome.tabs.query({ active: true, currentWindow: true });
  SITE = (SITES.find(([h]) => (TAB.url || "").includes(h)) || [])[1] || null;
  const name = SITE && profileName(TAB.url);
  $("site").textContent = SITE || ""; $("name").textContent = SITE ? (name ? (name.startsWith("@") ? name : "@" + name) : SITE) : t.notSupported; $("what").textContent = SITE ? t.what : t.notSupportedWhat;
  APP = await findApp(); setConn(APP ? "on" : "off");
  if (!APP) { $("alert").hidden = false; $("alert").textContent = t.noApp; }
  busy(false);
}

$("go").addEventListener("click", async () => {
  if ($("go").getAttribute("aria-disabled") === "true") return;
  busy(true); $("alert").hidden = true; $("done").hidden = true; $("openapp").hidden = true; $("msg").className = "msg"; $("msg").textContent = t.collecting;
  try {
    const u = new URL(TAB.url);
    if (u.hostname.includes("instagram.com") && !/\/reels\/?$/.test(u.pathname)) {          // only the Reels tab of a profile shows view counts
      const name = u.pathname.split("/").filter(Boolean)[0];
      if (name && !["reel", "p", "explore", "reels"].includes(name)) {
        $("msg").textContent = t.toReels;
        await chrome.tabs.update(TAB.id, { url: `https://www.instagram.com/${name}/reels/` });
        await new Promise((r) => { const f = (id, info) => { if (id === TAB.id && info.status === "complete") { chrome.tabs.onUpdated.removeListener(f); r(); } }; chrome.tabs.onUpdated.addListener(f); setTimeout(r, 15000); });
        await new Promise((r) => setTimeout(r, 2500));
        $("msg").textContent = t.collecting;
      }
    }
    const [res] = await chrome.scripting.executeScript({ target: { tabId: TAB.id }, func: collect, args: [N] });
    const data = res.result;
    if (!data || !data.items.length) { $("msg").className = "msg err"; $("msg").textContent = t.none; busy(false); return; }
    $("msg").textContent = t.sending;
    const r = await fetch(`${APP}/api/ingest`, { method: "POST", headers: { "Content-Type": "application/json", "X-Reel-Intel-Client": "extension" },
      body: JSON.stringify({ profile: data.profile, items: data.items, lang: LANG, limit: N }) });
    const j = await r.json();
    if (!j.ok) throw new Error(j.msg);
    $("msg").textContent = ""; $("bar").hidden = true;
    $("done_t").textContent = t.doneT(data.items.length); $("done_s").textContent = t.doneS; $("done").hidden = false;
    $("openapp").href = APP + "/"; $("openapp").textContent = t.open; $("openapp").hidden = false;
    $("go").setAttribute("aria-disabled", "false");
  } catch (e) { $("msg").className = "msg err"; $("msg").textContent = `${t.err}: ${e.message}`; busy(false); }
});
init();
