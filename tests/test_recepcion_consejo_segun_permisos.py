"""EL CONSEJO DEL DÍA NO LE NOMBRA A NADIE UN BOTÓN QUE EL SERVIDOR LE NIEGA.

Supervisor y Compras corrigen los días en Recepción diaria (`recepcion:editar`), pero no
tienen `liquidaciones:administrar` —Corregir y Anular— ni `liquidaciones:eliminar` —borrar
un pago—. El 422 del día les decía "use 'Corregir esta quincena'", "anule primero esa
liquidación" o "Elimine primero ese pago": en la liquidación no ven ese botón y el servidor
les contesta 403. Para ellos el consejo dice quién lo hace: un Administrador de la empresa.

La razón (el aviso y el rebote) es la misma para todos, y el aviso del día
(`candado_aviso`) sigue sin llevar consejo: la grilla y el diálogo dicen lo mismo a
cualquiera.
"""
import uuid
from decimal import Decimal

import pytest

from app.core.context import RequestContext
from app.core.exceptions import BusinessError
from app.modules.liquidaciones.models import Liquidacion
from app.modules.recepcion.models import RecepcionLeche
from app.modules.recepcion.service import RecepcionService, _por_que_esta_trabada
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol
from tests.test_liquidacion_deuda_arrastrada_plata import _montar_par
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _leer
from tests.test_recepcion_candado_quincena_corregida import _corregida_sin_pagos

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"

PIDA = "Administrador de la empresa"
# Lo que solo se le puede decir a quien tiene el botón: el consejo en imperativo.
A_QUIEN_PUEDE = ("día, use 'Corregir esta quincena'", "Anule primero", "Elimine primero")


def D(v):
    return Decimal(str(v))


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _usuarios(client, db_session, base_datos):
    emp = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, emp, "Supervisor", "rol.supervisor")
    crear_usuario_con_rol(db_session, emp, "Compras", "rol.compras")
    return {
        "admin": auth_headers(client, "admin.a"),
        "supervisor": auth_headers(client, "rol.supervisor"),
        "compras": auth_headers(client, "rol.compras"),
    }


def _aviso_igual_para_todos(client, hs, dia):
    """El aviso del día no depende de quién mira, y no lleva consejo."""
    avisos = {q: client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"]
              for q, h in hs.items()}
    assert len(set(avisos.values())) == 1, avisos
    aviso = avisos["admin"]
    assert "Corregir" not in aviso and PIDA not in aviso and "nule" not in aviso
    return aviso


def test_la_corregida_v2_manda_al_administrador_a_quien_no_puede_corregir(
        client, base_datos, db_session):
    """'parcial' v2 de $216.000 (adelanto $180.000 + día olvidado de 20 L), saldo $36.000."""
    hs = _usuarios(client, db_session, base_datos)
    _, dia, _, liq = _corregida_sin_pagos(client, hs["admin"], "Permisos Corregida")
    detalle = next(d for d in _leer(client, hs["admin"], liq)["detalles"]
                   if d["fecha"] == "2026-06-02")
    cuerpo = {"motivo": "precio mal digitado",
              "precios": [{"detalle_id": detalle["id"], "precio_litro": "1700"}]}
    _aviso_igual_para_todos(client, hs, dia)
    for quien in ("supervisor", "compras"):
        h = hs[quien]
        put = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
        texto = _detalle(put)
        print(f"\n  {quien}: {texto}")
        assert put.status_code == 422
        assert "ya emitió un comprobante corregido" in texto
        for frase in A_QUIEN_PUEDE:
            assert frase not in texto, (quien, frase)
        assert ("Si lo que está mal es el precio del día, pídale a un Administrador de la "
                "empresa que use 'Corregir esta quincena'") in texto
        # El botón que se le nombraba antes le da 403: por eso no se le dice que lo use.
        assert client.post(f"{API}/{liq}/corregir/previsualizar", json=cuerpo,
                           headers=h).status_code == 403
    # Al Administrador se le dice a él, y su salida existe.
    texto = _detalle(client.put(f"{REC}/{dia}", json={"precio_litro": "1700"},
                                headers=hs["admin"]))
    assert "día, use 'Corregir esta quincena'" in texto and PIDA not in texto
    assert client.post(f"{API}/{liq}/corregir/previsualizar", json=cuerpo,
                       headers=hs["admin"]).status_code == 200
    assert D(_leer(client, hs["admin"], liq)["saldo"]) == D(36000)


def test_la_deuda_cobrada_no_le_dice_anule_a_quien_no_puede_anular(
        client, base_datos, db_session):
    """Q1: $180.000 contra $300.000 de adelanto (debe $120.000); Q2 de $250.000 se los
    cobra (borrador). Anular Q2 sí destraba el día, pero solo lo puede hacer un
    Administrador."""
    hs = _usuarios(client, db_session, base_datos)
    _, q1, q2, _, dia1, _ = _montar_par(client, hs["admin"], "Henri Permisos")
    _aviso_igual_para_todos(client, hs, dia1["id"])
    for quien in ("supervisor", "compras"):
        h = hs[quien]
        put = client.put(f"{REC}/{dia1['id']}", json={"cantidad_litros": "90"}, headers=h)
        texto = _detalle(put)
        print(f"\n  {quien}: {texto}")
        assert put.status_code == 422
        for frase in A_QUIEN_PUEDE:
            assert frase not in texto, (quien, frase)
        assert ("Pídale a un Administrador de la empresa que anule primero esa "
                "liquidación") in texto
        assert client.post(f"{API}/{q2['id']}/anular", headers=h).status_code == 403
    # El Administrador lo lee para él, lo sigue, y el día se corrige.
    texto = _detalle(client.put(f"{REC}/{dia1['id']}", json={"cantidad_litros": "90"},
                                headers=hs["admin"]))
    assert "Anule primero esa liquidación" in texto and PIDA not in texto
    _ok(client.post(f"{API}/{q2['id']}/anular", headers=hs["admin"]))
    _ok(client.put(f"{REC}/{dia1['id']}", json={"cantidad_litros": "90"},
                   headers=hs["supervisor"]))
    # 90 L × $1.800 = $162.000 − $300.000: debe $138.000.
    assert D(_leer(client, hs["admin"], q1["id"])["saldo"]) == D(-138000)


