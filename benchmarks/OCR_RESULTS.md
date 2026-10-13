# OCR benchmark (synthetic frames)

40 frames (20 Polish, 20 English), known text over a noisy photo-like background, white text with a dark outline, 30-48 px on a 540x960 frame. CER = character error rate after lower-casing and removing punctuation (lower is better). Synthetic text is cleaner than real video: an upper bound.

| language | step | CER | lines read exactly |
|---|---|---|---|
| pl | raw OCR | 7.9% | 1/20 |
| pl | + diacritics restored (built-in words only) | 2.5% | 10/20 |
| pl-heldout | raw OCR | 7.1% | 1/20 |
| pl-heldout | + diacritics restored (built-in words only) | 4.4% | 5/20 |
| en | raw OCR | 0.3% | 19/20 |

## Misses

| language | expected | read |
|---|---|---|
| pl | Zapisz ten film, żeby nie zgubić instrukcji | Zapisz ten film, żeby nie zgubie instrukcji |
| pl | Pierwsze sekundy decydują o wszystkim | Pierwsze sekundy decyduja wszystkim |
| pl | Błąd, który kosztuje Cię klientów | Błąd, który kosztuje Cie klientów |
| pl | Trzy sposoby na lepszą organizację dnia | Trzy sposoby na lepsza organizacje dnia |
| pl | Dlaczego reklamy przestają działać | Dlaczego reklamy przestaja działać |
| pl | Zrób to zanim zaczniesz kampanię | Zrob to zanim zaczniesz kampanię |
| pl | Nie wierz w mity o algorytmie | Nie wierz w mity 0 algorytmie |
| pl | Źle ustawiona reklama to stracone pieniądze | Złe ustawiona reklamą to stracone pieniądze |
| pl | Zostań ze mną, a pokażę Ci jak to ogarnąć | Zostan ze mna, a pokażę Ci jak to ogarnać |
| pl | Przestań wyrzucać pieniądze w błoto | Przestan wyrzucać pieniądze w btoto |
| pl-heldout | Jak przygotować się do wyjazdu w góry | Jak przygotować się do wyjazdu w gory |
| pl-heldout | Dlaczego warto czytać książki wieczorem | Dlaczego warto czytać ksiazki wieczorem |
| pl-heldout | Pięć nawyków, które zmieniły mój poranek | Pie? nawyk6w, kt6re zmienily m?j poranek |
| pl-heldout | Zdrowe śniadanie w pięć minut | Zdrowe sniadanie w piec minut |
| pl-heldout | Tego błędu nie popełniaj podczas rozmowy kwalifikacyjnej | Tego błędu nie popetniaj podczas rozmowy kwalifikacyjnej |
| pl-heldout | Co robić, gdy brakuje motywacji | Co robic, gdy brakuje motywacji |
| pl-heldout | Najczęstsze pytania naszych klientów | Najczestsze pytania naszych klientów |
| pl-heldout | Zmień swoje przyzwyczajenia od jutra | Zmien swoje przyzwyczajenia od jutra |
| pl-heldout | Mała zmiana, która robi dużą różnicę | Mała zmiana, która robi duża roznice |
| pl-heldout | Poradnik dla osób zaczynających treningi | Poradnik dla osob zaczynających treningi |
| pl-heldout | Ile naprawdę kosztuje własny ogródek | lle naprawde kosztuje wlasny ogrodek |
| pl-heldout | Wybierz mądrze zanim cokolwiek kupisz | Wybierz madrze zanim cokolwiek kupisz |
| pl-heldout | Ten trik oszczędza godzinę dziennie | Ten trik oszczedza godzine dziennie |
| pl-heldout | Zapytaj mnie o cokolwiek w komentarzach | Zapytaj mnie 0 cokolwiek w komentarzach |
| pl-heldout | Skąd wiedzieć, że to się opłaca | Skad wiedzieć, ze to się oplaca |
| en | Three ways to organise your day better | Three ways to organise your day better O |
