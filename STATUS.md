# Košnica Nadzor — stanje (26.11.2026)

Verzija **0.9.2**. Računar u radionici `desktop-fk7e7jt`, `C:\Users\zzroz\Desktop\VAJB\kosnica-nadzor`, port 8800, git klon ovog repoa.
Nastavak u novom četu: pročitaj `AGENTS.md`, `SPECIFICATION.md`, ovaj fajl; pravila `vajb-osnova/RULES.md`.

## Poslednje urađeno
- 0.9.0: slika se poravnava sa spuštanjem ploče pre poređenja (nema lažnog stranog predmeta zbog oznaka na ploči). Važi za K1 i K2, bez kalibracije.
- 0.9.1: javljena promena postaje novo normalno (nema ponavljanja na 30 min).
- 0.9.2: početak štampe uvek ide na Telegram; 0.8.10: i nastavak.
- 0.8.6: strožija provera pre pauze (2 min, 4 provere, prag ne ispod minimuma, vrh predmeta koji raste se ne broji).
- 0.8.5: format Telegram poruka po Mirku (24.11).

## Odluke
- Lažna pauza je najgora greška (24.11, posle pauze 10-satne štampe zbog senke).
- Strani predmet pored projekta: samo poruka, bez pauze (24.11).
- Bez poznatog mesta projekta (K1) nema automatske pauze.
- `materialStatus == 1` = nema filamenta (izmereno na K1).

## Otvoreno
- Da li vratiti pauzu za strani predmet.
- Kalibracija pogleda K1 — čeka projekat sa više predmeta po ploči, visokih ≥ 2 cm.
- Greške štampača sa opisom i linkom na Creality wiki (predlog dat, čeka „da“).
- Edge mini meni: `ugasi_mini_meni.vbs` u VAJB folderu traži administratora — Mirko pokreće sam.
- Auto-login posle nestanka struje: lozinka Microsoft naloga ne prolazi.
- Nadzor za druge radionice: plan u `docs/OTHER_WORKSHOPS_PLAN.md` (korak 1: stabilno kod nas).

## Nije provereno
- Ispravka 0.9.0 na pravim štampama (provereno na veštačkom snimku).
- Nastavak/stop komandom na K2.
