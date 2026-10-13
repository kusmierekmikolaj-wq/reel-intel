# Terms of use and legal risk / Zasady użycia i ryzyko prawne

*English summary: the tool analyses videos **you** point it at, locally, for pattern analysis. It is not affiliated with any platform. Platform terms restrict automated collection; you use it at your own risk. Nothing here is legal advice; the checklist at the end must be settled by the owner (and ideally a lawyer) before the repository is made public.*

## 1. Co robi narzędzie
Pobiera z wskazanych publicznych filmów (Instagram, TikTok, YouTube, X, Facebook) ścieżkę dźwiękową, opis i publiczne liczby oraz (do analizy obrazu, można wyłączyć opcją `--no-visuals`) małą kopię wideo w rozdzielczości najwyżej 480p, zapisaną tylko lokalnie, zamienia mowę na tekst **na Twoim komputerze** i liczy statystyki. Nie działa przez oficjalne API platform, nie hostuje cudzych nagrań, nie ma konta ani serwera, nie wysyła danych nigdzie (poza pobraniem filmu z platformy i, przy pierwszym użyciu, modeli z Hugging Face).

## 2. Zasady dla użytkownika
1. **Regulaminy platform.** Regulaminy ograniczają automatyczne pobieranie danych. Używasz narzędzia na własną odpowiedzialność; sprawdź, czy Twój przypadek (np. analiza komercyjna) jest dopuszczalny.
2. **Zakres.** Tylko publiczne filmy i profile. Nie omijaj zabezpieczeń, nie zakładaj kont do zbierania danych, nie przełamuj blokad.
3. **Tempo.** Domyślne limity (liczba filmów na przebieg, równoległość, losowe przerwy między pobraniami, ponawianie z odstępem) chronią serwisy przed obciążeniem. Nie zwiększaj ich bez potrzeby.
4. **Prawa autorskie.** Nagrania i teksty należą do autorów. Używaj ich do analizy wzorców (temat, pierwsze zdanie, struktura, liczby), **nie do ponownej publikacji**. Narzędzie `score` porównuje Twój scenariusz ze wzorcami, nie podpowiada cudzych zdań do skopiowania.
5. **Poświadczenia.** `cookies.txt` to poświadczenie konta. Nie wysyłaj go nigdzie, nie dodawaj do repozytorium (jest w `.gitignore`). Używaj konta pomocniczego.
6. **Dane osobowe.** Głos, imię i nazwisko oraz treść wypowiedzi twórców to dane osobowe. Zostają lokalnie w folderze danych (`reel-data/`); nie udostępniaj go dalej i usuń, gdy przestaje być potrzebny (wystarczy skasować folder).
7. **Wnioski.** To hipotezy z ograniczonych danych, nie dowody; nie podejmuj na ich podstawie decyzji, których nie da się cofnąć.

## 3. Czego repozytorium nie zawiera
Dane demonstracyjne są **syntetyczne** (wymyślone). Benchmark używa własnych skryptów czytanych syntezatorem mowy. Studium przypadku zawiera wyłącznie zagregowane liczby, bez tekstów i nagrań, z zanonimizowanymi kontami. W repozytorium nie ma plików `cookies.txt`, nagrań ani folderów danych (`.gitignore`).

## 4. Pozycjonowanie
Nazwy platform pojawiają się tylko jako informacja, z czym narzędzie współpracuje. Projekt nie jest powiązany z żadną platformą i nie używa jej znaków jako nazwy ani hasła. W README jest zdanie o braku powiązań.

## 5. Zgłoszenia usunięcia
Jeśli twórca lub platforma prosi o usunięcie czegokolwiek z repozytorium (np. wzmianki o koncie), zgłoszenie idzie przez Issues lub e-mail właściciela; odpowiedź w ciągu 7 dni, usunięcie bez dyskusji, gdy dotyczy cudzych danych. Dane użytkowników leżą u nich lokalnie i są poza kontrolą projektu.

---

# English version

reel-intel analyses videos **you** point it at, on your own computer, to find patterns. It is not affiliated with Instagram, TikTok, YouTube, X, Facebook or any other platform, and nothing here is legal advice.

1. **Platform terms.** Platform terms restrict automated collection of data. You use the tool at your own risk; check whether your use (for example commercial analysis) is allowed.
2. **Scope.** Public videos and profiles only. Do not bypass protections, do not create accounts to collect data.
3. **Pace.** The defaults (videos per run, parallelism, random pauses between downloads, spaced retries) protect the services. Do not raise them without a reason.
4. **Copyright.** Recordings and texts belong to their authors. Use them to analyse patterns (topic, first sentence, structure, numbers), **not to republish them**. `score` compares your draft with patterns; it does not hand out other people's sentences.
5. **Credentials.** `cookies.txt` is an account credential. Never send it anywhere or commit it (it is in `.gitignore`). Use a secondary account.
6. **Personal data.** Voices, names and statements of creators are personal data. They stay in your local data folder (`reel-data/`); do not share the folder and delete it when you no longer need it.
7. **Conclusions.** They are hypotheses from limited data, not proof; do not base irreversible decisions on them.

**What the repository does not contain:** the demo data is synthetic, the benchmark uses scripts written for it and read by a speech synthesiser, and the case study contains only aggregate numbers about anonymised accounts. No recordings, cookies or data folders are included.

**Removal requests:** if a creator or a platform asks for something to be removed from this repository, open an issue; we will answer within 7 days and remove it without discussion when it concerns someone else's data. Data on users' computers is outside the project's control.
