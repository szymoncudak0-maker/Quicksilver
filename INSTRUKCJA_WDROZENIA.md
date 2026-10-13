# Quicksilver ONLINE 2.0

**Pełna aplikacja internetowa z serwerowym pobieraniem zdjęć EAN.**

Wystarczy przeglądarka Edge lub Chrome. Na komputerze użytkownika **nie trzeba instalować Pythona, EXE, dodatków ani bibliotek**. Python działa wyłącznie na **serwerze hostingowym**, uruchamianym w Dockerze.

> **WAŻNE:** paczka jest projektem gotowym do wdrożenia, nie gotową domeną. Link HTTPS powstaje dopiero po uruchomieniu hostingu przez właściciela konta. Plików firmowych nie należy wysyłać na prywatny hosting bez zgody administratora IT.

## Zawartość

- `app_online.py` – API FastAPI, logowanie, sesje, uploady, zadania, wyniki i usuwanie danych.
- `engine.py` – logika pobierania/konwersji i tworzenia plików Excel / ZIP / folderów.
- `safe_images.py` – bezpieczne pobieranie zdjęć z adresów publicznych HTTPS.
- `static/` – nowy panel WWW (HTML/CSS/JS oraz logo).
- `Dockerfile`, `render.yaml` – wdrożenie na Render.
- `compose.yaml` – wdrożenie przez firmowe IT do własnego środowiska Docker.
- `requirements.txt` – biblioteki serwera, **nie użytkownika**.
- `tests/` – automatyczne testy działania i izolacji kont.

## Opcja A: Render (najprostszy sposób uzyskania linku)

1. Wejdź na **https://github.com/** i utwórz **prywatne repozytorium** np. `quicksilver-online`.
2. Rozpakuj `Quicksilver_ONLINE_2.0.zip`, wgraj pliki i folder `static` **do katalogu głównego repozytorium**, nie zewnętrzny ZIP. **Nie wgrywaj `.env` ani żadnych prawdziwych Exceli/EAN-ów.**
3. Zaloguj się na **https://dashboard.render.com/**, wybierz **New → Blueprint**, połącz prywatne repozytorium. Render rozpozna `render.yaml`.
4. Wymagane ustawienie sekretu: `QS_ACCESS_PASSWORD`. Wpisz długie, losowe hasło (minimum 12 znaków, zalecane 20+), używane potem na ekranie logowania. Nie wpisuj hasła w plikach na GitHubie.
5. Uruchom wdrożenie. Poczekaj na status `Live` w Render.
6. Otwórz przydzielony przez Render adres `https://<nazwa-usługi>.onrender.com/`. Ten adres podajesz uprawnionym użytkownikom.
7. Użytkownicy otwierają stronę w przeglądarce, logują się hasłem i przetwarzają Excel.

Domyślny plan w `render.yaml` to **Starter (płatny)**. Testowo można ręcznie zmienić plan na `free`, ale Render Free usypia stronę po okresie bezczynności, a restart usługi może przerwać zadania i usunąć ich wyniki. Dlatego nie jest zalecany dla produkcyjnego przetwarzania plików. Ceny i dostępne plany sprawdź przed aktywacją w panelu Render.

**Połączenie z GitHub i założenie hostingu wymagają Twojego konta oraz działań po Twojej stronie.** Kod nie wykonuje automatycznie wdrożenia.

## Opcja B: serwer firmowy / zatwierdzony hosting

Przekaż `Dockerfile`, `compose.yaml` i projekt administratorowi IT.

- Reverse proxy (Caddy, Nginx, firmowy gateway) musi udostępniać usługę przez **HTTPS**.
- Ustaw `QS_ACCESS_PASSWORD` jako sekret w środowisku (poza repozytorium).
- Własny serwer powinien mieć limit ruchu wychodzącego i ewentualną listę domen zdjęć `QS_IMAGE_HOSTS`.
- `compose.yaml` nie wystawia portu publicznie: usługę trzeba połączyć z zatwierdzonym reverse proxy i jego siecią. Nie należy zmieniać `QS_SECURE_COOKIES=true` dla publicznej witryny.
- Aplikacja używa **jednego procesu** (`--workers 1`). Nie uruchamiaj kilku replik bez dodatkowej kolejki zadań i zewnętrznej pamięci sesji.
- W środowisku firmowym rekomendowane jest uwierzytelnianie **SSO w firmowym gatewayu** zamiast współdzielonego hasła. Obecne hasło to rozwiązanie podstawowe, nie zamiennik zarządzania tożsamością w przedsiębiorstwie.

## Obsługa strony

1. Otwórz link HTTPS i wpisz hasło.
2. Wybierz Excel `XLSX`, `XLSM` albo `CSV` (do 25 MB), a następnie arkusz.
3. Ustaw numer pierwszego wiersza danych oraz kolumny EAN i URL zdjęć, liczbę kolumn i maksymalną liczbę zdjęć.
4. Wybierz `ZIP` (domyślny) lub `folder`. Określ opcjonalny limit MB i podział na paczki.
5. Wybierz format konwersji zdjęć i kliknij **Pobierz zdjęcia i utwórz Excel**.
6. Śledź pasek postępu i ewentualne błędy. Możesz zatrzymać bieżące zadanie.
7. Pobierz kompletną paczkę wynikową albo indywidualny plik Excel/ZIP.

