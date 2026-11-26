# Košnica Nadzor — specifikacija

Šta app radi iz ugla korisnika. Svaka izmena ponašanja prvo se upiše ovde, pa u kod.
Oblik: **kad se desi X → app uradi Y**. Tehnika i zamke su u `CLAUDE.md`, gde smo stali u `STANJE.md`.

## Svrha
Prati 3D štampu na **K1 Max** i **K2 Plus** u radionici i javlja na Telegram (grupa radionice) kad nešto krene naopako, pre svega kad se predmet odlepi od ploče i napravi zbrku. Radi lokalno na računaru u radionici, bez naloga i bez oblaka štampača.

## Najgora greška
**Lažna pauza** (Mirko 24.11): pauzirati ispravnu štampu od 10 sati je gore od propuštene uzbune. Zato pauza tek kad je promena sigurna (vidi 3).

## 1. Pokretanje
- Računar se upali i prijavi → Nadzor se pokrene sam, bez prozora (`--tiho`), samo ikonica pored sata.
- Ikonica → Otvori: strana Nadzora u sopstvenom prozoru (Edge bez adresne trake). Ugasi: gasi app (vidi se samo na računaru u radionici).
- Telefon: link iz Telegram grupe (`/strana`) otvara istu stranu preko tunela; bez linka sa ključem strana se ne otvara.

## 2. Telegram poruke
Svaka poruka: prvi red `Štampač: posao · sloj X/Y · N% · još T`, drugi red šta se desilo, ispod slika i dugmad.

| Kad | Poruka | Dugmad |
|---|---|---|
| Štampa počne | „počela štampa“ (uvek, od 0.9.2) | — |
| Štampa se završi | „štampa završena za …“ | Obriši poruke |
| Prekid (sa štampača ili greškom) | „štampa prekinuta“ | Obriši poruke |
| Štampač sam pauzira / nastavi | „štampač pauzirao štampu“ / „štampa nastavljena“ | Nastavi · Zaustavi |
| Nestane filamenta | „nema filamenta“ | Nastavi · Zaustavi |
| Štampač javi grešku | „štampač javlja grešku {kod} ({opis})“ | Pauziraj · Zaustavi |
| Odlepljivanje ili špagete | „moguće odlepljivanje ili špagete, Košnica pauzira štampu.“ | Nastavi · Zaustavi |
| Strani predmet pored projekta | „strani predmet u projektu, proveri!“ (bez pauze, Mirko 24.11) | Pauziraj · Zaustavi |
| Promena a mesto projekta nije poznato (K1) | „promena u slici, proveri!“ (bez pauze) | Pauziraj · Zaustavi |
| Temperatura dizne odstupa | „temperatura dizne 180° umesto 210°“ | Pauziraj · Zaustavi |
| Glava stoji 10 min | „glava se ne pomera, štampa stoji već 10 minuta“ | Pauziraj · Zaustavi |
| Štampač nestane 2 min | „štampač se ne javlja više od 2 minuta“ | — |
| Kamera ne daje sliku | „kamera se ne odaziva“ | — |
| Komanda ne prođe | „prethodna komanda nije prošla, proveri!“ | — |

- Pauza i greška u istom trenutku → jedna poruka.
- Javljena promena postaje novo normalno → ista stvar se ne javlja ponovo.
- „Obriši poruke“ → briše sve poruke o tom poslu iz grupe, i samu sebe (Telegram dozvoljava do 48 h).
- Komande: `/slika` (slika samo štampača koji štampaju; ostali jedan red), `/status` (posao, temperature, „stanje redovno“), `/strana` („Strana nadzora:“ + link).
- Zaustavi traži potvrdu. Posle komande app čeka da štampač promeni stanje; ako ne promeni → poruka da nije prošla.

## 3. Provera slike
- Prva 3 sloja se ne proverava; ploča koja se spušta se poravnava pre poređenja.
- Uzbuna tek kad promena ostane na istom mestu 2 min (4 provere) i pređe prag koji se uči na početku svake štampe.
- Promena na vrhu predmeta koji upravo raste se ne broji ako nije velika. Mesto koje je skoro izgledalo kao ranije (glava) se ne broji.
- Automatska pauza samo kad je poznato gde je projekat (G-code / 3MF i pogled kamere). K1 bez izmerenog pogleda → samo poruka.
- Osetljivost: Niska / Srednja / Visoka (Podešavanja).

## 4. Strana
- Po štampaču: uživo slika, Projekat (do trenutnog sloja: urađeno žuto, trenutni belo, ostalo sivo), sloj, dizna, ploča, do kraja; Pauziraj, Zaustavi.
- Podešavanja: Tema, provera slike (uključeno, osetljivost), Telegram (grupa, javi početak/kraj, probna poruka, odveži grupu), pristup spolja (stanje, Novi link), Dnevnik, Verzija (ažuriranje), Deinstaliraj.
- Arhiva slika događaja (poslednjih 300).

## Namerno ne radi
- Ne šalje ništa preko Creality oblaka i ne traži root na štampačima.
- Ne pauzira zbog stranog predmeta (samo javlja) — otvoreno da li vratiti pauzu.

## Nije provereno na pravom štampaču
- Nastavak i zaustavljanje komandom na K2 (pauza na K1 radi).
- Pogled kamere K2 (kalibracija); K1 pogled čeka pogodan projekat.
- Slika projekta sa štampača kao poslednja rezerva.
