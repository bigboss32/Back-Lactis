"""ATAQUE 3 — la otra quesera, el otro proveedor, y quien no debería poder.

Corregir una quincena YA PAGADA es la única puerta del sistema que reescribe plata que
ya salió de la caja. Todo lo que entra por ahí —el id de la liquidación, el id de cada
día suelto, el id de cada renglón de precio— viene del navegador, y un id es un número
que se puede teclear. Lo que se mide aquí es que ninguno de esos números sirva para
sacar leche de un lado y pagarla en otro.

Los cuatro robos que se intentan, con la plata en la mano:

  (1) LA OTRA QUESERA. La Quesera B previsualiza, corrige y lee las correcciones de una
      quincena de la Quesera A. Tiene que ser 404 y NO 403: un 403 le confirmaría a un
      competidor que ese documento existe, que es la mitad del dato.
  (2) LA LECHE DE LA OTRA QUESERA. Un día suelto de B metido en el `recepciones_a_incluir`
      de A —y al revés, un día de A metido en una quincena de B—. Si entrara, la leche de
      un tenant quedaría marcada como pagada en el comprobante del otro.
  (3) EL OTRO PROVEEDOR DE LA MISMA CASA. Es el más caro y el más fácil de que pase de
      verdad, porque los dos ids conviven en la misma pantalla: el día de $300.000 de
      Marleny entrando en el comprobante de Libardo. Libardo cobraría leche que no
      entregó y Marleny quedaría con su día MARCADO como ya liquidado — o sea, no vuelve
      a salir en ninguna corrida y nadie se lo paga nunca.
  (4) LA FECHA DE AFUERA. Un día del 20/06 entrando en la quincena del 01 al 15: el
      comprobante diría que cubre del 1 al 15 y cobraría un día de la quincena siguiente,
      que además se lo van a volver a pagar cuando esa quincena se corra.

Y en los cuatro se mide lo mismo DESPUÉS del rebote: la liquidación atacada quedó con el
MISMO valor_total, la MISMA versión y SIN renglón de corrección. Un guardia que rebota
pero deja el día marcado es peor que no tener guardia, porque el daño queda invisible.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import PASSWORD, auth_headers

API = "/api/v1/liquidaciones"

PERIODO = {"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15"}


def D(v):
    return Decimal(str(v))


def detalle(respuesta):
    return respuesta.json().get("error", {}).get("detail", "")


# --------------------------------------------------------------------- montaje
def crear_proveedor(client, h, nombre, precio="2000"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def anotar_dia(client, h, proveedor, fecha, litros):
    """Un día de leche. Sirve para el montaje y para fabricar días sueltos."""
    r = client.post(
        "/api/v1/recepciones",
        json={
            "fecha": fecha,
            "proveedor_id": proveedor["id"],
            "cantidad_litros": str(litros),
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def quincena_pagada(client, h, nombre, *, litros="250", precio="2000"):
    """Un productor con su quincena del 01 al 15 GENERADA, APROBADA Y PAGADA.

    Con los valores por defecto son las cifras del dueño: 250 L × $2.000 = $500.000
    entregados completos. Es el punto de partida de la corrección: sin estado 'pagada'
    la puerta ni siquiera se abre.
    """
    proveedor = crear_proveedor(client, h, nombre, precio)
    anotar_dia(client, h, proveedor, "2026-06-02", litros)
    generadas = client.post(
        f"{API}/generar", json={**PERIODO, "tipo": "proveedor"}, headers=h
    ).json()["generadas"]
    liq = next(x for x in generadas if x["proveedor_id"] == proveedor["id"])
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    liq = pagada.json()
    assert liq["estado"] == "pagada"
    return proveedor, liq


def crear_usuario_con_rol(db_session, empresa, nombre_rol, username):
    """Un usuario de la empresa con uno de los roles que siembra el sistema."""
    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario, UsuarioRol

    rol = db_session.scalars(select(Rol).where(Rol.nombre == nombre_rol)).one()
    usuario = Usuario(
        nombre=username.title(),
        apellido="Prueba",
        correo=f"{username}@test.local",
        username=username,
        hashed_password=hash_password(PASSWORD),
        empresa_id=empresa.id,
    )
    db_session.add(usuario)
    db_session.flush()
    db_session.add(UsuarioRol(usuario_id=usuario.id, rol_id=rol.id, empresa_id=empresa.id))
    db_session.commit()
    return usuario


# ------------------------------------------------------------------ medidores
def foto_de(client, h, liq_id):
    """Las tres cosas que un ataque fallido NO puede haber movido."""
    liq = client.get(f"{API}/{liq_id}", headers=h)
    assert liq.status_code == 200, liq.text
    liq = liq.json()
    correcciones = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert correcciones.status_code == 200, correcciones.text
    return {
        "valor_total": D(liq["valor_total"]),
        "pagado": D(liq["pagado"]),
        "saldo": D(liq["saldo"]),
        "estado": liq["estado"],
        "version": liq["version"],
        "correcciones": len(correcciones.json()),
    }


def exigir_intacta(client, h, liq_id, antes, que_se_intento):
    """Después del rebote: misma plata, misma versión, sin renglón de corrección."""
    despues = foto_de(client, h, liq_id)
    print(f"  {que_se_intento}")
    print(f"    antes   · total {antes['valor_total']} · v{antes['version']} · "
          f"correcciones {antes['correcciones']}")
    print(f"    después · total {despues['valor_total']} · v{despues['version']} · "
          f"correcciones {despues['correcciones']}")
    assert despues == antes, f"el intento fallido movió algo: {que_se_intento}"


def dia_esta_suelto(db_session, recepcion_id):
    """¿Ese día sigue SIN dueño? Es la marca que decide si alguien lo va a cobrar."""
    db_session.expire_all()
    recepcion = db_session.scalars(
        select(RecepcionLeche).where(RecepcionLeche.id == uuid.UUID(str(recepcion_id)))
    ).one()
    return recepcion.liquidacion_id is None


# ===========================================================================
# (1) La Quesera B no ve, no previsualiza, no corrige y no lee las correcciones
# ===========================================================================
def test_la_quesera_b_no_toca_la_quincena_pagada_de_a_y_recibe_404_no_403(
    client, base_datos
):
    """Las TRES puertas de la corrección, con el id de la Quesera A en la mano.

    El 404 no es cosmético. La administradora de la Quesera B tiene el permiso
    'liquidaciones:administrar' —en SU empresa—, así que el guardia de permisos la deja
    pasar y lo único que la detiene es el filtro por fila. Si eso devolviera 403, un
    competidor sabría que ese id existe y de paso que es una quincena de proveedor
    pagada; con 404 el documento simplemente no existe para ella.

    La plata: son $500.000 entregados a un productor de otra empresa. Una corrección
    ajena podría subirle el total a $680.000 y dejarle a la Quesera A un saldo por pagar
    que nadie autorizó.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    proveedor_a, liq_a = quincena_pagada(client, h_a, "Libardo")
    # Un día del 12/06 anotado tarde: $180.000 de leche de verdad, suelta y a la vista.
    dia_olvidado = anotar_dia(client, h_a, proveedor_a, "2026-06-12", "90")

    antes = foto_de(client, h_a, liq_a["id"])
    print("\n===== (1) LA QUESERA B CONTRA LA QUINCENA DE LA QUESERA A =====")
    print(f"  quincena de A: total {antes['valor_total']} · pagado {antes['pagado']} · "
          f"v{antes['version']}")

    payload = {
        "motivo": "se me olvido un dia",
        "recepciones_a_incluir": [dia_olvidado["id"]],
    }
    intentos = {
        "previsualizar": client.post(
            f"{API}/{liq_a['id']}/corregir/previsualizar", json=payload, headers=h_b
        ),
        "corregir": client.post(
            f"{API}/{liq_a['id']}/corregir", json=payload, headers=h_b
        ),
        "leer las correcciones": client.get(
            f"{API}/{liq_a['id']}/correcciones", headers=h_b
        ),
    }
    for etiqueta, r in intentos.items():
        print(f"  admin.b intenta {etiqueta}: {r.status_code} · {detalle(r)}")
        assert r.status_code == 404, f"{etiqueta} no rebotó con 404: {r.text}"
        assert r.status_code != 403, (
            f"{etiqueta} devolvió 403 y eso le confirma a la Quesera B que el "
            "documento existe"
        )

    exigir_intacta(client, h_a, liq_a["id"], antes, "después de los tres intentos de B")


