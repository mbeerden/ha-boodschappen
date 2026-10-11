# Boodschappen voor Home Assistant

Een eenvoudige boodschappenlijst per winkel bovenop [Grocy](https://grocy.info), gemaakt voor gebruik in de winkel op je telefoon.

- **Lijst per winkel** (één per Grocy-boodschappenlijst), gesorteerd op looproute via de Grocy-productgroepen (`01 …`, `02 …`).
- **Afvinken boekt de aankoop** in de Grocy-voorraad; terugzetten maakt de boeking ongedaan.
- **Snel toevoegen** met gewone tekst (`2 komkommer`, `melk`), slim gekoppeld aan Grocy-producten.
- **Vaak gekocht**: je meest gekochte producten van de laatste 8 weken met één tik op de lijst; vastpinnen en verbergen kan.
- **Vaste verse boodschappen**: producten met een Grocy-gebruikersveld `wekelijks` (getal) komen met één actie op de lijst.
- **Tekorten**: producten onder de minimumvoorraad op de lijst van hun winkel.
- **Aanbiedingen** (inklapbaar onder de lijst; producten op je lijst die in de aanbieding zijn krijgen een label *actie*): elke ochtend worden de aanbiedingen van Lidl (via de integratie [ha-lidl](https://github.com/FaserF/ha-lidl), incl. Lidl Plus-coupons en volgende week) en Jumbo (jumbo.com) gekoppeld aan je vaste producten, met je normale prijs en een inslaadvoorstel op basis van verbruik en houdbaarheid. Koppelen gebeurt met een AI-taak (`ai_task`, bijv. Google Gemini).
- **Bonnetjes**: actie `boodschappen.bon_verwerken` zet de betaalde prijs op afgevinkte aankopen, boekt alleen ongeplande aankopen en zet onbekende bonregels klaar om te koppelen op de pagina *Boodschappen → Bonnetjes*. Koppelingen (inclusief "stuks per bonregel") worden onthouden.

- **Eten** (aparte pagina `/eten`): recepten, weekmenu en beoordelingen, zonder losse receptenapp.
  - *Recepten* toevoegen via een link (schema.org-recept of paginatekst), geplakte tekst of zelf invullen; de AI-taak zet het om en koppelt de ingrediënten aan je Grocy-producten.
  - *Weekmenu*: de AI-taak doet een voorstel op basis van je huisregels, de recepten, wat recent gegeten is en de beoordelingen per persoon. Na goedkeuren komen de maaltijden in een agenda naar keuze (bijv. een gedeelde Google-agenda) en de ontbrekende ingrediënten op de boodschappenlijst van de juiste winkel.
  - *Aanpassen met de AI*: per dag (in het voorstel of al gepland) een wens opgeven, bijv. "een ovenschotel". De AI kiest een passend recept of schrijft een nieuw recept, dat pas in je bibliotheek komt als je het voorstel goedkeurt of de suggestie toepast.
  - *Geen maaltijd*: markeer een dag waarop jullie niet koken; komt zo in de agenda, zonder boodschappen.
  - De agenda blijft bruikbaar op je telefoon: verschuif of verwijder je daar een maaltijd, dan neemt de planning dat over. Events zonder kenmerk (zelf ingevoerd) zie je ook en kun je aan een recept koppelen.
  - *Koken*: stap-voor-stap weergave voor een tablet in de keuken (scherm blijft aan), porties schalen.
  - *Beoordelen*: per persoon een score na de maaltijd; telt mee in volgende voorstellen.

Daarnaast maakt de integratie `todo`-entiteiten per winkel, zodat de lijsten ook werken in de standaard To-do-weergave en met Assist ("zet melk op Boodschappen Lidl").

## Installatie

1. HACS → Integraties → menu → *Aangepaste repositories* → deze repository toevoegen als *Integratie*.
2. *Boodschappen* installeren en Home Assistant herstarten.
3. Instellingen → Apparaten & diensten → *Integratie toevoegen* → **Boodschappen**.
   Is de [Grocy-integratie](https://github.com/custom-components/grocy) al ingesteld, dan wordt die verbinding hergebruikt; anders vul je Grocy-URL en API-sleutel in.

De pagina staat daarna in de zijbalk als **Boodschappen** (`/boodschappen`).

## Grocy voorbereiden

- Geef producten een **standaardwinkel** met dezelfde naam als de boodschappenlijst (bijv. winkel *Lidl* ↔ lijst *Lidl*).
- Gebruik **productgroepen** met een volgnummer voor de looproute, bijv. `01 Groente & fruit`, `02 Brood`.
- Optioneel: maak een gebruikersveld `wekelijks` (geheel getal) op producten voor vaste wekelijkse aankopen.

## Bonnetjes verwerken

De actie verwacht de regels van een bon, bijvoorbeeld uitgelezen door een AI-taak:

```yaml
action: boodschappen.bon_verwerken
data:
  winkel: Lidl
  datum: "2026-10-03"
  bestand: /media/bonnetjes/lidl/bon.jpg   # voorkomt dubbel verwerken
  regels:
    - bon: "SCHARRELEIEREN 6ST"
      product: "Eieren"
      grocy: "Eieren"      # optioneel: exacte Grocy-productnaam
      aantal: 2
      totaal: 3.78
```

## Op een dashboard

Voeg de resource `/boodschappen_static/card.js` (type *JavaScript module*) toe; daarna zijn er twee kaarten,
handig voor een wandtablet (bijv. in een weergave van het type *paneel*):

```yaml
type: custom:eten-card          # of custom:boodschappen-card
show_header: false              # standaard; true toont de titelbalk
```

## Eten instellen

Open *Eten → ⚙* en kies de agenda, het tijdstip van de maaltijd, de personen die beoordelen en je huisregels
(vrije tekst voor het voorstel, bijv. wie op welke dag kookt, wie mee-eet, vriezerdagen, dieetwensen).

## Ontwikkelen

```bash
pip install homeassistant pytest
pytest tests/
python3 tests/eten_smoke.py   # echte HA-core met Local Calendar en een nep-AI-taak
```

## Licentie

MIT
