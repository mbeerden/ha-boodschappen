"""Constanten voor de Boodschappen-integratie."""

DOMAIN = "boodschappen"

CONF_URL = "url"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_VERIFY_SSL = "verify_ssl"
DEFAULT_PORT = 9192

SCAN_INTERVAL_SECONDS = 30

# Grocy-userfield op producten met de vaste weekhoeveelheid
USERFIELD_WEKELIJKS = "wekelijks"

NOTE_WEEKVAST = "weekvast"
NOTE_TEKORT = "voorraad laag"

SERVICE_WEEKVAST = "weekvast_toevoegen"
SERVICE_TEKORTEN = "tekorten_toevoegen"