### Oczekiwany format wyników

W Excelu są dokładnie kolumny:

- `Typ`
- `EAN`
- `Zdjęcie okładki/produktu`
- `Dodatkowe zdjęcia`

Przy wyborze ZIP plik `zdjecia.zip` zawiera **same zdjęcia w głównym katalogu** archiwum, np. `5901234123457.png`, `5901234123457_2.png`. Nie ma dodatkowego folderu wewnątrz `zdjecia.zip`. Przy wyborze folderu końcowy ZIP pobrany przez przeglądarkę zawiera katalog `zdjecia/` (to normalne: przeglądarka pobiera katalog jako archiwum). Przy podziale na paczki pojawiają się `paczka_001`, `paczka_002` itd.

Katalog wyników jest nazywany po **dacie i godzinie zakończenia** w polskiej strefie czasowej, np. `Quicksilver_2026-10-13_11-15-30`.

### Limity i przechowywanie

- Import: maksymalnie **25 MB**.
- Jedno pobierane zdjęcie: maksymalnie **40 MB**.
- Wynik jednego zadania: domyślnie maksymalnie **500 MB** sumy zapisanych zdjęć, ustawienie admina `QS_MAX_OUTPUT_MB`.
- Równoczesne zadania: domyślnie **2** (konfiguracja `QS_MAX_JOBS`).
- Wyniki i przesłane pliki: kasowane na serwerze po około **2 godzinach**, przy kolejnej aktywności aplikacji. Restart serwera może je usunąć wcześniej; **pobierz wyniki od razu**.
- Sesja logowania: maksymalnie **12 godzin**; po wygaśnięciu wymagane ponowne logowanie.
- Aplikacja wymaga udostępnionych publicznie adresów zdjęć HTTPS. Nie pobiera obrazów z adresów intranetowych czy wymagających logowania.
- **Pamiętaj:** limit paczki to rozmiar zdjęć przed spakowaniem, a nie rozmiar pliku ZIP. Wszystkie zdjęcia jednego EAN pozostają razem, nawet jeśli przekroczą zadany limit.

## Konfiguracja administratora

| Zmienna środowiskowa | Cel | Przykład |
|---|---|---|
| `QS_ACCESS_PASSWORD` | wymagane hasło logowania, min. 12 znaków | ustaw jako sekret hostingu |
| `QS_SECURE_COOKIES` | ciasteczka tylko przez HTTPS, obowiązkowo `true` online | `true` |
| `QS_MAX_JOBS` | maks. liczba równoczesnych zadań | `2` |
| `QS_MAX_OUTPUT_MB` | budżet zdjęć w pojedynczym zadaniu | `500` |
| `QS_IMAGE_HOSTS` | opcjonalna lista zaufanych domen obrazów, przecinki | `cdn.example.com,*.cdn.example.org` |
| `QS_ALLOW_HTTP_IMAGES` | zezwalaj na niezabezpieczone zdjęcia HTTP, domyślnie `false` | `false` |
| `QS_PUBLIC_ORIGIN` | opcjonalnie dokładny adres HTTPS strony do weryfikacji pochodzenia żądań | `https://twojadomena.pl` |

## Ochrona danych i uwagi bezpieczeństwa

- Nie udostępniaj hasła osobom spoza uprawnionej grupy. Po wycieku zmień je w panelu hostingu.
- Logowanie korzysta z ciasteczek `HttpOnly`, `SameSite=Strict`, tokenu CSRF, ochrony anty-bruteforce oraz izolacji prac według sesji.
- Pobieranie adresów z Excela blokuje prywatne i lokalne IP, niestandardowe porty i domyślnie HTTP; DNS jest rozwiązywany przed nawiązaniem połączenia z przypiętym publicznym IP, a przekierowania sprawdzane oddzielnie.
- To mały system do kontrolowanego użycia, a nie zewnętrzny, certyfikowany system do przechowywania danych firmowych; nie wgrywaj danych poufnych bez zgody IT i oceny hostingu, kraju przetwarzania oraz wymaganych zasad RODO.
- Logi i pliki źródłowe są przechowywane tylko na instancji serwera w katalogu tymczasowym. Zmiany lokalnych plików mogą zniknąć na restarcie.
- Zainstalowanie samego Render nie gwarantuje dostępu z firmowego komputera: domena może wymagać zatwierdzenia w zaporze firmowej.

## Testy (dla administratora)

Po zainstalowaniu zależności z `requirements.txt` i testowych `pytest` oraz `httpx`:

```bash
python -m pytest -q tests/
```

Opcjonalny test lokalny administratora (nie na komputerach pracowników):

```bash
export QS_ACCESS_PASSWORD='tu-wstaw-silne-testowe-haslo'
export QS_SECURE_COOKIES=false
uvicorn app_online:app --host 127.0.0.1 --port 8000 --workers 1
```

Otwórz `http://127.0.0.1:8000` na tym samym komputerze testowym. W publicznym wdrożeniu `QS_SECURE_COOKIES` musi pozostać `true`, a połączenie musi być HTTPS.