# ===========================================================================
# (2) La leche de la otra quesera no cruza, en ninguna de las dos direcciones
# ===========================================================================
def test_un_dia_suelto_de_la_quesera_b_no_aparece_en_los_dias_sueltos_de_a(
    client, base_datos
):
    """El diálogo de corrección de A solo puede ofrecer días de A.

    La lista de días sueltos es una casilla que el dueño MARCA: lo que aparezca ahí es
    lo que va a entrar. Si se colara un día de la Quesera B —mismo período, misma
    fecha— el dueño de A lo marcaría de buena fe y le pagaría a su productor $180.000
    de leche que recibió otra empresa.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    proveedor_a, liq_a = quincena_pagada(client, h_a, "Libardo")
    dia_de_a = anotar_dia(client, h_a, proveedor_a, "2026-06-12", "90")

    # La Quesera B tiene su propio productor con un día suelto EL MISMO 12/06 y por la
    # MISMA plata: si el filtro por empresa se cayera, los dos saldrían mezclados y
    # serían indistinguibles en la pantalla.
    proveedor_b = crear_proveedor(client, h_b, "Libardo")
    dia_de_b = anotar_dia(client, h_b, proveedor_b, "2026-06-12", "90")

    r = client.post(
        f"{API}/{liq_a['id']}/corregir/previsualizar",
        json={"motivo": "revisando que hay suelto", "recepciones_a_incluir": []},
        headers=h_a,
    )
    assert r.status_code == 200, r.text
    sueltos = r.json()["dias_sueltos"]

    print("\n===== (2a) LOS DÍAS SUELTOS QUE VE LA QUESERA A =====")
    for d in sueltos:
        print(f"  {d['fecha']} · {d['litros']} L × ${d['precio_litro']} = {d['valor']}")
    ids = {d["recepcion_id"] for d in sueltos}
    assert ids == {dia_de_a["id"]}, "se coló un día que no es de la Quesera A"
    assert dia_de_b["id"] not in ids
    # Y la plata que ofrece son $180.000, no $360.000: no se están sumando los dos.
    assert sum(D(d["valor"]) for d in sueltos) == D(180000)


def test_la_leche_de_la_otra_quesera_no_se_puede_meter_en_ninguna_direccion(
    client, base_datos, db_session
):
    """El robo directo: mandar el id de una recepción del OTRO tenant en el cuerpo.

    Se prueban las DOS direcciones, porque el daño no es el mismo:
      · A mete un día de B  → A le paga $180.000 a su productor por leche que no
        recibió, y el día de B queda marcado como liquidado: el productor de B no
        vuelve a verlo en ninguna corrida y nadie se lo paga jamás.
      · B mete un día de A  → lo mismo al revés, y además le vacía a la Quesera A un
        día de su próxima quincena sin que A se entere nunca.

    Rebota con 422 (BusinessError) y —lo que de verdad se mide— NO ESCRIBE NADA: ni la
    marca en la recepción ajena, ni la versión, ni el renglón de corrección.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    proveedor_a, liq_a = quincena_pagada(client, h_a, "Libardo")
    proveedor_b, liq_b = quincena_pagada(client, h_b, "Marleny")
    dia_de_a = anotar_dia(client, h_a, proveedor_a, "2026-06-12", "90")
    dia_de_b = anotar_dia(client, h_b, proveedor_b, "2026-06-12", "90")

    antes_a = foto_de(client, h_a, liq_a["id"])
    antes_b = foto_de(client, h_b, liq_b["id"])
    print("\n===== (2b) MANDAR LA RECEPCIÓN DEL OTRO TENANT EN EL CUERPO =====")
    print(f"  quincena A: {antes_a['valor_total']} · quincena B: {antes_b['valor_total']}")

    r_a = client.post(
        f"{API}/{liq_a['id']}/corregir",
        json={"motivo": "metiendo el dia de la otra quesera",
              "recepciones_a_incluir": [dia_de_b["id"]]},
        headers=h_a,
    )
    print(f"  A intenta llevarse el día de B: {r_a.status_code} · {detalle(r_a)}")
    assert r_a.status_code == 422, r_a.text

    r_b = client.post(
        f"{API}/{liq_b['id']}/corregir",
        json={"motivo": "metiendo el dia de la otra quesera",
              "recepciones_a_incluir": [dia_de_a["id"]]},
        headers=h_b,
    )
    print(f"  B intenta llevarse el día de A: {r_b.status_code} · {detalle(r_b)}")
    assert r_b.status_code == 422, r_b.text

    exigir_intacta(client, h_a, liq_a["id"], antes_a, "quincena de A tras el intento")
    exigir_intacta(client, h_b, liq_b["id"], antes_b, "quincena de B tras el intento")

    # Y LA MARCA, que es lo único que decide si un día se vuelve a cobrar o se pierde.
    assert dia_esta_suelto(db_session, dia_de_a["id"]), (
        "el día de $180.000 de la Quesera A quedó marcado por el intento de B"
    )
    assert dia_esta_suelto(db_session, dia_de_b["id"]), (
        "el día de $180.000 de la Quesera B quedó marcado por el intento de A"
    )
    print("  los dos días de $180.000 siguen sueltos y cobrables por su dueño")


