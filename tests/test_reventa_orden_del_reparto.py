"""EL ORDEN DEL REPARTO FIFO: que la misma pregunta dé siempre la misma respuesta.

De este orden sale PLATA: cuando una venta se lleva kilos, el reparto decide de
cuál compra salen, y con eso a qué productor se le carga el costo y cuánta
ganancia le queda. El orden lo fijan las tres llaves de
`app/modules/reventa/repository.py` (`llave_cronologica_compra`, `_venta` y
`_ajuste`), y lo que se prueba aquí es exactamente eso:

1. Que el resultado NO TIEMBLE: la misma situación registrada veinte veces da las
   veinte veces las mismas cifras. Antes no: el último criterio de desempate era
   el `id`, que es un UUID ALEATORIO, así que a cuál productor se le consumían los
   kilos primero lo decidía la suerte y esta prueba habría fallado una de cada dos
   veces por lote.
2. Que cuando dos compras del mismo día tienen la MISMA hora de registro —que es
   lo que deja una migración o una carga masiva— haya una regla dicha y no un
   sorteo: manda el nombre del productor.
3. Que la hora de registro sirva para desempatar, que es lo que la hace una llave
   de orden y no un adorno: dos facturas registradas seguidas no pueden compartir
   el instante.
4. Que entre los ajustes del mismo día vaya primero el que pasa queso a borona y
   después la merma.

Las cifras se imprimen porque el dueño las cuadra a mano con calculadora.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.modules.reventa.models import (
    CompraQueso,
    ConversionBorona,
    DocumentoReventa,
    VentaQueso,
)
from tests.conftest import auth_headers

API = "/api/v1/reventa"


def D(valor):
    return Decimal(str(valor))


def compra(client, headers, **datos):
    r = client.post(f"{API}/compras", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, headers, **datos):
    r = client.post(f"{API}/ventas", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def ajuste(client, headers, **datos):
    r = client.post(f"{API}/conversiones", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def panel(client, headers, **params):
    r = client.get(f"{API}/lotes", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def por_fecha(p):
    return {lote["fecha"]: lote for lote in p["lotes"]}


def por_productor(lote):
    """Las compras del lote agrupadas por productor. En estas pruebas cada
    productor tiene UNA compra por lote, así que la fila es suya."""
    return {c["productor"]: c for c in lote["detalle_compras"]}


def aplanar_la_hora(db_session, modelo, momento):
    """Le pone a TODAS las filas de esa tabla la misma hora de registro.

    Es la situación de las filas que entran por una migración o por una carga
    masiva: quedan todas con el mismo instante, y ahí la hora ya no puede decir
    cuál se registró primero. Se fuerza a mano porque es el único caso en el que se
    ejerce el desempate de último recurso, y tiene que quedar fijado: en las dos
    bases y no por el `id`.
    """
    for fila in db_session.scalars(select(modelo)).all():
        fila.created_at = momento
    db_session.commit()


# ===========================================================================
# 1. EL REPARTO NO TIEMBLA
# ===========================================================================
def test_el_reparto_da_lo_mismo_las_veinte_veces(client, base_datos):
    """VEINTE veces la misma situación, con filas nuevas cada vez (y por lo tanto
    con ids nuevos), y las veinte tienen que dar las mismas cifras.

    La situación es la del defecto: dos compras del MISMO DÍA de dos productores
    distintos y una venta que se lleva parte de las dos. Con el desempate por `id`
    —un UUID aleatorio— cada lote era una moneda al aire: la ganancia de Patricia
    y la de Sebastián se cambiaban de puesto sin que nadie tocara nada.

    Cada tanda es un lote aparte y se vende COMPLETO (500 kg comprados, 500
    vendidos), así que ninguna tanda le deja inventario a la siguiente y las veinte
    son de verdad la misma pregunta.

    Los datos se registran en el mismo orden que fija la llave (Patricia primero, y
    "Patricia" también va primero por nombre), así que la prueba exige lo mismo
    empate o no empate el reloj: es la respuesta correcta por las dos vías.
    """
    h = auth_headers(client, "admin.a")
    primeros_dias = []
    for tanda in range(20):
        dia = date(2026, 3, 1) + timedelta(days=tanda * 3)
        compra(client, h, fecha=dia.isoformat(), productor="Patricia Ospina",
               kilos_brutos="200", precio_kilo="15000")
        compra(client, h, fecha=dia.isoformat(), productor="Sebastián Ruiz",
               kilos_brutos="300", precio_kilo="18000")
        # 450 kg: se llevan los 200 de Patricia y 250 de Sebastián
        venta(client, h, fecha=(dia + timedelta(days=1)).isoformat(),
              cliente="Alba Nieto", kilos="450", precio_kilo="21000",
              pagada_de_contado=True)
        # y los 50 que quedan, a otro precio, para cerrar el lote
        venta(client, h, fecha=(dia + timedelta(days=2)).isoformat(),
              cliente="Alba Nieto", kilos="50", precio_kilo="30000",
              pagada_de_contado=True)
        primeros_dias.append(dia)

    lotes = por_fecha(panel(client, h))
    print("\n===== 20 TANDAS IGUALES: a quién se le consumen los kilos =====")
    distintos = set()
    for dia in primeros_dias:
        lote = lotes[dia.isoformat()]
        filas = por_productor(lote)
        patricia, sebastian = filas["Patricia Ospina"], filas["Sebastián Ruiz"]
        distintos.add(
            (patricia["kilos_vendidos"], patricia["ingresos"],
             sebastian["kilos_vendidos"], sebastian["ingresos"])
        )
        # Patricia se compró primero: sus 200 kg salen primero, todos a $21.000
        assert D(patricia["kilos_vendidos"]) == 200
        assert D(patricia["ingresos"]) == 200 * 21000
        assert D(patricia["ganancia"]) == 200 * 21000 - 200 * 15000
        # Sebastián: 250 kg a $21.000 y los 50 últimos a $30.000
        assert D(sebastian["kilos_vendidos"]) == 300
        assert D(sebastian["ingresos"]) == 250 * 21000 + 50 * 30000
        assert D(sebastian["ganancia"]) == 250 * 21000 + 50 * 30000 - 300 * 18000
        # Y el lote cuadra peso a peso
        assert D(lote["costo_vendido"]) + D(lote["costo_sin_vender"]) == D(lote["costo_total"])

    print(f"  respuestas distintas en 20 tandas: {len(distintos)}")
    for r in distintos:
        print(f"    Patricia {r[0]} kg / ${r[1]}   Sebastián {r[2]} kg / ${r[3]}")
    assert len(distintos) == 1, "el reparto dio dos respuestas distintas a la misma pregunta"


# ===========================================================================
# 2. LA REGLA CUANDO LA HORA EMPATA
# ===========================================================================
def test_con_la_misma_hora_manda_el_nombre_del_productor(client, base_datos, db_session):
    """Dos compras del mismo día CON LA MISMA HORA: se consume primero la del
    productor que va primero por nombre.

    POR QUÉ ESTA REGLA. Cuando la hora empata no existe en los datos la respuesta a
    "cuál se registró primero", así que hay que escoger una y que sea siempre la
    misma. Se escogió el nombre porque es lo único de esas dos filas que el dueño
    ve en la pantalla del lote y puede seguir con el dedo: si ve "Patricia" y
    "Sebastián", sabe sin preguntarle a nadie que los kilos salieron primero de
    Patricia. Y no le quita ni le regala plata a ninguno de los dos: el lote vale
    lo mismo y la ganancia del período es la misma; lo que la regla garantiza es
    que la cifra de cada uno sea la misma hoy y el mes que viene.

    Aquí se registra a Sebastián PRIMERO y a Patricia después, y aun así se consume
    primero la de Patricia: la prueba falla si el desempate volviera a ser el `id`
    (aleatorio) o el orden de escritura (que con la hora aplanada ya no se puede
    saber).
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-03-01", productor="Sebastián Ruiz",
           kilos_brutos="300", precio_kilo="18000")
    compra(client, h, fecha="2026-03-01", productor="Patricia Ospina",
           kilos_brutos="200", precio_kilo="15000")
    aplanar_la_hora(
        db_session, CompraQueso, datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
    )
    venta(client, h, fecha="2026-03-02", cliente="Alba Nieto", kilos="250",
          precio_kilo="21000", pagada_de_contado=True)

    lote = panel(client, h)["lotes"][0]
    print("\n===== MISMA HORA: 250 kg vendidos de dos compras del 1 de marzo =====")
    for c in lote["detalle_compras"]:
        print(f"  {c['productor']:18} comprados={c['kilos']} vendidos={c['kilos_vendidos']} "
              f"sin_vender={c['kilos_sin_vender']} ganancia={c['ganancia']}")
    filas = por_productor(lote)
    assert D(filas["Patricia Ospina"]["kilos_vendidos"]) == 200, (
        "con la hora empatada manda el nombre: Patricia va antes que Sebastián"
    )
    assert D(filas["Sebastián Ruiz"]["kilos_vendidos"]) == 50
    # Y el orden en que se le muestran es el mismo en que se consumieron: el dueño
    # lee el lote de arriba hacia abajo.
    assert [c["productor"] for c in lote["detalle_compras"]] == [
        "Patricia Ospina", "Sebastián Ruiz"
    ]
    # El cuadre de siempre: cada peso pagado está en uno de los cuatro destinos
    repartido = (
        D(lote["costo_vendido"]) + D(lote["costo_borona_vendida"])
        + D(lote["costo_merma"]) + D(lote["costo_sin_vender"])
    )
    print(f"  costo_total={lote['costo_total']} y los cuatro destinos suman {repartido}")
    assert repartido == D(lote["costo_total"])


