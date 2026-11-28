"""Gemini direct aanroepen met streaming, zodat je in het scherm kunt meekijken terwijl Gemini nadenkt.

De AI-taak van Home Assistant (ai_task.generate_data) geeft pas antwoord als alles klaar is. Voor het meekijkvenster
gebruiken we daarom de Gemini-API zelf, met de API-sleutel en het model uit de integratie 'Google Generative AI' die
al in Home Assistant staat. Is die er niet, of lukt het niet, dan valt de aanroeper terug op de AI-taak.

Gemini stuurt tijdens het denken samenvattingen van zijn gedachten mee (includeThoughts). Dat zijn samenvattingen, geen
letterlijke denkstappen.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

GOOGLE_DOMEIN = "google_generative_ai_conversation"
STANDAARD_MODEL = "gemini-2.5-flash"
MAX_TOKENS = 32768  # ruim: een vertaald recept met koppelingen past niet in de standaard van de Google-integratie
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"


class GeminiNietBeschikbaar(Exception):
    """Geen sleutel of geen verbinding: gebruik de AI-taak."""


def model_naam(model: Any) -> str:
    m = str(model or "").strip()
    if m.startswith("models/"):
        m = m[len("models/"):]
    return m or STANDAARD_MODEL


def sleutel_en_model(hass) -> tuple[str, str] | None:
    """API-sleutel en model uit de Google Generative AI-integratie (het model van de AI-taak als dat er is)."""
    for entry in hass.config_entries.async_entries(GOOGLE_DOMEIN):
        key = (entry.data or {}).get("api_key")
        if not key:
            continue
        model = None
        subs = getattr(entry, "subentries", None) or {}
        for sub in subs.values():
            if getattr(sub, "subentry_type", None) == "ai_task_data":
                model = (sub.data or {}).get("chat_model") or model
        if not model:
            for sub in subs.values():
                model = model or (sub.data or {}).get("chat_model")
        return key, model_naam(model or (entry.options or {}).get("chat_model"))
    return None


def delen_uit_chunk(obj: dict) -> list[tuple[bool, str]]:
    """[(is_gedachte, tekst)] uit één SSE-bericht van Gemini."""
    uit: list[tuple[bool, str]] = []
    for cand in obj.get("candidates") or []:
        for part in ((cand.get("content") or {}).get("parts") or []):
            t = part.get("text")
            if t:
                uit.append((bool(part.get("thought")), t))
    return uit


async def stream(
    session: aiohttp.ClientSession,
    key: str,
    model: str,
    prompt: str,
    op_gedachte: Callable[[str], Awaitable[None] | None],
    json_antwoord: bool = True,
    timeout: float = 90,
    gedachten: bool = True,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """Vraag Gemini met streaming. Gedachten gaan naar op_gedachte; het antwoord (tekst) komt terug."""
    async def vraag(met_gedachten: bool) -> str:
        cfg: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if json_antwoord:
            cfg["responseMimeType"] = "application/json"
        if met_gedachten:
            cfg["thinkingConfig"] = {"includeThoughts": True}
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
        tekst: list[str] = []
        async with session.post(
            API.format(model=model), json=body, headers={"x-goog-api-key": key},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 400 and met_gedachten:
                raise _GeenGedachten(await resp.text())
            if resp.status >= 400:
                fout = (await resp.text())[:300]
                raise GeminiNietBeschikbaar(f"Gemini gaf HTTP {resp.status}: {fout}")
            buffer = b""
            async for chunk in resp.content.iter_any():
                buffer += chunk
                while b"\n" in buffer:
                    regel, buffer = buffer.split(b"\n", 1)
                    regel = regel.strip()
                    if not regel.startswith(b"data:"):
                        continue
                    try:
                        obj = json.loads(regel[5:].strip())
                    except ValueError:
                        continue
                    for is_gedachte, t in delen_uit_chunk(obj):
                        if is_gedachte:
                            r = op_gedachte(t)
                            if r is not None:
                                await r
                        else:
                            tekst.append(t)
        return "".join(tekst)

    try:
        try:
            return await vraag(gedachten)
        except _GeenGedachten as err:
            # model zonder denkstand (of andere instelling): dan zonder gedachten
            _LOGGER.debug("Gemini zonder gedachten: %s", err)
            return await vraag(False)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise GeminiNietBeschikbaar(f"Gemini niet bereikbaar: {err}") from err


class _GeenGedachten(Exception):
    pass
