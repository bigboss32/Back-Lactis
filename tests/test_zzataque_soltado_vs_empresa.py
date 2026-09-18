"""ATAQUE — CAMINO 3: EL REINICIO DE EMPRESA, EL AISLAMIENTO ENTRE QUESERAS Y LA NÓMINA,
contra la marca nueva `Anticipo.soltado_de_liquidacion_id`.

QUÉ SE ESTÁ MIDIENDO. Desde que existe "Corregir esta quincena", una corrección puede
SACAR un adelanto de un comprobante ya entregado. Ese adelanto queda con
`liquidacion_id = None` —idéntico a uno recién registrado— y por eso se le puso una
marca nueva: `soltado_de_liquidacion_id`, una FK a `liquidaciones`. La marca la escribe
`LiquidacionService.corregir_pagada` y la miran `AnticipoService._exigir_no_pagado` (el
candado) y `_marcar_liquidacion` (el campo `bloqueado` que ve la pantalla).

La marca se revisó contra la pantalla de anticipos. NO se revisó contra tres caminos que
tocan esas mismas filas por otra puerta, y eso es lo que se mide aquí:

  (1) EL REINICIO DE EMPRESA (`EmpresaService.reiniciar`), que borra anticipos y
      liquidaciones. La marca nueva es una FK a `liquidaciones`: si el reinicio se
      trabara, o dejara filas colgando apuntando a una quincena borrada, la quesera
      quedaría a medio reiniciar y con plata fantasma.
  (2) EL AISLAMIENTO ENTRE QUESERAS. La Quesera B no puede ver ni tocar un adelanto
      soltado de la Quesera A, y tiene que ser 404 —no 403—: un 403 le confirmaría a B
      que ese id existe. Y el campo `bloqueado` que devuelve la lista no puede
      contestarse con información de la otra empresa.
  (3) LOS ADELANTOS DE NÓMINA (`pago_empleado_id`), que van por otro camino: la marca
      nueva no los toca y su candado propio sigue igual.

LA REGLA DE LA CASA, en cada paso donde hay una quincena a la vista:

    suma de los días  = valor_total
    neto_a_pagar      = valor_total - anticipos - saldo_anterior
    neto_a_pagar      = pagado + saldo

Las cifras son las del dueño ($500.000 de quincena, $200.000 y $300.000 de adelanto) y se
imprimen en cada paso para poder sumarlas a mano.
"""
import uuid as _uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.empleados.models import PagoEmpleado
from app.modules.liquidaciones.models import Anticipo, CorreccionLiquidacion, Liquidacion
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
ANTICIPOS = "/api/v1/anticipos"
EMPRESAS = "/api/v1/empresas"
NOMINA = "/api/v1/nomina"

INICIO = "2026-06-01"
FIN = "2026-06-15"


def D(v):
    return Decimal(str(v))


def _detalle(r):
    """El texto del error de negocio, que es lo que lee el dueño en la pantalla."""
    try:
        return r.json().get("error", {}).get("detail", "")
    except Exception:  # pragma: no cover - respuestas sin cuerpo (204)
        return ""


def _mostrar(titulo, liq):
    print(
        f"  {titulo:<22}. estado {liq['estado']:<9}. v{liq.get('version', '?')} "
        f". total {liq['valor_total']} . anticipos {liq['anticipos']} "
        f". neto {liq['neto_a_pagar']} . pagado {liq['pagado']} . saldo {liq['saldo']}"
    )


