"""Serveur MCP pour Metabase.

Expose des outils pour explorer et interroger une instance Metabase
(questions/cards, dashboards, bases de données, recherche, SQL natif).

Auth : clé API Metabase (Settings > Authentication > API keys, v0.49+),
envoyée via le header `X-API-Key`.

Config via variables d'environnement (ou fichier .env à côté de ce script) :
  - METABASE_URL      ex: https://metabase.konbini.com  (sans / final)
  - METABASE_API_KEY  la clé API Metabase
  - METABASE_MAX_ROWS (optionnel) plafond de lignes renvoyées, défaut 2000
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


def _load_dotenv() -> None:
    """Charge un .env minimal placé à côté du script, sans dépendance externe."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Ne pas écraser une variable déjà définie dans l'environnement réel.
        os.environ.setdefault(key, value)


_load_dotenv()

METABASE_URL = os.environ.get("METABASE_URL", "").rstrip("/")
METABASE_API_KEY = os.environ.get("METABASE_API_KEY", "")
DEFAULT_MAX_ROWS = int(os.environ.get("METABASE_MAX_ROWS", "2000"))

mcp = FastMCP("metabase")


def _client() -> httpx.Client:
    if not METABASE_URL or not METABASE_API_KEY:
        raise RuntimeError(
            "Configuration manquante : définis METABASE_URL et METABASE_API_KEY "
            "(dans metabase-mcp/.env ou l'environnement)."
        )
    return httpx.Client(
        base_url=METABASE_URL,
        headers={
            "X-API-Key": METABASE_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=60.0,
    )


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


def _post(path: str, json: dict[str, Any] | None = None) -> Any:
    with _client() as client:
        resp = client.post(path, json=json or {})
        resp.raise_for_status()
        return resp.json()


def _put(path: str, json: dict[str, Any] | None = None) -> Any:
    with _client() as client:
        resp = client.put(path, json=json or {})
        resp.raise_for_status()
        return resp.json()


def _format_rows(payload: Any, max_rows: int) -> dict[str, Any]:
    """Normalise la réponse de query Metabase en {columns, rows, row_count, truncated}."""
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    cols = [c.get("display_name") or c.get("name") for c in data.get("cols", [])]
    rows = data.get("rows", [])
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    result: dict[str, Any] = {
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
    if isinstance(data, dict) and data.get("error"):
        result["error"] = data["error"]
    return result


# --------------------------------------------------------------------------- #
# Exploration
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_databases() -> list[dict[str, Any]]:
    """Liste les bases de données connectées à Metabase (id, name, engine)."""
    payload = _get("/api/database")
    dbs = payload.get("data", payload) if isinstance(payload, dict) else payload
    return [
        {"id": d["id"], "name": d["name"], "engine": d.get("engine")}
        for d in dbs
    ]


@mcp.tool()
def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recherche transverse (questions, dashboards, collections, tables) par mot-clé."""
    payload = _get("/api/search", params={"q": query})
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    out = []
    for it in items[:limit]:
        out.append(
            {
                "id": it.get("id"),
                "name": it.get("name"),
                "model": it.get("model"),  # card, dashboard, collection, table...
                "collection": (it.get("collection") or {}).get("name"),
                "description": it.get("description"),
            }
        )
    return out


@mcp.tool()
def list_cards(limit: int = 50) -> list[dict[str, Any]]:
    """Liste les questions (cards) Metabase. Pour cibler, préfère `search`."""
    cards = _get("/api/card")
    out = []
    for c in cards[:limit]:
        out.append(
            {
                "id": c["id"],
                "name": c["name"],
                "collection": (c.get("collection") or {}).get("name"),
                "database_id": c.get("database_id"),
                "description": c.get("description"),
            }
        )
    return out


def _extract_native_sql(dataset_query: dict[str, Any]) -> str | None:
    """Extrait le SQL natif d'un dataset_query, ancien ET nouveau format.

    Metabase <=62 : {"type": "native", "native": {"query": "..."}}.
    Metabase >=63 (query builder v2) : {"stages": [{"lib/type":
    "mbql.stage/native", "native": "..."}, ...]} — le SQL est une string
    brute (pas un sous-objet), en général sur le dernier stage natif.
    """
    native_obj = dataset_query.get("native")
    if isinstance(native_obj, dict) and native_obj.get("query"):
        return native_obj["query"]

    stages = dataset_query.get("stages") or []
    for stage in reversed(stages):
        if stage.get("lib/type") == "mbql.stage/native":
            native = stage.get("native")
            if isinstance(native, str):
                return native
            if isinstance(native, dict):
                return native.get("query")
    return None


def _query_type(card: dict[str, Any]) -> str | None:
    """query_type est exposé au niveau racine de la card depuis la v63 ;
    fallback sur dataset_query.type pour les instances plus anciennes."""
    return card.get("query_type") or (card.get("dataset_query") or {}).get("type")


def _extract_template_tags(dataset_query: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrait les template-tags (variables `{{...}}` du SQL natif), ancien
    ET nouveau format. Ne pas confondre avec `card["parameters"]`, qui décrit
    les filtres exposés au niveau dashboard, pas les tags bruts du SQL."""
    native_obj = dataset_query.get("native")
    if isinstance(native_obj, dict) and native_obj.get("template-tags"):
        tags = native_obj["template-tags"]
        return list(tags.values()) if isinstance(tags, dict) else tags

    for stage in dataset_query.get("stages") or []:
        if stage.get("lib/type") == "mbql.stage/native" and stage.get("template-tags"):
            return stage["template-tags"]
    return []


@mcp.tool()
def get_card(card_id: int) -> dict[str, Any]:
    """Détail d'une question : nom, description, base, SQL/définition, paramètres."""
    c = _get(f"/api/card/{card_id}")
    query = c.get("dataset_query", {})
    return {
        "id": c["id"],
        "name": c["name"],
        "description": c.get("description"),
        "database_id": c.get("database_id"),
        "collection": (c.get("collection") or {}).get("name"),
        "query_type": _query_type(c),
        "native_sql": _extract_native_sql(query),
        "parameters": c.get("parameters", []),
        "template_tags": _extract_template_tags(query),
    }


@mcp.tool()
def list_dashboards(limit: int = 50) -> list[dict[str, Any]]:
    """Liste les dashboards Metabase (id, name, collection)."""
    dashboards = _get("/api/dashboard")
    out = []
    for d in dashboards[:limit]:
        out.append(
            {
                "id": d["id"],
                "name": d["name"],
                "collection": (d.get("collection") or {}).get("name"),
                "description": d.get("description"),
            }
        )
    return out


@mcp.tool()
def get_dashboard(dashboard_id: int) -> dict[str, Any]:
    """Détail d'un dashboard et la liste des cards qu'il contient."""
    d = _get(f"/api/dashboard/{dashboard_id}")
    cards = []
    for dc in d.get("dashcards", d.get("ordered_cards", [])):
        card = dc.get("card") or {}
        if card.get("id"):
            cards.append({"card_id": card["id"], "name": card.get("name")})
    return {
        "id": d["id"],
        "name": d["name"],
        "description": d.get("description"),
        "cards": cards,
    }


# --------------------------------------------------------------------------- #
# Exécution
# --------------------------------------------------------------------------- #

@mcp.tool()
def run_card(
    card_id: int,
    max_rows: int = DEFAULT_MAX_ROWS,
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Exécute une question Metabase existante et renvoie ses lignes.

    Renvoie {columns, rows, row_count, truncated}. C'est la voie sûre :
    on rejoue une requête déjà validée plutôt que d'écrire du SQL.

    `parameters` (optionnel) permet de filtrer comme le ferait une URL de
    dashboard (ex. ?marque=Herstory&plateforme=Instagram) au lieu de subir
    les valeurs par défaut des template-tags. Regarder `get_card(card_id)
    ["template_tags"]` pour connaître les noms/types/défauts disponibles,
    puis fournir une liste du type :
      [{"type": "category", "target": ["variable", ["template-tag", "marque"]],
        "value": "Herstory"},
       {"type": "date/single", "target": ["variable", ["template-tag", "debut"]],
        "value": "2026-01-01"}]
    """
    body: dict[str, Any] = {}
    if parameters is not None:
        body["parameters"] = parameters
    payload = _post(f"/api/card/{card_id}/query", json=body)
    return _format_rows(payload, max_rows)


@mcp.tool()
def run_sql(
    database_id: int, sql: str, max_rows: int = DEFAULT_MAX_ROWS
) -> dict[str, Any]:
    """Exécute une requête SQL native (lecture) sur une base donnée via /api/dataset.

    `database_id` provient de `list_databases`. Réservé à des SELECT :
    Metabase applique les permissions de la clé API, mais évite tout DML.
    """
    body = {
        "database": database_id,
        "type": "native",
        "native": {"query": sql},
    }
    payload = _post("/api/dataset", json=body)
    return _format_rows(payload, max_rows)


# --------------------------------------------------------------------------- #
# Écriture (création de cards / dashboards)
# --------------------------------------------------------------------------- #

@mcp.tool()
def create_card(
    name: str,
    database_id: int,
    sql: str | None = None,
    dataset_query: dict[str, Any] | None = None,
    display: str = "table",
    template_tags: dict[str, Any] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    visualization_settings: dict[str, Any] | None = None,
    collection_id: int | None = None,
    description: str | None = None,
    card_type: str = "question",
) -> dict[str, Any]:
    """Crée une question (card) Metabase — SQL natif ou requête graphique (MBQL).

    Cas courant (SQL natif) : fournir `sql`. Peut contenir des field filters
    (ex. `[[AND {{format}}]]`) ; dans ce cas fournir `template_tags` pour les
    câbler à des colonnes, p.ex. :
      {"format": {"id": "<uuid>", "name": "format", "display-name": "Format",
                  "type": "dimension", "dimension": ["field", 91439, null],
                  "widget-type": "string/="}}

    Cas avancé (question graphique / MBQL, nécessaire pour un vrai Pivot Table
    natif — Metabase refuse "pivot" sur une question SQL native même agrégée) :
    fournir `dataset_query` directement, `sql` est alors ignoré. Exemple pour
    empiler une agrégation par jour/item sur une card existante (source-table
    "card__<id>") :
      {"database": 2, "type": "query",
       "query": {"source-table": "card__1690",
                  "aggregation": [["sum", ["field", "impressions", {"base-type": "type/Integer"}]]],
                  "breakout": [["field", "jour", {"base-type": "type/DateTime"}],
                               ["field", "item", {"base-type": "type/Text"}]]}}

    `card_type` : "question" (défaut) ou "model".
    `display` : table, pie, bar, row, line, scalar, gauge, pivot...
    Renvoie {id, name}.
    """
    body: dict[str, Any] = {
        "name": name,
        "display": display,
        "visualization_settings": visualization_settings or {},
        "collection_id": collection_id,
        "type": card_type,
    }
    if dataset_query is not None:
        body["dataset_query"] = dataset_query
    else:
        if sql is None:
            raise ValueError("Fournir `sql` (SQL natif) ou `dataset_query` (MBQL).")
        body["dataset_query"] = {
            "database": database_id,
            "type": "native",
            "native": {"query": sql, "template-tags": template_tags or {}},
        }
    if parameters is not None:
        body["parameters"] = parameters
    if description is not None:
        body["description"] = description
    c = _post("/api/card", json=body)
    return {"id": c["id"], "name": c["name"]}


@mcp.tool()
def update_card(
    card_id: int,
    name: str | None = None,
    sql: str | None = None,
    dataset_query: dict[str, Any] | None = None,
    database_id: int | None = None,
    display: str | None = None,
    template_tags: dict[str, Any] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    visualization_settings: dict[str, Any] | None = None,
    collection_id: int | None = None,
    description: str | None = None,
    card_type: str | None = None,
) -> dict[str, Any]:
    """Modifie une question existante (PUT partiel sur /api/card/{id}).

    Seuls les champs fournis sont modifiés, le reste de la card est inchangé.
    Pour changer `sql` (question SQL native), `database_id` doit aussi être
    fourni ; `template_tags` doit refléter les variables `{{...}}` du nouveau
    SQL. Pour une question graphique (MBQL), fournir `dataset_query`
    directement (voir `create_card`) — `sql` est alors ignoré.
    `card_type` : "question" ou "model".
    Renvoie {id, name}.
    """
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if display is not None:
        body["display"] = display
    if visualization_settings is not None:
        body["visualization_settings"] = visualization_settings
    if collection_id is not None:
        body["collection_id"] = collection_id
    if description is not None:
        body["description"] = description
    if card_type is not None:
        body["type"] = card_type
    if dataset_query is not None:
        body["dataset_query"] = dataset_query
    if parameters is not None:
        body["parameters"] = parameters
    if sql is not None:
        if database_id is None:
            raise ValueError(
                "database_id est requis quand `sql` est fourni "
                "(Metabase l'exige pour reconstruire dataset_query)."
            )
        body["dataset_query"] = {
            "database": database_id,
            "type": "native",
            "native": {"query": sql, "template-tags": template_tags or {}},
        }
    c = _put(f"/api/card/{card_id}", json=body)
    return {"id": c["id"], "name": c["name"]}


@mcp.tool()
def create_dashboard(
    name: str,
    description: str | None = None,
    collection_id: int | None = None,
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Crée un dashboard (vide) dans Metabase.

    `parameters` : filtres au niveau dashboard, p.ex.
      [{"id": "abcd1234", "name": "Format", "slug": "format",
        "type": "string/=", "sectionId": "string"}]
    Renvoie {id, name}. Utiliser `add_cards_to_dashboard` pour y déposer des cards.
    """
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    if collection_id is not None:
        body["collection_id"] = collection_id
    d = _post("/api/dashboard", json=body)
    if parameters:
        d = _put(f"/api/dashboard/{d['id']}", json={"parameters": parameters})
    return {"id": d["id"], "name": d["name"]}


@mcp.tool()
def add_cards_to_dashboard(
    dashboard_id: int,
    dashcards: list[dict[str, Any]],
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Place des cards sur un dashboard (remplace la disposition existante).

    Chaque entrée de `dashcards` :
      {"id": -1, "card_id": 123, "row": 0, "col": 0, "size_x": 12, "size_y": 6,
       "parameter_mappings": [
          {"parameter_id": "abcd1234", "card_id": 123,
           "target": ["dimension", ["template-tag", "format"]]}
       ]}
    Utiliser des `id` négatifs distincts (-1, -2, …) pour les nouvelles dashcards.
    `parameters` (optionnel) met aussi à jour les filtres du dashboard.
    Renvoie {id, dashcard_count}.
    """
    payload: dict[str, Any] = {"dashcards": dashcards}
    if parameters is not None:
        payload["parameters"] = parameters
    d = _put(f"/api/dashboard/{dashboard_id}", json=payload)
    return {"id": d["id"], "dashcard_count": len(d.get("dashcards", []))}


if __name__ == "__main__":
    mcp.run()