# ===========================================================================
# (3) El otro proveedor DE LA MISMA CASA — el caso caro
# ===========================================================================
def test_el_dia_de_otro_proveedor_de_la_misma_quesera_no_entra_en_esta_quincena(
    client, base_datos, db_session
):
    """Los dos ids viven en la misma pantalla del mismo usuario: aquí el filtro por
    empresa no protege nada y lo único que separa la plata es `proveedor_id`.

    Con cifras: la quincena de Libardo va en $500.000 y ya se le entregaron $500.000.
    Marleny tiene suelto un día de 150 L × $2.000 = $300.000. Si ese día entrara al
    comprobante de Libardo:
      · Libardo pasaría a $800.000 y le quedarían $300.000 por cobrar que no entregó;
      · y el día de Marleny quedaría con `liquidacion_id` puesto, o sea DESAPARECE de
        los días por liquidar: su quincena saldría en $0 y esos $300.000 no se los
        pagaría nadie, ni ese mes ni nunca.
    """
    h = auth_headers(client, "admin.a")
    _, liq_libardo = quincena_pagada(client, h, "Libardo")
    marleny = crear_proveedor(client, h, "Marleny")
    dia_de_marleny = anotar_dia(client, h, marleny, "2026-06-12", "150")

    antes = foto_de(client, h, liq_libardo["id"])
    print("\n===== (3) EL DÍA DE MARLENY EN EL COMPROBANTE DE LIBARDO =====")
    print(f"  Libardo: total {antes['valor_total']} · pagado {antes['pagado']}")
    print("  Marleny tiene suelto el 12/06: 150 L × $2.000 = $300.000")

    # Primero: el diálogo NI SIQUIERA se lo ofrece. Es la primera línea de defensa y la
    # única que el dueño ve.
    previa = client.post(
        f"{API}/{liq_libardo['id']}/corregir/previsualizar",
        json={"motivo": "que hay suelto de Libardo", "recepciones_a_incluir": []},
        headers=h,
    )
    assert previa.status_code == 200, previa.text
    ofrecidos = previa.json()["dias_sueltos"]
    print(f"  días que el diálogo de Libardo ofrece: {len(ofrecidos)}")
    assert ofrecidos == [], "el diálogo de Libardo está ofreciendo leche de Marleny"

    # Y segundo: mandándolo a mano, que es lo que hace quien conoce el endpoint.
    r = client.post(
        f"{API}/{liq_libardo['id']}/corregir",
        json={"motivo": "el dia de la vecina",
              "recepciones_a_incluir": [dia_de_marleny["id"]]},
        headers=h,
    )
    print(f"  mandándolo a mano: {r.status_code} · {detalle(r)}")
    assert r.status_code == 422, r.text

    exigir_intacta(client, h, liq_libardo["id"], antes, "Libardo tras el intento")
    assert dia_esta_suelto(db_session, dia_de_marleny["id"]), (
        "el día de $300.000 de Marleny quedó marcado en el comprobante de Libardo: "
        "ella no lo vuelve a cobrar nunca"
    )
    print("  los $300.000 de Marleny siguen sueltos y le van a salir en su quincena")