def _cuadra(liq) -> bool:
    """LA REGLA DE LA CASA, la que el dueño verifica con calculadora."""
    suma_dias = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq.get("saldo_anterior") or 0)
    return (
        suma_dias == D(liq["valor_total"])
        and D(liq["neto_a_pagar"]) == neto
        and D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])
    )


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": str(precio)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dia(client, h, proveedor_id, fecha, litros):
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": fecha, "proveedor_id": proveedor_id, "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, proveedor_id, fecha, valor, obs=None):
    r = client.post(
        ANTICIPOS,
        json={
            "tipo": "proveedor",
            "proveedor_id": proveedor_id,
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": obs,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, proveedor_id):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": INICIO, "periodo_fin": FIN, "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == proveedor_id)


def quincena_con_adelanto_soltado(client, h, nombre="Libardo", valor_adelanto=200000):
    """LA SITUACIÓN QUE ESTRENA LA MARCA, montada con plata de verdad.

    250 L a $2.000 = $500.000, menos $200.000 de adelanto: neto $300.000, que se pagan.
    Después una corrección SACA el adelanto de esa quincena ya pagada: el comprobante -v2
    sube a neto $500.000 con $300.000 entregados y $200.000 por entregar, y el adelanto
    queda SUELTO pero MARCADO —`soltado_de_liquidacion_id` puesta— porque el papel que el
    productor tiene en la mano promete descontárselo en la siguiente.

    Devuelve (proveedor, anticipo, liquidacion_corregida).
    """
    prov = _proveedor(client, h, nombre, 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", valor_adelanto, "para la droga")
    liq = _generar(client, h, prov["id"])
    assert D(liq["valor_total"]) == D(500000)
    assert D(liq["anticipos"]) == D(valor_adelanto)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    pagada = pagada.json()
    assert pagada["estado"] == "pagada"
    assert _cuadra(pagada)

    motivo = "ese adelanto no iba en esta quincena"
    prev = client.post(
        f"{API}/{liq['id']}/corregir/previsualizar", json={"motivo": motivo}, headers=h
    )
    assert prev.status_code == 200, prev.text
    aplicados = prev.json()["anticipos_aplicados"]
    assert [a["anticipo_id"] for a in aplicados] == [ant["id"]], aplicados

    r = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": motivo, "anticipos_a_soltar": [ant["id"]]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert corregida["version"] == 2
    assert D(corregida["anticipos"]) == D(0), "la corrección no soltó el adelanto"
    assert D(corregida["neto_a_pagar"]) == D(500000)
    assert _cuadra(corregida)

    # Y quedó MARCADO: el servidor lo rebota y la pantalla lo dice.
    suelto = client.get(f"{ANTICIPOS}/{ant['id']}", headers=h).json()
    assert suelto["liquidacion_id"] is None, "tenía que quedar suelto"
    assert suelto["bloqueado"] is True, "la marca nueva no está trabando el adelanto"
    return prov, ant, corregida


def _marca_en_bd(db_session, anticipo_id):
    """La columna nueva leída de la tabla, sin pasar por el esquema de respuesta."""
    db_session.expire_all()
    return db_session.execute(
        select(Anticipo.soltado_de_liquidacion_id).where(
            Anticipo.id == _uuid.UUID(str(anticipo_id))
        )
    ).scalar()


def _contar(db_session, modelo, empresa_id=None):
    """Cuenta FILAS DE VERDAD, incluidas las borradas en suave: el reinicio borra duro
    y lo que se está midiendo es justamente si queda algo."""
    db_session.expire_all()
    stmt = select(func.count()).select_from(modelo)
    if empresa_id is not None:
        stmt = stmt.where(modelo.empresa_id == empresa_id)
    return db_session.execute(stmt).scalar()


def _colgando(db_session, modelo, campo, padre):
    """Filas de `modelo` cuya FK `campo` apunta a un `padre` que ya no existe."""
    db_session.expire_all()
    return db_session.execute(
        select(func.count())
        .select_from(modelo)
        .where(campo.is_not(None), campo.not_in(select(padre.id)))
    ).scalar()


def _reiniciar(client, hs, empresa_id, nombre):
    return client.post(
        f"{EMPRESAS}/{empresa_id}/reiniciar", json={"confirmacion": nombre}, headers=hs
    )


# ===========================================================================
# (1) EL REINICIO DE EMPRESA
# ===========================================================================
def test_el_reinicio_no_se_traba_con_un_adelanto_soltado_y_se_lo_lleva(
    client, base_datos, db_session
):
    """La marca nueva es una FK a `liquidaciones`, y el reinicio borra las dos tablas.

    Si el orden estuviera mal, el borrado de `liquidaciones` chocaría contra la FK nueva
    y el reinicio quedaría a medias: la quesera con las quincenas borradas y los
    adelantos vivos, o al revés. Aquí se monta la situación más difícil —una quincena
    CORREGIDA con un adelanto soltado y marcado— y se exige que el reinicio pase y deje
    las dos tablas en cero.
    """
    ha = auth_headers(client, "admin.a")
    hs = auth_headers(client, "superadmin")
    empresa_a = base_datos["empresa_a"].id

    prov, ant, corregida = quincena_con_adelanto_soltado(client, ha)
    print("\n===== (1) REINICIO CON UN ADELANTO SOLTADO =====")
    _mostrar("quincena corregida", corregida)
    marca = _marca_en_bd(db_session, ant["id"])
    print(f"  marca soltado_de_liquidacion_id = {marca}")
    assert marca is not None, "la corrección no escribió la marca nueva"

    r = _reiniciar(client, hs, empresa_a, "Quesera A")
    print(f"  POST /empresas/A/reiniciar -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    borrados = r.json()
    print(f"  borrados: anticipos={borrados.get('anticipos')} "
          f"liquidaciones={borrados.get('liquidaciones')}")
    assert borrados.get("anticipos") == 1, borrados
    assert borrados.get("liquidaciones") == 1, borrados

    assert _contar(db_session, Anticipo, empresa_a) == 0
    assert _contar(db_session, Liquidacion, empresa_a) == 0
    assert client.get(ANTICIPOS, headers=ha).json()["total"] == 0
    assert client.get(API, headers=ha).json()["total"] == 0
    # El proveedor es catálogo: sobrevive, como manda el reinicio.
    assert client.get(f"/api/v1/proveedores/{prov['id']}", headers=ha).status_code == 200


def test_el_reinicio_no_deja_anticipos_apuntando_a_una_quincena_borrada(
    client, base_datos, db_session
):
    """NINGUNA fila de `anticipos` puede quedar con la marca nueva apuntando al vacío.

    Se monta el caso mixto, que es el que de verdad puede dejar basura: DOS proveedores,
    uno con el adelanto soltado por una corrección (marca puesta) y otro con el adelanto
    todavía descontado en su quincena pagada (marca nula). Después del reinicio se
    barre la tabla entera buscando marcas huérfanas.
    """
    ha = auth_headers(client, "admin.a")
    hs = auth_headers(client, "superadmin")
    empresa_a = base_datos["empresa_a"].id

    _, ant_soltado, _ = quincena_con_adelanto_soltado(client, ha, nombre="Libardo")
    # El segundo, que se queda quieto dentro de su quincena pagada.
    otro = _proveedor(client, ha, "Amparo", 1800)
    _dia(client, ha, otro["id"], "2026-06-04", 100)
    ant_quieto = _anticipo(client, ha, otro["id"], "2026-06-05", 50000, "el del 5")
    liq2 = _generar(client, ha, otro["id"])
    assert client.post(f"{API}/{liq2['id']}/aprobar", headers=ha).status_code == 200
    pagada2 = client.post(f"{API}/{liq2['id']}/pagar", headers=ha).json()
    print("\n===== (1.bis) MARCAS HUÉRFANAS DESPUÉS DEL REINICIO =====")
    _mostrar("la que se queda", pagada2)
    assert _cuadra(pagada2)
    assert _marca_en_bd(db_session, ant_soltado["id"]) is not None
    assert _marca_en_bd(db_session, ant_quieto["id"]) is None

    assert _reiniciar(client, hs, empresa_a, "Quesera A").status_code == 200

    huerfanos = _colgando(
        db_session, Anticipo, Anticipo.soltado_de_liquidacion_id, Liquidacion
    )
    colgando = _colgando(db_session, Anticipo, Anticipo.liquidacion_id, Liquidacion)
    print(f"  anticipos con marca huérfana: {huerfanos} . con liquidacion_id huérfano: {colgando}")
    assert huerfanos == 0, "quedaron adelantos marcados contra una quincena borrada"
    assert colgando == 0


def test_el_reinicio_se_lleva_el_renglon_de_la_correccion(client, base_datos, db_session):
    """El renglón de la corrección es PLATA CONGELADA de una quesera que quedó en ceros.

    `CorreccionLiquidacion` guarda `valor_total_antes/despues`, `neto_antes/despues`,
    `anticipos_antes/despues` y el desglose `anticipos_cambiados` — las cifras exactas de
    la quincena que el reinicio acaba de borrar. Si sobrevive:

      · queda una fila con `empresa_id` de la Quesera A y `liquidacion_id` apuntando al
        vacío, en una empresa que se supone que quedó en ceros;
      · y trae adentro las cifras del productor ($500.000 de quincena, $200.000 de
        adelanto), que es justo lo que el reinicio promete llevarse.
    """
    ha = auth_headers(client, "admin.a")
    hs = auth_headers(client, "superadmin")
    empresa_a = base_datos["empresa_a"].id

    quincena_con_adelanto_soltado(client, ha)
    antes = _contar(db_session, CorreccionLiquidacion, empresa_a)
    print("\n===== (1.ter) EL RENGLÓN DE LA CORRECCIÓN =====")
    print(f"  renglones de corrección antes del reinicio: {antes}")
    assert antes == 1, "la corrección no dejó su renglón"

    assert _reiniciar(client, hs, empresa_a, "Quesera A").status_code == 200

    quedan = _colgando(
        db_session, CorreccionLiquidacion, CorreccionLiquidacion.liquidacion_id, Liquidacion
    )
    db_session.expire_all()
    for c in db_session.execute(select(CorreccionLiquidacion)).scalars().all():
        print(
            f"  SOBREVIVE: empresa_a={c.empresa_id == empresa_a} v{c.version_nueva} "
            f"motivo='{c.motivo}' total {c.valor_total_antes}->{c.valor_total_despues} "
            f"neto {c.neto_antes}->{c.neto_despues} "
            f"anticipos {c.anticipos_antes}->{c.anticipos_despues} "
            f"desglose={c.anticipos_cambiados} por='{c.corregido_por_nombre}'"
        )
    print(f"  renglones colgando de una quincena borrada: {quedan}")
    assert quedan == 0, (
        "sobrevivió el renglón de la corrección apuntando a una quincena borrada"
    )


def test_el_reinicio_de_una_quesera_no_toca_el_adelanto_soltado_de_la_otra(
    client, base_datos, db_session
):
    """Las dos queseras montan la MISMA situación. Se reinicia una; la otra no se mueve.

    Se compara cifra por cifra: la quincena corregida de B sigue con su total de
    $500.000, su neto de $500.000, sus $300.000 entregados y sus $200.000 por entregar;
    y su adelanto sigue suelto, marcado y trabado.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    hs = auth_headers(client, "superadmin")
    empresa_a = base_datos["empresa_a"].id
    empresa_b = base_datos["empresa_b"].id

    quincena_con_adelanto_soltado(client, ha, nombre="Libardo")
    _, ant_b, corregida_b = quincena_con_adelanto_soltado(client, hb, nombre="Henri")

    print("\n===== (1.quater) REINICIO DE A, LA B QUIETA =====")
    _mostrar("B antes", corregida_b)
    marca_b = _marca_en_bd(db_session, ant_b["id"])
    assert marca_b is not None

    assert _reiniciar(client, hs, empresa_a, "Quesera A").status_code == 200

    despues = client.get(f"{API}/{corregida_b['id']}", headers=hb)
    assert despues.status_code == 200, despues.text
    despues = despues.json()
    _mostrar("B después", despues)
    assert D(despues["valor_total"]) == D(500000)
    assert D(despues["anticipos"]) == D(0)
    assert D(despues["neto_a_pagar"]) == D(500000)
    assert D(despues["pagado"]) == D(300000)
    assert D(despues["saldo"]) == D(200000)
    assert despues["version"] == 2
    assert _cuadra(despues)

    sigue = client.get(f"{ANTICIPOS}/{ant_b['id']}", headers=hb)
    assert sigue.status_code == 200, sigue.text
    sigue = sigue.json()
    print(f"  adelanto de B: valor {sigue['valor']} . bloqueado={sigue['bloqueado']}")
    assert D(sigue["valor"]) == D(200000)
    assert sigue["bloqueado"] is True, "el reinicio de A le soltó el candado a B"
    assert _marca_en_bd(db_session, ant_b["id"]) == marca_b
    assert _contar(db_session, Anticipo, empresa_b) == 1
    assert _contar(db_session, Liquidacion, empresa_b) == 1

    # Y el candado de B sigue cerrado de verdad, no solo en la pantalla.
    editar = client.put(f"{ANTICIPOS}/{ant_b['id']}", json={"valor": "1"}, headers=hb)
    print(f"  PUT /anticipos/B -> {editar.status_code} . {_detalle(editar)}")
    assert editar.status_code == 422, editar.text


# ===========================================================================
# (2) EL AISLAMIENTO ENTRE QUESERAS
# ===========================================================================
def test_la_quesera_b_no_ve_ni_toca_el_adelanto_soltado_de_la_a(client, base_datos):
    """404 y no 403, en las tres puertas, y sin mover un peso de la Quesera A.

    El 404 no es cosmética: un 403 le CONFIRMA a B que ese id existe en el sistema —y
    con el id de un adelanto en la mano ya sabe que la otra quesera le adelantó plata a
    alguien—. Y el mensaje del candado nuevo nombra la quincena de la que salió: si
    saliera por esta puerta, B se enteraría de que A corrigió una quincena.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    _, ant_a, _ = quincena_con_adelanto_soltado(client, ha)

    print("\n===== (2) LA B CONTRA EL ADELANTO DE LA A =====")
    ver = client.get(f"{ANTICIPOS}/{ant_a['id']}", headers=hb)
    editar = client.put(f"{ANTICIPOS}/{ant_a['id']}", json={"valor": "1"}, headers=hb)
    borrar = client.delete(f"{ANTICIPOS}/{ant_a['id']}", headers=hb)
    for nombre, r in (("GET", ver), ("PUT", editar), ("DELETE", borrar)):
        print(f"  {nombre:<7} /anticipos/id-de-A -> {r.status_code} . {_detalle(r)}")
        assert r.status_code == 404, f"{nombre} contestó {r.status_code}: {r.text}"
        # Nada del candado nuevo puede asomarse: ni la promesa del comprobante, ni la
        # palabra 'corrección', ni el nombre de la otra puerta.
        assert "corrección" not in _detalle(r)
        assert "quincena" not in _detalle(r)

    # Y el de A quedó igual.
    sigue = client.get(f"{ANTICIPOS}/{ant_a['id']}", headers=ha).json()
    assert D(sigue["valor"]) == D(200000)
    assert sigue["bloqueado"] is True


def test_el_campo_bloqueado_no_se_contesta_con_datos_de_la_otra_quesera(client, base_datos):
    """`bloqueado` de la lista de B tiene que decidirse SOLO con lo que B tiene.

    Se monta el caso adversarial: A con su adelanto soltado y trabado; B con un adelanto
    igual de fecha y valor, recién registrado y libre. Si `_marcar_liquidacion` mirara
    fuera del tenant, el de B saldría trabado —y el dueño de B se quedaría sin poder
    corregir un adelanto suyo por algo que pasó en otra empresa—.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    _, ant_a, _ = quincena_con_adelanto_soltado(client, ha)

    prov_b = _proveedor(client, hb, "Henri", 2000)
    ant_b = _anticipo(client, hb, prov_b["id"], "2026-06-03", 200000, "para la droga")

    print("\n===== (2.bis) EL CAMPO `bloqueado` POR EMPRESA =====")
    lista_b = client.get(ANTICIPOS, headers=hb).json()
    ids_b = [a["id"] for a in lista_b["items"]]
    print(f"  la lista de B trae {lista_b['total']} . bloqueado="
          f"{[a['bloqueado'] for a in lista_b['items']]}")
    assert lista_b["total"] == 1, "B está viendo adelantos que no son suyos"
    assert ids_b == [ant_b["id"]]
    assert ant_a["id"] not in ids_b
    assert lista_b["items"][0]["bloqueado"] is False, (
        "el adelanto libre de B salió trabado: la marca de la otra quesera se filtró"
    )
    assert lista_b["items"][0]["liquidacion_estado"] is None

    # Y el suyo sí se puede corregir: el candado de A no le cierra la puerta a B.
    r = client.put(f"{ANTICIPOS}/{ant_b['id']}", json={"valor": "150000"}, headers=hb)
    print(f"  PUT /anticipos/B -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    assert D(r.json()["valor"]) == D(150000)

    # La lista de A, en cambio, sigue mostrando el suyo trabado.
    lista_a = client.get(ANTICIPOS, headers=ha).json()
    assert lista_a["total"] == 1
    assert lista_a["items"][0]["id"] == ant_a["id"]
    assert lista_a["items"][0]["bloqueado"] is True


def test_la_quesera_b_no_puede_corregir_la_quincena_de_la_a_para_soltarle_el_adelanto(
    client, base_datos
):
    """La otra puerta: la de la corrección, que es la única que SÍ mueve anticipos.

    Si B pudiera entrar por ahí, le movería a A el comprobante de un productor que no es
    suyo. Tiene que ser 404 en las dos: previsualizar y corregir.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    _, ant_a, corregida = quincena_con_adelanto_soltado(client, ha)

    print("\n===== (2.ter) LA B CONTRA LA QUINCENA DE LA A =====")
    prev = client.post(
        f"{API}/{corregida['id']}/corregir/previsualizar",
        json={"motivo": "a ver que hay"},
        headers=hb,
    )
    corr = client.post(
        f"{API}/{corregida['id']}/corregir",
        json={"motivo": "a ver que hay", "anticipos_a_incluir": [ant_a["id"]]},
        headers=hb,
    )
    for nombre, r in (("previsualizar", prev), ("corregir", corr)):
        print(f"  POST {nombre} de la quincena de A -> {r.status_code} . {_detalle(r)}")
        assert r.status_code == 404, f"{nombre} contestó {r.status_code}: {r.text}"

    igual = client.get(f"{API}/{corregida['id']}", headers=ha).json()
    _mostrar("A intacta", igual)
    assert igual["version"] == 2
    assert D(igual["anticipos"]) == D(0)
    assert _cuadra(igual)


# ===========================================================================
# (3) LOS ADELANTOS DE NÓMINA
# ===========================================================================
def _empleado(client, h, nombre="Aurelio", apellido="Marin", valor_dia="40000"):
    r = client.post(
        "/api/v1/empleados",
        json={"nombre": nombre, "apellido": apellido, "cargo": "Quesero",
              "valor_dia": valor_dia},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo_empleado(client, h, empleado_id, fecha, valor, obs=None):
    r = client.post(
        ANTICIPOS,
        json={"tipo": "empleado", "empleado_id": empleado_id, "fecha": fecha,
              "valor": str(valor), "observaciones": obs},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_el_adelanto_de_nomina_no_lo_toca_la_marca_nueva_y_su_candado_sigue_igual(
    client, base_datos, db_session
):
    """El adelanto del empleado va por `pago_empleado_id`, no por liquidaciones.

    Se le descuenta en un pago de nómina: ahí queda trabado —y tiene que quedar trabado
    por SU motivo, con SU mensaje— y la columna nueva tiene que seguir en nulo. Si la
    corrección de quincenas le escribiera la marca, el empleado quedaría con un adelanto
    trabado por dos razones y un mensaje que le habla de una quincena de leche que no
    existe.
    """
    ha = auth_headers(client, "admin.a")
    emp = _empleado(client, ha)
    ant = _anticipo_empleado(client, ha, emp["id"], "2026-06-03", 100000, "adelanto del 3")

    print("\n===== (3) EL ADELANTO DE NÓMINA =====")
    libre = client.get(f"{ANTICIPOS}/{ant['id']}", headers=ha).json()
    print(f"  antes del pago: bloqueado={libre['bloqueado']} "
          f"pago_empleado_id={libre['pago_empleado_id']}")
    assert libre["bloqueado"] is False, "un adelanto de nómina sin pagar se puede corregir"
    assert _marca_en_bd(db_session, ant["id"]) is None

    # Se le puede corregir mientras no se le haya descontado: eso NO puede haberse roto.
    r = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "120000"}, headers=ha)
    print(f"  PUT antes del pago -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    assert D(r.json()["valor"]) == D(120000)

    pago = client.post(
        NOMINA,
        json={"empleado_id": emp["id"], "fecha": "2026-06-10", "dias_trabajados": "10"},
        headers=ha,
    )
    assert pago.status_code == 201, pago.text
    pago = pago.json()
    # 10 días × $40.000 = $400.000 menos $120.000 de adelanto = $280.000 en la mano.
    print(f"  nómina: total {pago.get('total_pagar')} . anticipos {pago.get('anticipos')} "
          f". neto {pago.get('neto_pagar')}")

    aplicado = client.get(f"{ANTICIPOS}/{ant['id']}", headers=ha).json()
    print(f"  después del pago: bloqueado={aplicado['bloqueado']} "
          f"pago_empleado_id={aplicado['pago_empleado_id']}")
    assert aplicado["pago_empleado_id"] is not None
    assert aplicado["bloqueado"] is True
    assert aplicado["liquidacion_id"] is None
    # LA MARCA NUEVA NO LO TOCÓ. Es la columna que estrena este cambio.
    assert _marca_en_bd(db_session, ant["id"]) is None, (
        "la marca de las quincenas de leche se le escribió a un adelanto de nómina"
    )

    editar = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=ha)
    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=ha)
    print(f"  PUT    después del pago -> {editar.status_code} . {_detalle(editar)}")
    print(f"  DELETE después del pago -> {borrar.status_code} . {_detalle(borrar)}")
    assert editar.status_code == 422, editar.text
    assert borrar.status_code == 422, borrar.text
    # SU mensaje, el de siempre: el de nómina. No el del adelanto soltado.
    assert "pago de nómina" in _detalle(editar), _detalle(editar)
    assert "pago de nómina" in _detalle(borrar), _detalle(borrar)
    assert "corrección" not in _detalle(editar)
    assert D(client.get(f"{ANTICIPOS}/{ant['id']}", headers=ha).json()["valor"]) == D(120000)


def test_el_adelanto_de_nomina_no_se_le_cuela_a_la_correccion_de_una_quincena(
    client, base_datos
):
    """Un adelanto del empleado NO puede aparecer como candidato de una quincena de leche.

    Si se colara, la corrección le descontaría al PRODUCTOR $100.000 que se le dieron a
    un trabajador, y encima le escribiría la marca nueva a una fila de nómina. Se mide
    en la pantalla que decide: la previsualización.
    """
    ha = auth_headers(client, "admin.a")
    emp = _empleado(client, ha)
    ant_emp = _anticipo_empleado(client, ha, emp["id"], "2026-06-03", 100000, "del empleado")
    _, ant_prov, corregida = quincena_con_adelanto_soltado(client, ha)

    print("\n===== (3.bis) NÓMINA CONTRA LA CORRECCIÓN =====")
    prev = client.post(
        f"{API}/{corregida['id']}/corregir/previsualizar",
        json={"motivo": "a ver los candidatos"},
        headers=ha,
    )
    assert prev.status_code == 200, prev.text
    sueltos = prev.json()["anticipos_sueltos"]
    ids = [a["anticipo_id"] for a in sueltos]
    print(f"  candidatos sueltos: {len(ids)} -> {ids}")
    assert ant_emp["id"] not in ids, "el adelanto del empleado se coló como candidato"
    assert ant_prov["id"] in ids, "el adelanto soltado del productor no aparece"

    # Y si lo mandan a mano, tampoco entra.
    r = client.post(
        f"{API}/{corregida['id']}/corregir",
        json={"motivo": "meter el del empleado", "anticipos_a_incluir": [ant_emp["id"]]},
        headers=ha,
    )
    print(f"  POST corregir con el del empleado -> {r.status_code} . {_detalle(r)}")
    assert r.status_code in (404, 422), r.text
    despues = client.get(f"{ANTICIPOS}/{ant_emp['id']}", headers=ha).json()
    assert despues["liquidacion_id"] is None
    assert despues["bloqueado"] is False, "quedó trabado un adelanto de nómina sin pagar"


def test_el_reinicio_se_lleva_tambien_los_adelantos_de_nomina(client, base_datos, db_session):
    """El reinicio tiene que dejar las dos familias en cero, no solo la de leche."""
    ha = auth_headers(client, "admin.a")
    hs = auth_headers(client, "superadmin")
    empresa_a = base_datos["empresa_a"].id

    emp = _empleado(client, ha)
    _anticipo_empleado(client, ha, emp["id"], "2026-06-03", 100000, "del empleado")
    client.post(
        NOMINA,
        json={"empleado_id": emp["id"], "fecha": "2026-06-10", "dias_trabajados": "10"},
        headers=ha,
    )
    quincena_con_adelanto_soltado(client, ha)

    print("\n===== (3.ter) REINICIO CON LAS DOS FAMILIAS =====")
    assert _contar(db_session, Anticipo, empresa_a) == 2
    r = _reiniciar(client, hs, empresa_a, "Quesera A")
    print(f"  POST /empresas/A/reiniciar -> {r.status_code} . "
          f"anticipos={r.json().get('anticipos') if r.status_code == 200 else '-'}")
    assert r.status_code == 200, r.text
    assert _contar(db_session, Anticipo, empresa_a) == 0
    assert _contar(db_session, PagoEmpleado, empresa_a) == 0
    presos = _colgando(db_session, Anticipo, Anticipo.pago_empleado_id, PagoEmpleado)
    print(f"  adelantos apuntando a un pago de nómina borrado: {presos}")
    assert presos == 0
    # El empleado es catálogo: sobrevive.
    assert client.get(f"/api/v1/empleados/{emp['id']}", headers=ha).status_code == 200


# ===========================================================================
# (4) QUE EL CANDADO NUEVO NO DEJE AL DUEÑO SIN CAMINO
# ===========================================================================
# Un candado que traba algo que el dueño legítimamente necesita hacer es tan malo como
# uno que falta: el adelanto soltado es plata que se entregó en la mano, y si no hubiera
# forma de volver a cobrarla, el candado habría cambiado un hueco por otro. El mensaje
# del 422 nombra DOS salidas; aquí se recorren las dos, con plata, hasta el final.
SIGUIENTE_INICIO = "2026-06-16"
SIGUIENTE_FIN = "2026-06-30"


def _generar_siguiente(client, h, proveedor_id):
    r = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": SIGUIENTE_INICIO,
            "periodo_fin": SIGUIENTE_FIN,
            "tipo": "proveedor",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == proveedor_id)


def test_la_salida_uno_existe_volver_a_incluirlo_con_corregir_esta_quincena(
    client, base_datos, db_session
):
    """SALIDA 1, la que el 422 nombra primero: volver a meterlo en la misma quincena.

    Se corrige otra vez, ahora al revés: el adelanto ENTRA. El comprobante pasa a -v3,
    los $200.000 vuelven a restarse ($500.000 − $200.000 = $300.000 de neto, que es
    exactamente lo que ya se le entregó, saldo $0) y la marca se LIMPIA: el adelanto
    vuelve a estar protegido por su liquidación y no por la marca.
    """
    ha = auth_headers(client, "admin.a")
    _, ant, corregida = quincena_con_adelanto_soltado(client, ha)

    print("\n===== (4) SALIDA 1: VOLVER A INCLUIRLO =====")
    _mostrar("soltado (-v2)", corregida)
    trabado = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=ha)
    print(f"  PUT /anticipos -> {trabado.status_code} . {_detalle(trabado)}")
    assert trabado.status_code == 422
    assert "Corregir esta quincena" in _detalle(trabado), _detalle(trabado)

    r = client.post(
        f"{API}/{corregida['id']}/corregir",
        json={"motivo": "el adelanto sí iba aquí", "anticipos_a_incluir": [ant["id"]]},
        headers=ha,
    )
    print(f"  POST corregir (incluir) -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    devuelta = r.json()
    _mostrar("recogido (-v3)", devuelta)
    assert devuelta["version"] == 3
    assert D(devuelta["valor_total"]) == D(500000)
    assert D(devuelta["anticipos"]) == D(200000)
    assert D(devuelta["neto_a_pagar"]) == D(300000)
    assert D(devuelta["pagado"]) == D(300000)
    assert D(devuelta["saldo"]) == D(0)
    assert _cuadra(devuelta)

    vuelto = client.get(f"{ANTICIPOS}/{ant['id']}", headers=ha).json()
    print(f"  marca después = {_marca_en_bd(db_session, ant['id'])} . "
          f"liquidacion_id={vuelto['liquidacion_id']} . bloqueado={vuelto['bloqueado']}")
    assert vuelto["liquidacion_id"] == devuelta["id"]
    assert _marca_en_bd(db_session, ant["id"]) is None, (
        "la marca quedó puesta sobre un adelanto que volvió a entrar en su quincena"
    )
    assert vuelto["bloqueado"] is True  # ahora por su liquidación, que ya salió en papel


def test_la_salida_dos_existe_la_quincena_siguiente_lo_recoge_y_ahi_si_se_corrige(
    client, base_datos, db_session
):
    """SALIDA 2, la otra que nombra el 422: esperar a que la siguiente lo recoja.

    La quincena del 16 al 30 son 100 L a $2.000 = $200.000, y el adelanto suelto de
    $200.000 se la come exacta: neto $0. Ahí el adelanto ya NO está trabado por la marca
    —está dentro de un borrador, que sí se puede corregir— y esa es justamente la salida
    que promete el mensaje. Se comprueba corrigiéndolo de verdad: bajarlo a $150.000
    deja el neto en $50.000 y el desglose sigue cuadrando.
    """
    ha = auth_headers(client, "admin.a")
    prov, ant, _ = quincena_con_adelanto_soltado(client, ha)

    print("\n===== (4.bis) SALIDA 2: LA QUINCENA SIGUIENTE =====")
    _dia(client, ha, prov["id"], "2026-06-20", 100)
    siguiente = _generar_siguiente(client, ha, prov["id"])
    _mostrar("la siguiente", siguiente)
    assert D(siguiente["valor_total"]) == D(200000)
    assert D(siguiente["anticipos"]) == D(200000), (
        "la quincena siguiente NO recogió el adelanto soltado: la salida no existe"
    )
    assert D(siguiente["neto_a_pagar"]) == D(0)
    assert _cuadra(siguiente)

    recogido = client.get(f"{ANTICIPOS}/{ant['id']}", headers=ha).json()
    print(f"  marca = {_marca_en_bd(db_session, ant['id'])} . "
          f"liquidacion_id={recogido['liquidacion_id']} . bloqueado={recogido['bloqueado']}")
    assert recogido["liquidacion_id"] == siguiente["id"]
    assert recogido["bloqueado"] is False, (
        "quedó trabado dentro de un borrador: el dueño no puede corregirlo por ninguna "
        "de las dos salidas que le nombra el mensaje"
    )

    r = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "150000"}, headers=ha)
    print(f"  PUT /anticipos (en la siguiente) -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    quedo = client.get(f"{API}/{siguiente['id']}", headers=ha).json()
    _mostrar("corregida", quedo)
    assert D(quedo["anticipos"]) == D(150000)
    assert D(quedo["neto_a_pagar"]) == D(50000)
    assert _cuadra(quedo)
