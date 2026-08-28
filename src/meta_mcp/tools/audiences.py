"""Outils audiences.

Deux familles distinctes :

1. **Audience organique** — qui sont tes abonnés (pays, ville, âge/genre). Ne
   nécessite qu'un token en lecture, aucun compte publicitaire.
2. **Audiences personnalisées** — ciblage publicitaire (Custom & Lookalike
   Audiences). Nécessite un compte publicitaire et la permission
   `ads_management`.

Confidentialité : les données personnelles envoyées à une audience personnalisée
sont normalisées puis **hachées en SHA-256 localement**. Meta ne reçoit jamais
d'email ni de téléphone en clair. Rappel RGPD : constituer une audience à partir
de données clients suppose une base légale et une information des personnes.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import (
    AD_ACCOUNT_FIELDS,
    AUDIENCE_FIELDS,
    AUDIENCE_SCHEMAS,
    FB_FANS_BREAKDOWNS,
)

JSONDict = dict[str, Any]

_NON_DIGITS = re.compile(r"\D")


def normalize_and_hash(value: str, schema: str) -> str:
    """Normalise une donnée selon les règles Meta puis la hache en SHA-256.

    - EMAIL : minuscules, espaces retirés
    - PHONE : chiffres uniquement (indicatif pays inclus)
    - FN / LN : minuscules, espaces retirés
    """
    cleaned = value.strip().lower()
    cleaned = (
        _NON_DIGITS.sub("", cleaned) if schema == "PHONE" else cleaned.replace(" ", "")
    )
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils audiences sur le serveur MCP."""

    # ── Audience organique (pas de compte publicitaire requis) ───────────────
    @mcp.tool()
    async def get_page_audience(page_id: str, breakdown: str = "country") -> JSONDict:
        """Profil des abonnés d'une Page Facebook.

        `breakdown` : country, city, age_gender ou locale.
        Renvoie le nombre d'abonnés par segment, trié du plus grand au plus petit.
        """
        if breakdown not in FB_FANS_BREAKDOWNS:
            return {"error": f"breakdown doit être parmi {list(FB_FANS_BREAKDOWNS)}"}
        metric = FB_FANS_BREAKDOWNS[breakdown]
        token = await client.page_token(page_id)
        data = await client.get(
            f"{page_id}/insights",
            {"metric": metric, "period": "lifetime"},
            token=token,
        )
        entries = data.get("data", [])
        if not entries:
            return {"breakdown": breakdown, "segments": {},
                    "note": "aucune donnée (audience trop petite pour être agrégée)"}
        values = (entries[0].get("values") or [{}])[-1].get("value") or {}
        if not isinstance(values, dict):
            return {"breakdown": breakdown, "raw": values}
        ordered = dict(
            sorted(values.items(), key=lambda kv: kv[1], reverse=True)
        )
        return {
            "breakdown": breakdown,
            "total": sum(ordered.values()),
            "segments": ordered,
        }

    @mcp.tool()
    async def audience_overview(page_id: str) -> JSONDict:
        """Vue d'ensemble de l'audience : top pays, villes et tranches d'âge.

        Combine plusieurs répartitions en un seul appel. Utile pour savoir à qui
        s'adressent réellement tes publications.
        """
        token = await client.page_token(page_id)
        overview: JSONDict = {"page_id": page_id}
        for label, metric in FB_FANS_BREAKDOWNS.items():
            try:
                data = await client.get(
                    f"{page_id}/insights",
                    {"metric": metric, "period": "lifetime"},
                    token=token,
                )
            except Exception as exc:  # noqa: BLE001 - métrique indisponible
                overview[label] = {"error": str(exc)}
                continue
            entries = data.get("data", [])
            values = (
                (entries[0].get("values") or [{}])[-1].get("value") if entries else {}
            )
            if isinstance(values, dict) and values:
                top = sorted(values.items(), key=lambda kv: kv[1], reverse=True)[:5]
                overview[label] = dict(top)
            else:
                overview[label] = {}
        return overview

    # ── Comptes publicitaires ───────────────────────────────────────────────
    @mcp.tool()
    async def list_ad_accounts() -> JSONDict:
        """Liste les comptes publicitaires accessibles (nécessaire aux audiences)."""
        accounts = await client.paginate(
            "me/adaccounts", {"fields": AD_ACCOUNT_FIELDS}
        )
        return {"count": len(accounts), "ad_accounts": accounts}

    # ── Audiences personnalisées (lecture) ──────────────────────────────────
    @mcp.tool()
    async def list_custom_audiences(ad_account_id: str, limit: int = 50) -> JSONDict:
        """Liste les audiences personnalisées d'un compte publicitaire.

        `ad_account_id` au format `act_123456789`.
        """
        audiences = await client.paginate(
            f"{ad_account_id}/customaudiences",
            {"fields": AUDIENCE_FIELDS, "limit": min(limit, 100)},
        )
        return {"count": len(audiences), "audiences": audiences[:limit]}

    @mcp.tool()
    async def get_custom_audience(audience_id: str) -> JSONDict:
        """Détaille une audience personnalisée (taille estimée, statut, source)."""
        return await client.get(audience_id, {"fields": AUDIENCE_FIELDS})

    # ── Audiences personnalisées (écriture) ─────────────────────────────────
    @mcp.tool()
    async def create_custom_audience(
        ad_account_id: str,
        name: str,
        description: str | None = None,
        subtype: str = "CUSTOM",
        customer_file_source: str = "USER_PROVIDED_ONLY",
    ) -> JSONDict:
        """Crée une audience personnalisée vide, prête à être alimentée.

        `subtype` : CUSTOM (liste de clients) ou ENGAGEMENT (interactions).
        """
        client.require_writes("create_custom_audience")
        return await client.post(
            f"{ad_account_id}/customaudiences",
            {
                "name": name,
                "description": description,
                "subtype": subtype,
                "customer_file_source": customer_file_source,
            },
        )

    @mcp.tool()
    async def add_users_to_audience(
        audience_id: str, schema: str, values: list[str]
    ) -> JSONDict:
        """Ajoute des personnes à une audience personnalisée.

        `schema` : EMAIL, PHONE, FN (prénom) ou LN (nom).
        Les valeurs sont normalisées et hachées en SHA-256 **localement** : Meta ne
        reçoit jamais de donnée en clair.

        RGPD : n'utilise que des données pour lesquelles tu as une base légale et
        dont les personnes ont été informées.
        """
        client.require_writes("add_users_to_audience")
        if schema not in AUDIENCE_SCHEMAS:
            return {"error": f"schema doit être parmi {list(AUDIENCE_SCHEMAS)}"}
        if not values:
            return {"error": "aucune valeur fournie"}
        hashed = [[normalize_and_hash(v, schema)] for v in values if v.strip()]
        payload = {"schema": [schema], "data": hashed}
        result = await client.post(
            f"{audience_id}/users",
            {"payload": json.dumps(payload)},
        )
        return {
            "result": result,
            "sent": len(hashed),
            "hashing": "SHA-256 local, aucune donnée en clair transmise",
        }

    @mcp.tool()
    async def remove_users_from_audience(
        audience_id: str, schema: str, values: list[str]
    ) -> JSONDict:
        """Retire des personnes d'une audience personnalisée (droit d'opposition).

        Mêmes règles de hachage que `add_users_to_audience`.
        """
        client.require_writes("remove_users_from_audience")
        if schema not in AUDIENCE_SCHEMAS:
            return {"error": f"schema doit être parmi {list(AUDIENCE_SCHEMAS)}"}
        hashed = [[normalize_and_hash(v, schema)] for v in values if v.strip()]
        payload = {"schema": [schema], "data": hashed}
        result = await client.delete(
            f"{audience_id}/users",
            {"payload": json.dumps(payload)},
        )
        return {"result": result, "removed": len(hashed)}

    @mcp.tool()
    async def create_lookalike_audience(
        ad_account_id: str,
        name: str,
        origin_audience_id: str,
        country: str = "FR",
        ratio: float = 0.01,
    ) -> JSONDict:
        """Crée une audience similaire à partir d'une audience existante.

        `ratio` : 0.01 = 1 % de la population du pays (le plus proche de l'origine),
        jusqu'à 0.10 pour une audience plus large et moins précise.
        """
        client.require_writes("create_lookalike_audience")
        spec = {"type": "custom_ratio", "ratio": ratio, "country": country}
        return await client.post(
            f"{ad_account_id}/customaudiences",
            {
                "name": name,
                "subtype": "LOOKALIKE",
                "origin_audience_id": origin_audience_id,
                "lookalike_spec": json.dumps(spec),
            },
        )

    @mcp.tool()
    async def delete_custom_audience(audience_id: str) -> JSONDict:
        """Supprime une audience personnalisée. Action irréversible."""
        client.require_writes("delete_custom_audience")
        return await client.delete(audience_id)