# ===========================================================================
# (4) La fecha de afuera
# ===========================================================================
def test_un_dia_fuera_del_periodo_no_entra_aunque_sea_del_mismo_proveedor(
    client, base_datos, db_session
):
    """Mismo productor, misma empresa, día suelto — pero del 20/06, y esta quincena
    cubre del 01 al 15.

    Si entrara, pasarían dos cosas y las dos cuestan plata: el comprobante diría
    "PERÍODO 01/06 al 15/06" y cobraría adentro un día que no le corresponde —el dueño
    suma la columna contra su cuaderno y no le cuadra—, y ese día quedaría marcado, así
    que cuando corra la quincena del 16 al 30 le va a faltar. $200.000 en el papel
    equivocado.
    """
    h = auth_headers(client, "admin.a")
    proveedor, liq = quincena_pagada(client, h, "Libardo")
    dentro = anotar_dia(client, h, proveedor, "2026-06-12", "90")     # $180.000, sí
    afuera = anotar_dia(client, h, proveedor, "2026-06-20", "100")    # $200.000, no

    antes = foto_de(client, h, liq["id"])
    print("\n===== (4) EL DÍA DEL 20/06 EN LA QUINCENA DEL 01 AL 15 =====")
    previa = client.post(
        f"{API}/{liq['id']}/corregir/previsualizar",
        json={"motivo": "que hay suelto", "recepciones_a_incluir": []},
        headers=h,
    )
    assert previa.status_code == 200, previa.text
    sueltos = previa.json()["dias_sueltos"]
    for d in sueltos:
        print(f"  ofrecido: {d['fecha']} · {d['valor']}")
    assert {d["recepcion_id"] for d in sueltos} == {dentro["id"]}, (
        "el diálogo está ofreciendo un día de fuera del período"
    )

    r = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "el dia del 20", "recepciones_a_incluir": [afuera["id"]]},
        headers=h,
    )
    print(f"  mandando el 20/06 a mano: {r.status_code} · {detalle(r)}")
    assert r.status_code == 422, r.text

    exigir_intacta(client, h, liq["id"], antes, "la quincena tras el intento")
    assert dia_esta_suelto(db_session, afuera["id"]), (
        "el día del 20/06 quedó preso en la quincena del 01 al 15 y le va a faltar a "
        "la quincena siguiente"
    )


