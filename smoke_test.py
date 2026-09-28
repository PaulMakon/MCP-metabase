"""Test rapide : vérifie la config et tente un appel réel si .env est rempli."""

import asyncio

import metabase_mcp as m


async def main() -> None:
    tools = await m.mcp.list_tools()
    print("Outils exposés :", ", ".join(t.name for t in tools))

    if not m.METABASE_URL or not m.METABASE_API_KEY:
        print("\n.env non configuré (METABASE_URL / METABASE_API_KEY).")
        print("→ Le serveur démarre quand même ; renseigne .env pour les appels réels.")
        return

    print(f"\nConnexion à {m.METABASE_URL} ...")
    dbs = m.list_databases()
    print(f"OK — {len(dbs)} base(s) :")
    for d in dbs:
        print(f"  - [{d['id']}] {d['name']} ({d['engine']})")


if __name__ == "__main__":
    asyncio.run(main())