def test_el_abono_no_le_dice_elimine_a_quien_no_puede_borrar_pagos(
        client, base_datos, db_session):
    """100 L × $1.800 = $180.000 con un abono de $50.000 ('parcial' v1, saldo $130.000)."""
    hs = _usuarios(client, db_session, base_datos)
    h = hs["admin"]
    prov = client.post(f"{V}/proveedores", json={"nombre": "Abono Permisos", "vereda": "X",
                                                 "precio_litro": "1800"},
                       headers=h).json()["id"]
    dia = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                 "cantidad_litros": "100"}, headers=h).json()["id"]
    g = _ok(client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                                "periodo_fin": "2026-06-15",
                                                "tipo": "proveedor"}, headers=h))
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
    pago = _ok(client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20",
                                                       "valor": "50000"},
                           headers=h))["pagos"][0]
    _aviso_igual_para_todos(client, hs, dia)
    for quien in ("supervisor", "compras"):
        put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=hs[quien])
        texto = _detalle(put)
        print(f"\n  {quien}: {texto}")
        assert put.status_code == 422 and "ya tiene un pago registrado" in texto
        for frase in A_QUIEN_PUEDE:
            assert frase not in texto, (quien, frase)
        assert "pídale a un Administrador de la empresa que use 'Corregir esta quincena'" \
            in texto
        assert "pídale a un Administrador de la empresa que elimine primero ese pago" in texto
        assert client.delete(f"{API}/{liq}/pagos/{pago['id']}",
                             headers=hs[quien]).status_code == 403
    assert D(_leer(client, h, liq)["saldo"]) == D(130000)


# ---------------------------------------------------------------------------------------
# Las combinaciones que no trae ningún rol sembrado, y el DELETE del día
# ---------------------------------------------------------------------------------------
def _ctx(base_datos, *permisos):
    return RequestContext(empresa_id=base_datos["empresa_a"].id, user_id=uuid.uuid4(),
                          permisos=set(permisos))


def test_cada_permiso_recorta_solo_su_salida(client, base_datos, db_session):
    """Un rol propio puede tener uno sin el otro: el consejo nombra cada botón solo a quien
    lo tiene, sobre la misma 'parcial' con un abono de $50.000."""
    h = auth_headers(client, "admin.a")
    prov = client.post(f"{V}/proveedores", json={"nombre": "Abono Combinado", "vereda": "X",
                                                 "precio_litro": "1800"},
                       headers=h).json()["id"]
    client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                           "cantidad_litros": "100"}, headers=h)
    g = _ok(client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                                "periodo_fin": "2026-06-15",
                                                "tipo": "proveedor"}, headers=h))
    liq_id = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    _ok(client.post(f"{API}/{liq_id}/aprobar", headers=h))
    _ok(client.post(f"{API}/{liq_id}/pagos", json={"fecha": "2026-06-20", "valor": "50000"},
                    headers=h))
    liq = db_session.get(Liquidacion, uuid.UUID(liq_id))
    db_session.refresh(liq)

    solo_administrar = _por_que_esta_trabada(
        liq, _ctx(base_datos, ("liquidaciones", "administrar"))).consejo
    assert "día, use 'Corregir esta quincena'" in solo_administrar
    assert "pídale a un Administrador de la empresa que elimine primero ese pago" \
        in solo_administrar
    assert "Elimine primero" not in solo_administrar

    solo_eliminar = _por_que_esta_trabada(
        liq, _ctx(base_datos, ("liquidaciones", "eliminar"))).consejo
    assert "pídale a un Administrador de la empresa que use 'Corregir esta quincena'" \
        in solo_eliminar
    assert "Elimine primero ese pago" in solo_eliminar

    # La razón es la misma para todos; solo cambia el consejo.
    todos = _por_que_esta_trabada(liq, None)
    ninguno = _por_que_esta_trabada(liq, _ctx(base_datos))
    assert (ninguno.aviso, ninguno.rebote) == (todos.aviso, todos.rebote)
    assert len({todos.consejo, solo_administrar, solo_eliminar, ninguno.consejo}) == 4


def test_el_delete_del_dia_tambien_mira_el_permiso(client, base_datos, db_session):
    """Un rol propio con `recepcion:eliminar` y sin `liquidaciones:administrar` llega al
    422 de borrar el día (los sembrados chocan antes con el 403). El consejo es el suyo."""
    h = auth_headers(client, "admin.a")
    _, dia, _, _ = _corregida_sin_pagos(client, h, "Permisos Delete")
    servicio = RecepcionService(db_session, _ctx(base_datos, ("recepcion", "eliminar")))
    with pytest.raises(BusinessError) as rebote:
        servicio.eliminar(uuid.UUID(dia))
    texto = rebote.value.detail
    print(f"\n  DELETE: {texto}")
    assert texto.startswith(
        "No se puede eliminar este día: la quincena de la leche de este día ya emitió un "
        "comprobante corregido.")
    assert "pídale a un Administrador de la empresa que use 'Corregir esta quincena'" in texto
    assert "día, use 'Corregir" not in texto
    assert db_session.get(RecepcionLeche, uuid.UUID(dia)).deleted_at is None