def test_meter_el_dia_bueno_y_el_malo_juntos_no_deja_entrar_ni_el_bueno(
    client, base_datos, db_session
):
    """La corrección es UNA operación, no una lista de intentos.

    Si el guardia rebotara solo el día malo y dejara pasar el bueno, el comprobante
    quedaría en $680.000 con una versión nueva y un renglón de corrección que dice una
    cosa mientras el usuario pidió otra — y el dueño no tendría cómo saber cuál de los
    dos días entró. O entran los dos o no entra ninguno.
    """
    h = auth_headers(client, "admin.a")
    proveedor, liq = quincena_pagada(client, h, "Libardo")
    bueno = anotar_dia(client, h, proveedor, "2026-06-12", "90")      # $180.000
    marleny = crear_proveedor(client, h, "Marleny")
    malo = anotar_dia(client, h, marleny, "2026-06-13", "150")        # $300.000, ajeno

    antes = foto_de(client, h, liq["id"])
    r = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "los dos dias", "recepciones_a_incluir": [bueno["id"], malo["id"]]},
        headers=h,
    )
    print("\n===== (5) EL DÍA BUENO Y EL AJENO EN LA MISMA PETICIÓN =====")
    print(f"  respuesta: {r.status_code} · {detalle(r)}")
    assert r.status_code == 422, r.text

    exigir_intacta(client, h, liq["id"], antes, "la quincena tras el intento mixto")
    assert dia_esta_suelto(db_session, bueno["id"]), (
        "el día bueno entró a medias: la corrección no es atómica"
    )
    assert dia_esta_suelto(db_session, malo["id"])


