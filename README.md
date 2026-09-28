# Metabase MCP

Serveur MCP (Python) pour interroger une instance Metabase depuis Claude Code.

## Outils exposés

| Outil | Rôle |
|---|---|
| `list_databases` | Bases connectées à Metabase (id, name, engine) |
| `search` | Recherche transverse (questions, dashboards, collections, tables) |
| `list_cards` | Liste des questions (cards) |
| `get_card` | Détail d'une question (SQL/définition, paramètres) |
| `list_dashboards` | Liste des dashboards |
| `get_dashboard` | Détail d'un dashboard + ses cards |
| `run_card` | Exécute une question existante → lignes |
| `run_sql` | Exécute un SELECT natif sur une base → lignes |

## Configuration

1. Créer une clé API dans Metabase : **Settings > Authentication > API keys > Create API key**.
2. Copier la config et renseigner les valeurs :

   ```bash
   cp .env.example .env
   # éditer .env : METABASE_URL et METABASE_API_KEY
   ```

Les secrets restent dans `.env` (gitignoré) — rien en dur dans le code.

## Enregistrement dans Claude Code

Déjà fait via `claude mcp add` (scope user). Pour le refaire manuellement :

```bash
claude mcp add -s user metabase -- \
  /Users/pauljuniorfeudjiomakon/Downloads/code/metabase-mcp/.venv/bin/python \
  /Users/pauljuniorfeudjiomakon/Downloads/code/metabase-mcp/metabase_mcp.py
```

Vérifier : `claude mcp list` puis, dans une session, `/mcp`.

## Test rapide hors Claude

```bash
.venv/bin/python smoke_test.py
```