# ===========================================================================
# 3. LA HORA DE REGISTRO SÍ DESEMPATA
# ===========================================================================
def test_dos_facturas_del_mismo_dia_no_comparten_la_hora(client, base_datos, db_session):
    """La hora de registro es una llave de orden, así que dos filas escritas por
    separado no pueden traer el mismo instante.

    Es lo que arregla `HoraDeRegistroMixin`: el reloj de la base daba segundos en
    SQLite y la hora de la transacción en Postgres, así que dos compras seguidas
    empataban y el desempate se iba a criterios que no son "cuál se registró
    primero". Ahora la escribe la aplicación, con microsegundos y estrictamente
    creciente.

    Y los renglones de UNA MISMA factura sí pueden compartir el instante (se
    escriben juntos): a esos los ordena `orden`, que es el puesto que el dueño les
    dio en la factura.
    """
    h = auth_headers(client, "admin.a")
    for productor in ("Uno", "Dos", "Tres"):
        compra(client, h, fecha="2026-03-01", productor=productor,
               kilos_brutos="10", precio_kilo="1000")
    r = client.post(
        f"{API}/documentos",
        json={
            "tipo": "compra", "fecha": "2026-03-01", "tercero": "Cuatro",
            "renglones": [
                {"tipo": "queso", "kilos_brutos": "5", "precio_kilo": "1000"},
                {"tipo": "queso", "kilos_brutos": "6", "precio_kilo": "1000"},
            ],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text

    filas = db_session.scalars(
        select(CompraQueso).order_by(CompraQueso.created_at, CompraQueso.orden)
    ).all()
    print("\n===== HORA DE REGISTRO DE CADA RENGLÓN =====")
    for f in filas:
        print(f"  {f.created_at.isoformat()}  orden={f.orden}  {f.productor}")

    sueltas = [f for f in filas if f.productor in ("Uno", "Dos", "Tres")]
    horas = [f.created_at for f in sueltas]
    assert len(set(horas)) == 3, "tres facturas distintas no pueden compartir el instante"
    assert horas == sorted(horas), "la hora no quedó en el orden en que se registraron"
    assert [f.productor for f in sueltas] == ["Uno", "Dos", "Tres"]

    # Los dos renglones de la misma factura: comparten o no el instante, pero su
    # orden lo fija `orden`, y la llave del reparto lo pone justo después de la hora.
    de_la_factura = [f for f in filas if f.productor == "Cuatro"]
    assert [f.orden for f in de_la_factura] == [0, 1]
    assert [D(f.kilos_netos) for f in de_la_factura] == [D("5.00"), D("6.00")]


def test_la_hora_de_registro_no_cambia_el_esquema():
    """La columna `created_at` de las tres tablas es LA MISMA que la del
    AuditMixin: mismo tipo, mismo `server_default`, misma obligatoriedad.

    Esta prueba es la que sostiene la afirmación más delicada del cambio: que NO
    NECESITA MIGRACIÓN sobre la base de un cliente real. Lo único que se movió es
    quién calcula el valor —la aplicación, con microsegundos y estrictamente
    creciente, en vez del reloj de la base—, y eso no se escribe en el esquema. Si
    alguien algún día le cambia el tipo o le quita el `server_default` a estas
    columnas, esta prueba se pone roja y avisa que ahora sí hay que migrar.
    """
    del_mixin = DocumentoReventa.__table__.c.created_at
    for modelo in (CompraQueso, VentaQueso, ConversionBorona):
        propia = modelo.__table__.c.created_at
        print(f"  {modelo.__tablename__}: {propia.type} "
              f"server_default={propia.server_default.arg} nullable={propia.nullable}")
        assert type(propia.type) is type(del_mixin.type)
        assert propia.type.timezone == del_mixin.type.timezone is True
        assert propia.nullable == del_mixin.nullable is False
        assert str(propia.server_default.arg) == str(del_mixin.server_default.arg)
        # Y la única diferencia: el valor lo pone la aplicación
        assert callable(propia.default.arg), "la hora ya no la calcula la aplicación"
        assert del_mixin.default is None


# ===========================================================================
# 4. LOS AJUSTES DEL MISMO DÍA
# ===========================================================================
def test_con_la_misma_hora_la_borona_va_antes_que_la_merma(client, base_datos, db_session):
    """Dos ajustes del mismo día con la misma hora: primero el que pasa queso a
    borona y después la merma.

    Es el orden de la bodega: primero se separa lo que ya no se vende entero y lo
    que falta al pesar el despacho es la merma. Y decide plata: aquí el ajuste que
    va primero se lleva los kilos de la compra MÁS VIEJA (la de $10.000), así que
    el costo de la merma es $2.000.000 si la borona va primero y $1.000.000 si va
    después. Sin regla, esa cifra la escogía el UUID del ajuste.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-03-01", productor="Patricia Ospina",
           kilos_brutos="100", precio_kilo="10000")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="20000")
    ajuste(client, h, fecha="2026-03-05", kilos="100", destino="merma")
    ajuste(client, h, fecha="2026-03-05", kilos="100", destino="borona",
           precio_kilo="0")
    aplanar_la_hora(
        db_session, ConversionBorona, datetime(2026, 3, 5, 9, 0, 0, tzinfo=timezone.utc)
    )

    lotes = por_fecha(panel(client, h))
    print("\n===== DOS AJUSTES DEL MISMO DÍA CON LA MISMA HORA =====")
    for fecha in sorted(lotes):
        lote = lotes[fecha]
        print(f"  lote {fecha}: a_borona={lote['kilos_a_borona']} "
              f"merma={lote['kilos_merma']} costo_merma={lote['costo_merma']}")
    viejo, nuevo = lotes["2026-03-01"], lotes["2026-03-02"]
    # El paso a borona va primero: se lleva los 100 kg de la compra más vieja
    assert D(viejo["kilos_a_borona"]) == 100 and D(viejo["kilos_merma"]) == 0
    # Y la merma se lleva los de la compra nueva, que costaron el doble
    assert D(nuevo["kilos_merma"]) == 100 and D(nuevo["costo_merma"]) == 2_000_000
    assert D(viejo["costo_merma"]) == 0
