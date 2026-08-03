"""Los bordes del cierre de ciclo: cuando todavía no hay nada que cerrar.

Una quesera que acaba de empezar no tiene ni una tanda cargada. La pantalla de
ciclos es de las primeras que va a abrir por curiosidad, y no puede reventar ni
proponerle cerrar un ciclo que no existe: eso la haría desconfiar del resto.
"""
from tests.conftest import auth_headers

API = "/api/v1/produccion"


def test_sin_ni_una_tanda_la_pantalla_abre_vacia(client, base_datos):
    """Sin producciones no hay ciclo que proponer, y hay que decirlo con un
    `null` limpio, no con un error ni con un rango inventado."""
    h = auth_headers(client, "admin.a")

    r = client.get(f"{API}/ciclos", headers=h)
    assert r.status_code == 200, r.text
    panel = r.json()
    print("\n===== BORDE: EMPRESA NUEVA =====")
    print(f"  ciclos: {panel['ciclos']} · propuesta: {panel['propuesta']}")
    assert panel["ciclos"] == []
    assert panel["propuesta"] is None
    assert float(panel["total_kilos_merma"]) == 0

    r = client.get(f"{API}/ciclos/propuesta", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() is None


def test_un_rango_al_reves_se_avisa_en_vez_de_devolver_vacio(client, base_datos):
    """Un rango invertido devolvería la pantalla en ceros y parecería que se
    perdieron los datos, que es lo peor que le puede pasar a esta pantalla."""
    h = auth_headers(client, "admin.a")
    r = client.get(
        f"{API}/ciclos/propuesta",
        params={"desde": "2026-07-22", "hasta": "2026-07-16"},
        headers=h,
    )
    print("\n===== BORDE: RANGO AL REVÉS =====")
    print(f"  {r.status_code} · {r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422

    r = client.post(
        f"{API}/ciclos/cerrar",
        json={"fecha_inicio": "2026-07-22", "fecha_fin": "2026-07-16"},
        headers=h,
    )
    print(f"  al cerrar: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "antes de empezar" in r.json()["error"]["detail"]


def test_un_rango_sin_movimientos_se_marca_como_vacio(client, base_datos):
    """Hay tandas, pero no en esas fechas. No es un error: simplemente ahí no
    pasó nada, y la pantalla lo dice en vez de ofrecer un botón que no hace
    nada."""
    from tests.test_ciclo_despacho import (
        montar_leche,
        producir,
        recibir,
        tipo_queso,
    )

    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)

    r = client.get(
        f"{API}/ciclos/propuesta",
        params={"desde": "2026-09-01", "hasta": "2026-09-07"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    p = r.json()
    print("\n===== BORDE: RANGO SIN MOVIMIENTOS =====")
    print(f"  vacio={p['vacio']} · merma {p['kilos_merma']} kg")
    assert p["vacio"] is True
    assert float(p["kilos_merma"]) == 0

    cierre = client.post(
        f"{API}/ciclos/cerrar",
        json={"fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-07"},
        headers=h,
    )
    print(f"  cerrarlo igual: {cierre.status_code} · "
          f"{cierre.json().get('error', {}).get('detail', '')}")
    assert cierre.status_code == 422
    # El aviso tiene que decir la verdad: que ahí no pasó nada, no que "se
    # despachó todo lo que se produjo", que mandaría a buscar un error que no hay.
    assert "no hay tandas ni despachos" in cierre.json()["error"]["detail"]
