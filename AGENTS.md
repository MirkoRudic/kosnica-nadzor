Važe pravila iz `RULES.md` u repou MirkoRudic/vajb-osnova (za modele i štampu i `RULES_3D_PRINTING.md`). Ovde su arhitektura, zamke i izuzeci. Šta app radi iz ugla korisnika je u `SPECIFICATION.md` — svaka izmena ponašanja prvo tamo. Dokle smo stigli: `STATUS.md`. Šta je već probano i nije radilo: `ATTEMPTS.md` — pročitati pre predloga rešenja.

# Košnica Nadzor

Prati 3D štampu na K1 Max i K2 Plus u radionici i javlja na Telegram kad nešto krene naopako.
Računar u radionici: desktop-fk7e7jt, folder `C:\Users\zzroz\Desktop\VAJB\kosnica-nadzor`. Port 8800 (APPLICATIONS.md).

## Arhitektura
- `nadzor.pyw` — pokretač: server 127.0.0.1:8800, go2rtc, ikonica pored sata, Edge --app. `--tiho` = bez prozora (prečica u Startup).
- `kn/printer.py` — websocket :9999 svakog štampača (bez root-a). Prva poruka je pun status, posle samo promene → spajaju se.
- `kn/camera.py` — `bin/go2rtc.exe` pravi lokalni RTSP (127.0.0.1:8554) od Creality WebRTC-a (`#format=creality`), OpenCV čita kadrove.
- `kn/detect.py` — provera slike: medijana ~45 s (briše pokretnu glavu) poredi se sa onom od pre 60 s; najveća promenjena mrlja mora da ostane na istom mestu 2 provere zaredom. Prag se uči za svaku štampu (prve 2 min).
- `kn/core.py` — pravila (greška štampača, pauza, kraj, temperatura dizne, zastoj 10 min, štampač nestao, kamera nestala), događaji, komande sa potvrdom (posle komande čeka promenu `state`, inače javlja da štampač NIJE potvrdio).
- `kn/telegram.py` — long polling (samo odlazne veze). Prva grupa koja pošalje /start se veže; druge se ignorišu. Dugmad: Pauziraj/Nastavi, U redu je (30 min tišine), Nova slika, Zaustavi (+ potvrda).
- Poruke o poslu: id-jevi bot poruka po poslu u `data/tg_poruke.json`; poruka o završetku/prekidu ima dugme „Obriši poruke“ koje ih briše iz grupe (ta poruka ostaje). Telegram dozvoljava brisanje do 48 h.
- `kn/tunnel.py` — `bin/cloudflared.exe`: besplatni quick tunnel (`*.trycloudflare.com`, adresa se menja pri svakom startu, app je šalje u Telegram; /strana ponovo šalje link). Kasnije, sa domenom: imenovani tunel preko `CLOUDFLARE_TUNNEL_TOKEN` u `.env`.
- Pristup spolja: zahtevi kroz tunel (zaglavlje `Cf-Ray`/`Cf-Connecting-Ip`) traže tajni ključ — link `/?k=<remote_key>` postavlja kolačić. „Novi link“ menja ključ. Gašenje app, tokeni i brisanje dnevnika spolja nisu dozvoljeni.
- `kn/preview.py` — „Projekat“: G-code posla se čita sa štampača (samo GET) i crta do trenutnog sloja (urađeno žuto, trenutni sloj belo, ostatak sivo). Pogled kamere: dugme sa nišanom → klik na 4 ugla ploče (prednji levi, prednji desni, zadnji desni, zadnji levi) → `calibrateCamera` iz jednog pogleda (žižna daljina + položaj, bez distorzije) → `settings.views`. Ploča se spušta sa Z, kamera je na ramu (K1 izmereno; K2 nije provereno). Kontrolna slika: `data/slike/kalibracija_<id>.jpg`. K2: Moonraker `:7125/server/files/gcodes/<ime>` (radi, 20.11). K1: ne daje G-code (proverene adrese, 20.11) → `kn/project3mf.py` traži posao u `projects_dir` (`Desktop\3D`): izvezen .gcode istog imena ili objekat istog imena u .3mf (mreža se seče na slojeve po visini sloja iz projekta). Poslednja rezerva je slika projekta sa štampača `/downloads/original/current_print_image.png` (nije provereno).
- `kn/web.py`, `static/` — lokalna strana. Ugasi je u zaglavlju pored Podešavanja (P4), vidi se samo na računaru u radionici, ne preko tunela. `static/index.html` se pravi iz `src/index.src.html` (ikonice Tabler, build.py u razvoju).
- Podaci: `data/settings.json`, `data/logs/`, `data/slike/` (slike događaja, čuva se poslednjih 300), `data/go2rtc.yaml` (pravi se pri startu).
- Tajna: `.env` → `TELEGRAM_TOKEN`, `CLOUDFLARE_TUNNEL_TOKEN` (upisuje ih app iz Podešavanja; Claude ne sme da piše `.env` na računaru) (F6, nikad u dnevnik; greške se čiste od tokena).

## Poznate zamke
- Pauza `{"method":"set","params":{"pause":1}}` RADI na K1 (20.11.2026): state 1 → 6 → 5 → 8 i ostaje 8 dok je pauzirano. Nastavak i stop još nisu provereni.
- `state` kodovi: 0, 1, 2, 5, 6, 8 izmereni; 8 = pauzirano (tačno značenje nije poznato); 3/4 iz ha_creality_ws, nije dokazano.
- Telegram pregled linka: link sa ključem šalje se bez pregleda (inače ga Telegram server otvori).
- Pragovi provere slike podešeni na sintetičkom testu (snimak K2 + nacrtana mrlja ~3% kadra): „niska“ ne hvata tako mali predmet, „visoka“ je blizu lažnih uzbuna. Na kraju svake štampe u dnevniku je red `job check summary: highest change X of limit` — po tome podešavati.
- Promena svetla (vrata, svetlo) > 25 nivoa sivog resetuje proveru umesto uzbune.
- Pokretanje preuzetih .exe sa Claude strane blokira sigurnosna zaštita — instalaciju pokreće Mirko dvoklikom na `instaliraj.bat`.
- Test bez štampača: `KN_TEST_GCODE=<fajl>`, `KN_TEST_VIDEO=<folder sa k1.mp4,k2.mp4>`, `KN_TG_API=<mock>`, printers u settings sa `"port"`.

## Izuzeci od RULES.md
- V2: folder na računaru je git klon MirkoRudic/kosnica-nadzor (od 24.11.2026); ažuriranje iz Podešavanja → Verzija. `static/kosnica.ico` drži ikonica pored sata, pa `git reset --hard` dok app radi ne prolazi (pull menja samo promenjene fajlove).
- P2: server je na 127.0.0.1, ali je strana dostupna i preko Cloudflare quick tunela (zaštita tajnim linkom iz Telegram grupe, ne Cloudflare Access — nema domena).
- Quick tunnel je Cloudflare-ova besplatna usluga bez garancije; ako ne radi, Telegram i dalje radi.
- **Slanje fajlova na računar:** `device_commit_files` ponekad pošalje staru verziju istog fajla kad se ista putanja u `outputs` koristi ponovo (20.11: app.js, index.html, nadzor.pyw ostali stari). Svaki put slati iz nove fascikle (`outputs/kn_<verzija>/`) i posle proveriti md5 vraćanjem fajla.
- Telegram: kad bot postane administrator, grupa može da pređe u supergrupu i dobije novi id (20.11: -5300237059 → -1004332687408). `telegram.py` prelazi sam (migrate_to_chat_id).