# ===========================================================================
# (6) El renglón de precio de OTRO comprobante
# ===========================================================================
def test_el_detalle_de_otra_quincena_no_deja_corregirle_el_precio(
    client, base_datos, db_session
):
    """`precios` viaja con `detalle_id`, y ese id se puede cruzar.

    Los dos cruces posibles, y los dos tienen que rebotar sin escribir:
      · el renglón de OTRO proveedor de la misma casa (404: no pertenece a la
        liquidación);
      · el renglón de una liquidación de la Quesera B.

    La plata: el renglón de Marleny va en 250 L × $2.000 = $500.000. Bajárselo a $1.000
    desde el comprobante de Libardo le quitaría $250.000 a una productora que ya recibió
    su papel, y de paso dejaría la recepción del día corregida contra un comprobante que
    no la contiene.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, liq_libardo = quincena_pagada(client, h_a, "Libardo")
    _, liq_marleny = quincena_pagada(client, h_a, "Marleny")
    _, liq_b = quincena_pagada(client, h_b, "Ajeno")

    antes_libardo = foto_de(client, h_a, liq_libardo["id"])
    antes_marleny = foto_de(client, h_a, liq_marleny["id"])
    antes_b = foto_de(client, h_b, liq_b["id"])

    print("\n===== (6) EL RENGLÓN DE PRECIO DE OTRO COMPROBANTE =====")
    for etiqueta, ajena in (("de Marleny", liq_marleny), ("de la Quesera B", liq_b)):
        renglon = ajena["detalles"][0]
        r = client.post(
            f"{API}/{liq_libardo['id']}/corregir",
            json={
                "motivo": "bajandole el precio al vecino",
                "precios": [{"detalle_id": renglon["id"], "precio_litro": "1000"}],
            },
            headers=h_a,
        )
        print(f"  renglón {etiqueta} contra el comprobante de Libardo: "
              f"{r.status_code} · {detalle(r)}")
        assert r.status_code == 404, r.text

    exigir_intacta(client, h_a, liq_libardo["id"], antes_libardo, "Libardo")
    exigir_intacta(client, h_a, liq_marleny["id"], antes_marleny, "Marleny")
    exigir_intacta(client, h_b, liq_b["id"], antes_b, "la quincena de la Quesera B")


# ===========================================================================
# (7) Quién no debería poder: la corrección tiene su propia puerta
# ===========================================================================
def test_corregir_exige_administrar_y_no_le_alcanza_a_compras_ni_a_consulta(
    client, base_datos, db_session
):
    """El rol Compras arma liquidaciones y edita precios en borrador, pero NO entrega
    plata: en liquidaciones tiene crear, editar, consultar, exportar e imprimir, y no
    'administrar'. Consulta solo mira.

    Si la corrección se hubiera colgado del `PUT /{id}` de siempre —que pide 'editar'—
    Compras podría subirle el precio a una quincena YA PAGADA sin que interviniera nadie
    con permiso de plata: 250 L de $2.000 a $2.400 son $100.000 más sobre un comprobante
    que ya se entregó.

    Consultar las correcciones sí es de su oficio: es el papel que explica por qué el
    comprobante del productor dice otra cifra.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Compras", "comprador")
    crear_usuario_con_rol(db_session, empresa, "Consulta", "mirona")

    h = auth_headers(client, "admin.a")
    proveedor, liq = quincena_pagada(client, h, "Libardo")
    dia_suelto = anotar_dia(client, h, proveedor, "2026-06-12", "90")
    antes = foto_de(client, h, liq["id"])

    payload = {"motivo": "se me olvido un dia",
               "recepciones_a_incluir": [dia_suelto["id"]]}

    print("\n===== (7) QUIÉN NO DEBERÍA PODER =====")
    for usuario in ("comprador", "mirona"):
        hu = auth_headers(client, usuario)
        for ruta in (f"{API}/{liq['id']}/corregir/previsualizar",
                     f"{API}/{liq['id']}/corregir"):
            r = client.post(ruta, json=payload, headers=hu)
            print(f"  {usuario} → {ruta.rsplit('/', 1)[-1]}: {r.status_code} · "
                  f"{detalle(r)}")
            assert r.status_code == 403, r.text
            assert "administrar" in detalle(r)

    # Pero LEER las correcciones sí, que pide 'consultar'.
    for usuario in ("comprador", "mirona"):
        hu = auth_headers(client, usuario)
        r = client.get(f"{API}/{liq['id']}/correcciones", headers=hu)
        print(f"  {usuario} lee las correcciones: {r.status_code}")
        assert r.status_code == 200, r.text

    exigir_intacta(client, h, liq["id"], antes, "la quincena tras los intentos sin permiso")
    assert dia_esta_suelto(db_session, dia_suelto["id"])


