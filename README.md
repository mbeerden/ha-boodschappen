# Boodschappen voor Home Assistant

Een eenvoudige boodschappenlijst per winkel bovenop [Grocy](https://grocy.info), gemaakt voor gebruik in de winkel op je telefoon.

- **Lijst per winkel** (één per Grocy-boodschappenlijst), gesorteerd op looproute via de Grocy-productgroepen (`01 …`, `02 …`).
- **Afvinken boekt de aankoop** in de Grocy-voorraad; terugzetten maakt de boeking ongedaan.
- **Snel toevoegen** met gewone tekst (`2 komkommer`, `melk`), slim gekoppeld aan Grocy-producten.
- **Vaak gekocht**: je meest gekochte producten van de laatste 8 weken met één tik op de lijst; vastpinnen en verbergen kan.
- **Vaste verse boodschappen**: producten met een Grocy-gebruikersveld `wekelijks` (getal) komen met één actie op de lijst.
- **Tekorten**: producten onder de minimumvoorraad op de lijst van hun winkel.
- **Bonnetjes**: actie `boodschappen.bon_verwerken` zet de betaalde prijs op afgevinkte aankopen, boekt alleen ongeplande aankopen en zet onbekende bonregels klaar om te koppelen op de pagina *Boodschappen → Bonnetjes*. Koppelingen (inclusief "stuks per bonregel") worden onthouden.

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

## Ontwikkelen

```bash
pip install homeassistant pytest
pytest tests/test_boodschappen.py
```

## Licentie

MIT
