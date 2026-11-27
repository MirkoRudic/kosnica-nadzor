# Košnica Nadzor za druge radionice — plan (24.11.2026)

Premešteno iz projektnog dokumenta `kosnica-nadzor-za-druge.md` 27.11.2026.

Cilj: Nadzor radi kod bilo koje radionice, na različitim štampačima sa kamerom, potpuno lokalno i nezavisno (svaka radionica svoj bot, svoji podaci, ništa ne prolazi kroz Mirka).

## Koraci

1. **Stabilno kod nas (sada → 2–3 nedelje).** Skupljati lažne i promašene uzbune na K1/K2, podešavati detekciju. Kalibracija K1 pogleda na prvom pogodnom projektu. Bez ovoga nema smisla deliti app.
2. **Automatska kalibracija kamere.** Iz G-code-a pravog posla (gde je prvi sloj) uparuje sliku sa pločom — bez klikova. Rezervno: kalibracioni predmet (4–6 tankih oznaka na poznatim mestima) kao 3MF koji korisnik otvori u svom slajseru.
3. **Razdvojiti „prevodioce“ za štampače.** Jezgro (detekcija, Telegram, strana) ne zna koji je štampač; svaki tip ima svoj mali modul: stanje, sloj, filament, pauza/nastavak/stop, adresa kamere, G-code posla.
   - Creality K1 (websocket 9999) — imamo
   - Klipper/Moonraker (K2, Elegoo Neptune 4, Sovol, Voron…) — pola imamo
   - Elegoo SDCP (Centauri Carbon)
   - Bambu Lab (MQTT u LAN režimu)
   - PrusaLink, OctoPrint
4. **Test bez štampača.** Simulator za Klipper (virtuelni štampač) + snimljeni video kamere (KN_TEST_VIDEO već postoji). Za Bambu/Elegoo/Prusa: snimci statusa sa pravih štampača kad nađemo vlasnike.
5. **Čarobnjak pri prvom pokretanju.** Traži štampače u mreži i na svakoj adresi proba sve prevodioce; ručni IP kao rezerva. Vodi kroz pravljenje sopstvenog Telegram bota (BotFather). Kalibracija (korak 2). Prvo samo Windows.
6. **Instalacija i ažuriranje.** Jedan instalacioni fajl (bez Pythona kod korisnika), ažuriranje iz app (kao sada, V2/V3).
7. **Pristup sa telefona.** Za nas: domen + Cloudflare imenovani tunel → stalan link i prijava kodom na mejl. Za druge: opciono (Telegram je dovoljan); kasnije podadresa na Mirkovom domenu po radionici, proveriti granice besplatnog Cloudflare naloga.
8. **Beta sa 5–10 radionica.** Dugme „Pošalji izveštaj“ (dnevnik + opis štampača, bez tokena). Tek posle toga šire.

## Otvorena pitanja
- Strani predmet pored projekta: samo poruka (Mirko 24.11) ili ponovo pauza.
- Jedan zajednički bot ima smisla samo uz plaćenu uslugu (traži server).
- Bambu: kamera u LAN režimu se razlikuje po modelu (RTSP na X1/H2/P2S, JPEG na A1/P1) i firmver je menjao pristup (pretpostavka, nije provereno na štampaču).
- Prusa Core One: kamera ide kroz Prusa Connect; lokalni pristup nije zvanično podržan (pretpostavka, nije provereno).
