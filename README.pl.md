<p align="center"><img src="assets/logo.svg" width="112" alt="reel-intel logo"></p>

<h1 align="center">reel-intel</h1>

**Lokalna analiza krótkich wideo.** Podajesz linki (Instagram Reels, TikTok, YouTube Shorts, X, Facebook) albo cały profil i dostajesz dosłowny tekst tego, co w nich padło, sprawdzone liczby i uczciwą odpowiedź, *dlaczego niektóre filmy wybuchają*, w postaci strony zrozumiałej dla każdego. Za darmo, bez kluczy API, wszystko zostaje na Twoim komputerze.

*English: [README.md](README.md)*

```bash
pipx install git+https://github.com/kusmierekmikolaj-wq/reel-intel
reel-intel demo            # strona z danych przykładowych: bez konta i bez sieci
reel-intel doctor          # sprawdza Python, ffmpeg, kartę graficzną, yt-dlp, sieć
reel-intel serve           # aplikacja w przeglądarce: wklej linki, patrz na postęp, czytaj stronę
reel-intel run linki.txt --lang pl --open
```

## Co dostajesz
- **Dosłowną treść każdego wideo**: mowa z czasami (Whisper na karcie graficznej lub procesorze), opis, wyświetlenia, lajki, komentarze. Wideo bez mowy jest czytane z klatek.
- **Skrót każdego wideo bez żadnej usługi AI** (`reel-intel shorten`): hook, najważniejsze zdania, wezwanie do działania, tekst z ekranu. Każde zdanie jest cytatem z nagrania.
- **Jakość, którą da się sprawdzić**: osobny program kontrolny (`reel-intel qa`) wykrywa urwane teksty, pętle Whispera, zły język, błędne liczby i nieaktualne dane, a autotest udowadnia to na 15 wstrzykniętych usterkach (`reel-intel qa --selftest`). Szczegóły: [docs/QUALITY.md](docs/QUALITY.md).
- **Uczciwe „dlaczego wybuchło”**: mediana i odstające, grupy tematów z testem odporności (bez najlepszego i bez trzech najlepszych), tabela cech formatu z **99% przedziałami ufności** (różnica jest „wyraźna”, tylko gdy przedział nie obejmuje 1), testy godziny publikacji i komentarzy, strona po polsku lub angielsku z wyszukiwarką i pełnym tekstem.
- **Nie tylko transkrypcja**: `reel-intel visuals` mierzy obraz i dźwięk (tempo cięć, twarz na początku, tekst na ekranie, napisy wypalone w obrazie, tempo mowy, głośność) i dodaje to do tej samej tabeli cech; napisy wypalone w obrazie służą też jako niezależna kontrola mowy.
- **Wszystko, co napisano na ekranie** (`reel-intel visuals`, `reel-intel report screen`): repozytoria GitHub, nazwy skilli i narzędzi, komendy i pliki są odczytywane z klatek każdego filmu, nie tylko tych bez mowy, i wypisane z minutą pojawienia się (to kandydaci: OCR się myli, więc sprawdź je w nagraniu).
- **Długie nagrania**: film 30–60 minut jest transkrybowany wyłącznie z dźwięku, nigdy z automatycznych napisów platformy (test 37 minut trwał ok. 13 minut na średniej karcie graficznej).
- **Porównanie kont** (`reel-intel compare`): co powtarza się na różnych profilach, a co jest stylem jednego konta. Na 6 kontach (262 filmy) żadna cecha formatu się nie powtórzyła: [docs/CASE_STUDY.md](docs/CASE_STUDY.md).
- **Szybko**: ok. 5–7 s na wideo dla transkrypcji (pobierany jest tylko dźwięk), plus kilka sekund analizy obrazu (mała kopia wideo, najwyżej 480p; `--no-visuals` ją pomija), 4 równolegle. Na czystej mowie syntetycznej ok. 1% błędnych słów i 91–94% rozpoznanych nazw; dokładność na prawdziwych mówcach nie jest jeszcze zmierzona: [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## Polecenia
| polecenie | co robi |
|---|---|
| `run ŹRÓDŁO [--profile H] [--limit N] [--lang pl\|en] [--open]` | skan, skróty, kontrola i strona jednym poleceniem. Źródło: plik z linkami, adresy, kanał YouTube albo `grid.tsv` |
| `serve` | lokalna aplikacja w przeglądarce (tylko localhost) |
| `extension/` | wtyczka do Chrome i Edge: jedno kliknięcie na stronie profilu zbiera linki i liczby wyświetleń i uruchamia skan w aplikacji ([extension/README.md](extension/README.md)) |
| `snippet` | kod do konsoli zalogowanej przeglądarki, który kopiuje linki i liczby wyświetleń z profilu (`grid.tsv`) |
| `demo`, `doctor` | próba na danych przykładowych; kontrola środowiska z jednolinijkowymi poprawkami |
| `scan`, `shorten`, `qa`, `report`, `page` | poszczególne kroki. `report`: `groups`, `timing`, `hooks`, `features`, `texts`, `content`, `screen`, `csv`, `clean` |
| `compare FOLDER FOLDER ...` | kilka profili obok siebie |
| `score scenariusz.txt` | ocena własnego scenariusza względem tego, co dane wyraźnie pokazują, i najbardziej podobnych filmów |
| `watch ŹRÓDŁO [--alert 5]` | skan nowości, alert gdy film wybuchnie, dopisuje do `digest.md` ([docs/MONITORING.md](docs/MONITORING.md)) |
| `insights EKSPORT.csv` | dodaje to, co widzi tylko właściciel konta (zasięg, zapisy, udostępnienia) z eksportu platformy |
| `platforms` | ponowny test, które platformy działają dziś, na prawdziwych publicznych linkach |
| `mcp` / `reel-intel-mcp` | serwer MCP dla Claude Desktop i Cursora: `{"mcpServers": {"reel-intel": {"command": "reel-intel-mcp"}}}` |

Dane: `--data FOLDER` (albo `$REEL_DATA`, domyślnie `./reel-data`): jeden folder na wideo z `content_full.md`, `content_short.md`, `features.json`, `transcript.json`; `report texts` łączy wszystko w jeden czytelny plik. Język interfejsu: `--lang pl|en` albo `ui_language` w `<dane>/config.yaml`. Własne nazwy i słownictwo: skopiuj `glossary.yaml` do folderu danych.

## Platformy (testowane na prawdziwych linkach)
| platforma | pojedyncze linki | cały profil |
|---|---|---|
| Instagram Reels | 3/3 | `snippet` w zalogowanej przeglądarce |
| YouTube Shorts | 3/3 | adres kanału działa wprost |
| X (Twitter) | 3/3 | linki albo `snippet` |
| TikTok | 3/3 (potrzebne `curl-cffi`, instaluje się z pakietem) | `snippet` |
| Facebook | częściowo (część reeli nie działa) | `snippet` |

Szczegóły i data ostatniego testu: [docs/PLATFORMS.md](docs/PLATFORMS.md). Wyświetlenia z Instagrama pochodzą z siatki profilu (`snippet`), nie z yt-dlp. Niektóre posty wymagają zalogowanej sesji (`cookies.txt`, `cookies_file` w `config.yaml`).

## Z Claude Code
```
/plugin marketplace add kusmierekmikolaj-wq/reel-intel
/plugin install reel-intel@reel-intel
```
Skille: `scan-reels`, `collect-reel-links`, `why-it-went-viral`.

## Granice
Platformy zmieniają zabezpieczenia; retencji, zapisów i udostępnień nie widać publicznie, więc wnioski to **hipotezy z etykietami pewności**, nie dowody. Regulamin platform ogranicza automatyczne pobieranie: przeczytaj [docs/LEGAL.md](docs/LEGAL.md). Zmiany: [CHANGELOG.md](CHANGELOG.md). Jak pomóc: [CONTRIBUTING.md](CONTRIBUTING.md).

Licencja MIT.