# ===========================================================================
# (8) El atajo del header: elegir la empresa a mano
# ===========================================================================
def test_el_header_de_empresa_no_es_una_puerta_para_entrar_a_la_otra_quesera(
    client, base_datos, db_session
):
    """`X-Empresa-Id` escoge la empresa activa, así que es EL primer sitio donde alguien
    va a intentar entrar: si el filtro por fila se apoya en ese header y el header no se
    valida, todo el aislamiento se cae de una sola vez.

    Se miden los tres casos que existen:
      · admin.b mandando el id de la Quesera A → 403, no pertenece. Y ojo: aquí el 403
        es correcto y no filtra nada, porque no habla del documento sino de la
        membresía, y B ya sabe a qué empresas pertenece.
      · el superadmin SIN header → 422 con instrucción, no un 500 ni —mucho peor— una
        corrección aplicada sin contexto de empresa.
      · el superadmin CON el header de la Quesera B contra una quincena de A → 404: la
        empresa activa manda, aunque quien pregunte lo pueda todo.

    En los tres, los $500.000 de la Quesera A se quedan quietos.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    h_s = auth_headers(client, "superadmin")
    proveedor, liq = quincena_pagada(client, h_a, "Libardo")
    dia = anotar_dia(client, h_a, proveedor, "2026-06-12", "90")
    empresa_a = str(base_datos["empresa_a"].id)
    empresa_b = str(base_datos["empresa_b"].id)

    antes = foto_de(client, h_a, liq["id"])
    payload = {"motivo": "entrando por el header", "recepciones_a_incluir": [dia["id"]]}

    print("\n===== (8) EL HEADER DE EMPRESA =====")
    casos = (
        ("admin.b con X-Empresa-Id de A", {**h_b, "X-Empresa-Id": empresa_a}, 403),
        ("superadmin sin header", h_s, 422),
        ("superadmin con X-Empresa-Id de B", {**h_s, "X-Empresa-Id": empresa_b}, 404),
    )
    for etiqueta, headers, esperado in casos:
        r = client.post(f"{API}/{liq['id']}/corregir", json=payload, headers=headers)
        print(f"  {etiqueta}: {r.status_code} · {detalle(r)}")
        assert r.status_code == esperado, f"{etiqueta}: {r.text}"

    exigir_intacta(client, h_a, liq["id"], antes, "la quincena de A tras los tres intentos")
    assert dia_esta_suelto(db_session, dia["id"])


# ===========================================================================
# (9) EL MISMO DÍA MANDADO DOS VECES — DEFECTO REPRODUCIDO
# ===========================================================================
# No es multiempresa, pero entra por la misma puerta y por el mismo campo:
# `recepciones_a_incluir` es una lista que llega del navegador y nadie la deduplica.
# Un doble clic en la casilla, una pantalla que agrega en vez de alternar, o un reintento
# que junta los dos arreglos, y el mismo id viaja dos veces.
#
# `_simular_correccion` construye `entran` recorriendo la lista y APILANDO uno por cada
# id, y después suma `list(actuales.values()) + entran`. El día repetido se cuenta DOS
# VECES en la simulación. La escritura, en cambio, marca la recepción (dos veces, que es
# lo mismo que una) y recalcula desde la base, así que escribe el valor correcto.
#
# Resultado: la pantalla y el papel dicen cifras distintas, que es exactamente lo que el
# docstring de `_simular_correccion` promete que no puede pasar.
def test_el_dialogo_y_el_boton_tienen_que_decir_la_misma_cifra(client, base_datos):
    """La previsualización existe para que el dueño compare la cifra que va a quedar
    contra el papel que tiene al lado. Si la simulación y la escritura no dan lo mismo,
    ese paso no protege: aprueba $860.000 y se escriben $680.000.
    """
    h = auth_headers(client, "admin.a")
    proveedor, liq = quincena_pagada(client, h, "Libardo")
    dia = anotar_dia(client, h, proveedor, "2026-06-12", "90")

    payload = {
        "motivo": "el mismo dia mandado dos veces",
        "recepciones_a_incluir": [dia["id"], dia["id"]],
    }
    previa = client.post(
        f"{API}/{liq['id']}/corregir/previsualizar", json=payload, headers=h
    ).json()
    escrita = client.post(f"{API}/{liq['id']}/corregir", json=payload, headers=h).json()

    print("\n===== (9) EL MISMO DÍA DOS VECES: DIÁLOGO vs PAPEL =====")
    print(f"  el diálogo anuncia · total {previa['valor_total_despues']} · "
          f"queda por entregar {previa['queda_por_entregar']}")
    print(f"  el papel sale en   · total {escrita['valor_total']} · "
          f"saldo {escrita['saldo']}")

    assert D(previa["valor_total_despues"]) == D(escrita["valor_total"]), (
        "el diálogo anunció un total distinto del que se escribió"
    )
    assert D(previa["saldo_despues"]) == D(escrita["saldo"])
    # Y la cifra correcta es UNA sola vez el día: $500.000 + $180.000.
    assert D(escrita["valor_total"]) == D(680000)


def test_el_desglose_de_la_correccion_suma_exacto_lo_que_subio_el_comprobante(
    client, base_datos
):
    """LA REGLA DE LA CASA sobre el papel nuevo: los días que dice haber agregado tienen
    que sumar EXACTO la diferencia entre el total de antes y el de después. Es la única
    forma de emparejar el comprobante viejo con el nuevo sumando, que es como el dueño
    lo verifica.
    """
    h = auth_headers(client, "admin.a")
    proveedor, liq = quincena_pagada(client, h, "Libardo")
    dia = anotar_dia(client, h, proveedor, "2026-06-12", "90")

    r = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "el mismo dia mandado dos veces",
              "recepciones_a_incluir": [dia["id"], dia["id"]]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    correccion = client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()[0]

    subio = D(correccion["valor_total_despues"]) - D(correccion["valor_total_antes"])
    suma_del_desglose = sum(D(d["valor"]) for d in correccion["dias_agregados"])

    print("\n===== (10) EL DESGLOSE DEL RENGLÓN DE CORRECCIÓN =====")
    for d in correccion["dias_agregados"]:
        print(f"  agregado: {d['fecha']} · {d['litros']} L · {d['valor']}")
    print(f"  el comprobante subió       : {subio}")
    print(f"  el desglose dice que subió : {suma_del_desglose}")

    assert suma_del_desglose == subio, (
        "los días agregados no suman lo que subió el comprobante"
    )
