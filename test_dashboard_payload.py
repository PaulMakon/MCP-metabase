"""Tests de `_dashboard_payload` : le corps envoyé à Metabase pour poser des cards, avec ou sans onglets.

Sans Metabase ni clé API. Lancer : .venv/bin/python test_dashboard_payload.py (ou pytest s'il est installé).
"""
import metabase_mcp as m


def _dashcard(i, tab=None):
    dc = {"id": i, "card_id": 100 + i, "row": 0, "col": 0, "size_x": 4, "size_y": 3}
    if tab is not None:
        dc["dashboard_tab_id"] = tab
    return dc


def test_sans_onglets_le_corps_est_inchange():
    dcs = [_dashcard(-1)]
    assert m._dashboard_payload(dcs) == {"dashcards": dcs}
    assert m._dashboard_payload(dcs, parameters=[{"id": "p"}]) == {"dashcards": dcs, "parameters": [{"id": "p"}]}


def test_onglets_recoivent_leur_position():
    tabs = [{"id": -1, "name": "A"}, {"id": -2, "name": "B"}, {"id": -3, "name": "C"}]
    payload = m._dashboard_payload([_dashcard(-1, -1)], tabs=tabs)
    assert [(t["id"], t["name"], t["position"]) for t in payload["tabs"]] == [(-1, "A", 0), (-2, "B", 1), (-3, "C", 2)]


def test_position_donnee_est_respectee():
    payload = m._dashboard_payload([_dashcard(-1, -1)], tabs=[{"id": -1, "name": "A", "position": 5}])
    assert payload["tabs"][0]["position"] == 5


def test_tabs_vide_retire_les_onglets():
    assert m._dashboard_payload([_dashcard(-1)], tabs=[])["tabs"] == []


def test_dashcard_vers_un_onglet_inconnu_leve_avant_d_appeler_metabase():
    try:
        m._dashboard_payload([_dashcard(-1, -9)], tabs=[{"id": -1, "name": "A"}])
    except ValueError as e:
        assert "-9" in str(e) and "-1" in str(e)
    else:
        raise AssertionError("une dashcard vers un onglet absent aurait dû lever")


def test_onglets_existants_par_leur_id_reel():
    payload = m._dashboard_payload([_dashcard(-1, 42)], tabs=[{"id": 42, "name": "Renommé"}])
    assert payload["tabs"][0]["id"] == 42


def test_le_tool_expose_le_parametre_tabs():
    import asyncio
    tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
    assert "tabs" in tools["add_cards_to_dashboard"].inputSchema["properties"]


if __name__ == "__main__":
    for nom, fn in list(globals().items()):
        if nom.startswith("test_"):
            fn()
            print("OK", nom)
