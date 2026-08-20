"""LA REGLA DEL DÍA FIJO, sostenida contra TODO lo que se le puede hacer a un día.

LA REGLA, que es una sola y la da el dueño:

    EN UN DÍA FIJO, EL RENGLÓN VALE LA TARIFA. Punto. NUNCA la suma de las fotos.
    Las fotos de las recepciones son SOLO el reparto de esa cifra entre los
    proveedores de ese día y esa ruta, y su única obligación es sumar exacto el
    renglón.

Este archivo la fija de la única forma en que una regla así se puede fijar: agarra UN
día fijo de CINCO proveedores y le hace, una tras otra, todas las cosas que un día
puede sufrir en la vida real —corregirle los litros a uno, apagarlo, volverlo a
prender, borrarlo, moverlo de fecha, moverlo de ruta, anotar leche nueva, recalcular
dos veces—; y DESPUÉS DE CADA UNA vuelve a exigir lo mismo:

    · el día 16/07 en la ruta "A fabrica" vale $150.000,00; y
    · las fotos de sus recepciones vivas suman EXACTO $150.000,00; y
    · el renglón del comprobante que lo cobra dice esos mismos $150.000,00; y
    · ninguna recepción apagada quedó cargando flete fantasma.

No hay ninguna operación privilegiada: si alguna de ellas puede mover el día, la regla
no está sostenida por el diseño sino por la suerte de que nadie oprima ese botón.

EL DÍA DE LOS CINCO PROVEEDORES, con las cifras del archivo hermano:

    Aurelio      82,00 L
    Marleny     137,45 L
    Gilberto     96,30 L
    Ramiro       60,00 L
    Rosa        124,20 L
                --------
                499,95 L      →   EL DÍA VALE $150.000,00   (no $750.000)

Y LAS DOS COSAS QUE NO SON EL DÍA, que también se revisan acá porque son las dos
formas en que un fijo se escapa por los lados:

    · juntar dos días fijos en uno (moviéndoles la fecha o la ruta) da UN fijo, no la
      suma de los dos;
    · y si de un día fijo no queda NINGUNA recepción viva, el renglón DESAPARECE. No
      se queda en $0,00, que diría que ese viaje no se paga, cuando lo que pasó es
      que ese viaje ya no existe.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    TIPO_TRANSPORTADOR,
    Liquidacion,
    LiquidacionDetalle,
)
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    CINCO,
    EL_DIA,
    FIJO,
    LIQUIDACIONES,
    NAPOLES,
    PROVEEDORES,
    RECEPCIONES,
    D,
    _crear,
    _escenario,
    _liquidar_flete,
    _recibir,
    centavos,
)

CERO = D(0)


def _fecha(texto: str) -> date:
    return date(*(int(x) for x in texto.split("-")))


class Radiografia:
    """Todo lo que hay que mirar de un (día, ruta) para saber si la regla se sostiene.

    Se arma leyendo la base directamente y no la respuesta del API a propósito: lo que
    esta prueba protege es la PLATA GUARDADA —las fotos de las recepciones y los
    renglones de los comprobantes—, que es lo que la contabilidad, la grilla y el papel
    del conductor leen después. Una respuesta puede salir buena y la base quedar mala.
    """

    def __init__(self, db_session, fecha: str, ruta_id: str):
        db_session.expire_all()
        dia = _fecha(fecha)
        ruta = uuid.UUID(ruta_id)
        filas = db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.fecha == dia,
                RecepcionLeche.ruta_id == ruta,
                RecepcionLeche.deleted_at.is_(None),
            )
        ).all()
        # La foto de cada recepción VIVA: es su parte del día. La clave lleva los litros
        # para que el print se pueda leer al lado de la cuenta a mano.
        self.fotos = {
            (str(f.id)[:8], D(f.cantidad_litros)): D(f.valor_transporte)
            for f in filas if f.estado == "activo"
        }
        # Y la de las APAGADAS, que no componen ningún renglón y por lo tanto no pueden
        # estar cargando un peso: cualquier cifra acá es un fijo fantasma.
        self.apagadas = {
            (str(f.id)[:8], D(f.cantidad_litros)): D(f.valor_transporte)
            for f in filas if f.estado != "activo"
        }
        # Los renglones que HOY cobran ese (día, ruta) en cualquier comprobante de flete
        # que siga vivo. Anuladas afuera: esas ya no cobran nada.
        self.renglones = [
            (D(d.valor), d.modo_transporte, bool(d.dia_fijo_ya_cobrado))
            for d in db_session.scalars(
                select(LiquidacionDetalle)
                .join(Liquidacion, Liquidacion.id == LiquidacionDetalle.liquidacion_id)
                .where(
                    Liquidacion.tipo == TIPO_TRANSPORTADOR,
                    Liquidacion.estado != ESTADO_ANULADA,
                    Liquidacion.deleted_at.is_(None),
                    LiquidacionDetalle.fecha == dia,
                    LiquidacionDetalle.ruta_id == ruta,
                    LiquidacionDetalle.deleted_at.is_(None),
                )
            ).all()
        ]

    @property
    def suma_fotos(self) -> Decimal:
        return sum(self.fotos.values(), CERO)

    @property
    def suma_renglones(self) -> Decimal:
        return sum((v for v, _, _ in self.renglones), CERO)

    @property
    def suma_apagadas(self) -> Decimal:
        return sum(self.apagadas.values(), CERO)

    def __str__(self) -> str:
        partes = " + ".join(
            f"{litros_}L=${v}" for (_, litros_), v in sorted(self.fotos.items())
        )
        renglon = " ".join(f"[{m}]${v}" for v, m, _ in self.renglones) or "(sin renglón)"
        fantasma = f"  fantasmas=${self.suma_apagadas}" if self.apagadas else ""
        return f"{partes or '(sin recepciones vivas)'} = ${self.suma_fotos}   {renglon}{fantasma}"


def _exigir_el_dia(db_session, esc, paso: str, *, vale=FIJO, fecha=EL_DIA, ruta=None):
    """LA EXIGENCIA, la misma después de cada operación. Devuelve la radiografía.

    Las cuatro cosas que tienen que ser ciertas a la vez, y si alguna falla el mensaje
    dice cuál operación la rompió (`paso`), porque con quince pasos encadenados saber
    QUÉ se rompió no sirve de nada sin saber CUÁNDO.
    """
    ruta_id = ruta if ruta is not None else esc["fabrica"]["id"]
    r = Radiografia(db_session, fecha, ruta_id)
    print(f"  {paso:<44}{r}")
    assert r.suma_fotos == vale, (
        f"tras «{paso}» las fotos del {fecha} suman ${r.suma_fotos} y el día vale ${vale}"
    )
    assert r.suma_apagadas == CERO, (
        f"tras «{paso}» quedó ${r.suma_apagadas} de flete fantasma en recepciones "
        f"apagadas: {r.apagadas}"
    )
    if r.renglones:
        assert r.suma_renglones == vale, (
            f"tras «{paso}» el comprobante cobra ${r.suma_renglones} por el {fecha} y el "
            f"día vale ${vale}"
        )
        assert r.suma_renglones == r.suma_fotos, (
            f"tras «{paso}» el renglón dice ${r.suma_renglones} y sus fotos suman "
            f"${r.suma_fotos}: el desglose dejó de sumar la cifra grande"
        )
    return r


def _exigir_los_comprobantes_cuadrados(client, h, db_session, paso: str) -> None:
    """LA REGLA DE ORO sobre TODOS los comprobantes de flete: cada uno suma lo suyo.

    Es la red de más afuera y va aparte de la del día: un arreglo que deje el 16/07
    perfecto y descuadre el total del comprobante no es un arreglo. Se revisa
    comprobante por comprobante que el valor grande sea EXACTO la suma de sus renglones
    y EXACTO la suma de las fotos de sus recepciones vivas.
    """
    db_session.expire_all()
    for liq in db_session.scalars(
        select(Liquidacion).where(
            Liquidacion.tipo == TIPO_TRANSPORTADOR,
            Liquidacion.estado != ESTADO_ANULADA,
            Liquidacion.deleted_at.is_(None),
        )
    ).all():
        renglones = sum(
            (D(d.valor) for d in liq.detalles if d.deleted_at is None), CERO
        )
        fotos = sum(
            (D(r.valor_transporte) for r in db_session.scalars(
                select(RecepcionLeche).where(
                    RecepcionLeche.liquidacion_transporte_id == liq.id,
                    RecepcionLeche.estado == "activo",
                    RecepcionLeche.deleted_at.is_(None),
                )
            ).all()),
            CERO,
        )
        assert D(liq.valor_transporte) == renglones, (
            f"tras «{paso}» el comprobante {str(liq.id)[:8]} dice "
            f"${D(liq.valor_transporte)} y sus renglones suman ${renglones}"
        )
        assert renglones == fotos, (
            f"tras «{paso}» los renglones del comprobante {str(liq.id)[:8]} suman "
            f"${renglones} y las fotos de sus días ${fotos}"
        )


# ===========================================================================
# LA PRUEBA QUE FIJA LA REGLA
# ===========================================================================
def test_al_dia_fijo_de_cinco_proveedores_se_le_hace_todo_y_sigue_valiendo_el_fijo(
    client, base_datos, db_session
):
    """Quince operaciones seguidas sobre el mismo día fijo. Después de cada una: $150.000.

    LO QUE SE LE HACE, en este orden y sin deshacer nada entre paso y paso —el día llega
    a cada operación con lo que le dejó la anterior, que es como pasa de verdad—:

         1-5. se anotan las cinco recepciones, de a una;
           6. se GENERA el comprobante del flete (queda en borrador);
           7. se le corrigen los litros a Aurelio (82,00 → 200,00);
           8. se le corrigen los litros a Rosa (124,20 → 7,05: una cifra fea a propósito);
           9. se APAGA a Marleny;
          10. se vuelve a PRENDER a Marleny;
          11. se BORRA a Gilberto;
          12. se anota una recepción NUEVA de ese mismo día (un sexto proveedor);
          13. se mueve a Ramiro a OTRA FECHA (17/07): el 16 sigue en $150.000 y el 17
              nace valiendo su propio fijo de $150.000;
          14. se mueve a Rosa a OTRA RUTA (Nápoles, que es POR LITRO);
          15. se devuelve a Rosa a la ruta fija;
       16-17. se RECALCULA el comprobante dos veces seguidas.

    Y en cada punto se revisan además TODOS los comprobantes de flete: su valor grande
    es exacto la suma de sus renglones y exacto la suma de las fotos de sus días.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    ids: dict[str, str] = {}

    print("\n===== LA REGLA: EN UN DÍA FIJO EL RENGLÓN VALE LA TARIFA =====")
    print(f"  el día {EL_DIA} en la ruta «A fabrica» vale ${FIJO} pase lo que pase")
    print("  " + "-" * 74)

    # ---- 1 a 5: las cinco recepciones, de a una -----------------------------
    for nombre, litros_, _ in CINCO:
        ids[nombre] = _recibir(client, h, esc, EL_DIA, nombre, str(litros_))["id"]
        _exigir_el_dia(db_session, esc, f"1-5. anotar a {nombre} ({litros_} L)")

    # ---- 6: el comprobante -------------------------------------------------
    liq_id = _liquidar_flete(client, h)["id"]
    _exigir_el_dia(db_session, esc, "6. GENERAR el comprobante")
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "6. generar")

    def poner(nombre: str, cuerpo: dict) -> None:
        r = client.put(f"{RECEPCIONES}/{ids[nombre]}", json=cuerpo, headers=h)
        assert r.status_code == 200, r.text

    # ---- 7 y 8: corregir los litros ---------------------------------------
    poner("Aurelio", {"cantidad_litros": "200"})
    _exigir_el_dia(db_session, esc, "7. corregir Aurelio 82,00 -> 200,00 L")
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "7. corregir litros")

    poner("Rosa", {"cantidad_litros": "7.05"})
    _exigir_el_dia(db_session, esc, "8. corregir Rosa 124,20 -> 7,05 L")
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "8. corregir litros")

    # ---- 9 y 10: apagar y volver a prender ---------------------------------
    poner("Marleny", {"estado": "inactivo"})
    apagada = _exigir_el_dia(db_session, esc, "9. APAGAR a Marleny")
    assert len(apagada.fotos) == 4, "la apagada sale del reparto"

    poner("Marleny", {"estado": "activo"})
    prendida = _exigir_el_dia(db_session, esc, "10. PRENDER a Marleny otra vez")
    assert len(prendida.fotos) == 5, "y al prenderla vuelve al reparto"
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "10. prender")

    # ---- 11: borrar --------------------------------------------------------
    r = client.delete(f"{RECEPCIONES}/{ids['Gilberto']}", headers=h)
    assert r.status_code in (200, 204), r.text
    borrada = _exigir_el_dia(db_session, esc, "11. BORRAR a Gilberto")
    assert len(borrada.fotos) == 4
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "11. borrar")

    # ---- 12: leche nueva del mismo día -------------------------------------
    esc["proveedores"]["Sexto"] = _crear(client, h, PROVEEDORES, {
        "nombre": "Sexto", "vereda": "La Vega", "precio_litro": "1800",
        "ruta_id": esc["fabrica"]["id"]})
    ids["Sexto"] = _recibir(client, h, esc, EL_DIA, "Sexto", "45.55")["id"]
    nueva = _exigir_el_dia(db_session, esc, "12. anotar leche NUEVA del mismo día")
    assert len(nueva.fotos) == 5, "la recepción nueva es del día y aparece en él"
    # Entra en $0,00 y eso es lo correcto: ese día ya está cobrado en el comprobante, y
    # recoger un proveedor más ese mismo día no cuesta más. Lo que importa acá es que NO
    # le sume un segundo fijo al día: sigue valiendo $150.000.
    assert nueva.fotos[(str(ids["Sexto"])[:8], D("45.55"))] == CERO
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "12. leche nueva")

    # ---- 13: mover de FECHA -------------------------------------------------
    # Ramiro se va al 17/07. El 16 sigue valiendo su fijo entre los que quedan, y el 17
    # nace valiendo SU PROPIO fijo: son dos días y son dos viajes.
    poner("Ramiro", {"fecha": "2026-07-17"})
    quedan = _exigir_el_dia(db_session, esc, "13. mover a Ramiro al 17/07 (el 16)")
    assert len(quedan.fotos) == 4
    _exigir_el_dia(db_session, esc, "13. …y el 17/07, que nace", fecha="2026-07-17")
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "13. mover de fecha")

    # ---- 14 y 15: mover de RUTA --------------------------------------------
    # Rosa (7,05 L) se pasa a Nápoles, que se cobra POR LITRO: allá su flete es
    # 7,05 × $242,76 = $1.711,46 y acá el día sigue valiendo el fijo entre los tres que
    # quedan. Los dos modos conviviendo el mismo día, que es lo que pidió el dueño.
    poner("Rosa", {"ruta_id": esc["napoles"]["id"]})
    tres = _exigir_el_dia(db_session, esc, "14. mover a Rosa a Nápoles (la ruta fija)")
    assert len(tres.fotos) == 3
    por_litro = Radiografia(db_session, EL_DIA, esc["napoles"]["id"])
    a_mano = centavos(D("7.05") * NAPOLES)
    print(f"  {'14. …y Nápoles, que es POR LITRO':<44}{por_litro}")
    print(f"  {'':<44}a mano: 7,05 × ${NAPOLES} = ${a_mano}")
    assert por_litro.suma_fotos == a_mano, "por litro la cuenta es la de siempre"
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "14. mover de ruta")

    poner("Rosa", {"ruta_id": esc["fabrica"]["id"]})
    vuelve = _exigir_el_dia(db_session, esc, "15. devolver a Rosa a la ruta fija")
    assert len(vuelve.fotos) == 4
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "15. mover de ruta")

    # ---- 16 y 17: recalcular dos veces --------------------------------------
    for vuelta in (1, 2):
        r = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
        assert r.status_code == 200, r.text
        _exigir_el_dia(db_session, esc, f"{15 + vuelta}. RECALCULAR (vuelta {vuelta})")
        _exigir_los_comprobantes_cuadrados(client, h, db_session, f"recalcular {vuelta}")

    # Y AL FINAL, el papel: un renglón de día completo por $150.000 y el conductor
    # pudiendo verificarlo leyéndolo.
    final = Radiografia(db_session, EL_DIA, esc["fabrica"]["id"])
    print("  " + "-" * 74)
    print(f"  después de las 17 operaciones el día vale ${final.suma_fotos} "
          f"en {len(final.renglones)} renglón(es)")
    assert final.suma_fotos == FIJO
    assert final.suma_renglones == FIJO
    assert [m for _, m, _ in final.renglones] == ["dia_fijo"]
    assert [c for _, _, c in final.renglones] == [False], (
        "ese día no se cobró en ningún otro comprobante: no puede decir «Ya cobrado»"
    )
    # El error clásico, para dejarlo escrito: cinco proveedores NO son cinco fijos.
    assert final.suma_fotos != FIJO * 5


# ===========================================================================
# JUNTAR DOS DÍAS FIJOS EN UNO: sale UN fijo, no la suma de los dos
# ===========================================================================
def test_juntar_dos_dias_fijos_en_uno_por_la_fecha_da_un_solo_fijo(
    client, base_datos, db_session
):
    """Cuatro días fijos ya liquidados, movidos todos al mismo día: $150.000, no $600.000.

    A MANO:
        antes:  16, 17, 18 y 19 de julio, cada uno su viaje  →  4 × $150.000 = $600.000
        después: las cuatro recepciones el 16/07, un solo viaje →      $150.000

    Es el mismo camión haciendo UNA vez la ruta: si el comprobante siguiera diciendo
    $600.000 estaría cobrando cuatro viajes que no ocurrieron.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dias = ["2026-07-16", "2026-07-17", "2026-07-18", "2026-07-19"]
    ids = [
        _recibir(client, h, esc, dia, nombre, str(litros_))["id"]
        for dia, (nombre, litros_, _) in zip(dias, CINCO)
    ]
    liq_id = _liquidar_flete(client, h)["id"]
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()

    print("\n===== JUNTAR CUATRO DÍAS FIJOS EN UNO (por la fecha) =====")
    print(f"  cuatro días sueltos: ${D(antes['valor_transporte'])} "
          f"en {len(antes['detalles'])} renglones")
    assert D(antes["valor_transporte"]) == FIJO * 4

    for rid in ids[1:]:
        r = client.put(f"{RECEPCIONES}/{rid}", json={"fecha": EL_DIA}, headers=h)
        assert r.status_code == 200, r.text

    despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    dia = Radiografia(db_session, EL_DIA, esc["fabrica"]["id"])
    print(f"  las cuatro el {EL_DIA}: ${D(despues['valor_transporte'])} "
          f"en {len(despues['detalles'])} renglón(es)")
    print(f"  {dia}")
    print(f"  el error que esto hace imposible: ${FIJO * 4}")

    assert len(despues["detalles"]) == 1, (
        f"los cuatro días son ahora UNO: salieron {len(despues['detalles'])} renglones"
    )
    assert D(despues["valor_transporte"]) == FIJO, (
        f"juntar cuatro días fijos en uno dejó el comprobante en "
        f"${D(despues['valor_transporte'])} y un solo viaje vale ${FIJO}"
    )
    assert dia.suma_fotos == FIJO
    assert dia.suma_renglones == FIJO
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "juntar por la fecha")


def test_juntar_dos_dias_fijos_en_uno_por_la_ruta_da_un_solo_fijo(
    client, base_datos, db_session
):
    """El mismo día con dos rutas FIJAS; se pasan todas a una: un solo fijo.

    A MANO:
        antes:  16/07 A fabrica $150.000  +  16/07 Napoles (fijo) $90.000  = $240.000
        después: todas en A fabrica                                        = $150.000
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    otro_fijo = D("90000")
    r = client.put(
        f"/api/v1/transportadores/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO),
             "modo_transporte": "dia_fijo"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(otro_fijo),
             "modo_transporte": "dia_fijo"},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    henri = _recibir(client, h, esc, EL_DIA, "Henri", "50.00")
    liq_id = _liquidar_flete(client, h)["id"]

    print("\n===== JUNTAR DOS RUTAS FIJAS EN UNA (por la ruta) =====")
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  dos rutas fijas: ${D(antes['valor_transporte'])} "
          f"en {len(antes['detalles'])} renglones")
    assert D(antes["valor_transporte"]) == FIJO + otro_fijo

    r = client.put(f"{RECEPCIONES}/{henri['id']}",
                   json={"ruta_id": esc["fabrica"]["id"]}, headers=h)
    assert r.status_code == 200, r.text

    despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    dia = Radiografia(db_session, EL_DIA, esc["fabrica"]["id"])
    print(f"  todas en «A fabrica»: ${D(despues['valor_transporte'])} "
          f"en {len(despues['detalles'])} renglón(es)")
    print(f"  {dia}")
    assert len(despues["detalles"]) == 1
    assert D(despues["valor_transporte"]) == FIJO, (
        f"juntar las dos rutas fijas dejó el comprobante en "
        f"${D(despues['valor_transporte'])} y ese viaje vale ${FIJO}"
    )
    assert dia.suma_fotos == FIJO
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "juntar por la ruta")


# ===========================================================================
# SIN RECEPCIONES VIVAS EL RENGLÓN DESAPARECE: ni $0,00 ni foto fantasma
# ===========================================================================
def test_sin_recepciones_vivas_el_renglon_del_dia_fijo_desaparece(
    client, base_datos, db_session
):
    """Se apagan TODAS las recepciones del día fijo: el renglón se va, no queda en cero.

    Un renglón de "Día completo — $0,00" diría que ese viaje no se paga. Lo que pasó es
    que ese viaje ya no existe: no hay ninguna leche recogida ese día en esa ruta. Y las
    recepciones apagadas no pueden quedar cargando el fijo, porque prenderlas otra vez
    lo devolvería inflado.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    dos = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    # Un día POR LITRO aparte, para que el comprobante no se quede sin nada que cobrar y
    # se pueda ver que el renglón que desaparece es SOLO el del día fijo.
    _recibir(client, h, esc, "2026-07-17", "Henri", "219.45")
    liq_id = _liquidar_flete(client, h)["id"]
    del_litro = centavos(D("219.45") * NAPOLES)

    print("\n===== APAGAR TODAS LAS RECEPCIONES DE UN DÍA FIJO =====")
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  con las dos vivas: ${D(antes['valor_transporte'])} "
          f"(${FIJO} del fijo + ${del_litro} por litro)")
    assert D(antes["valor_transporte"]) == FIJO + del_litro

    for recepcion in (uno, dos):
        r = client.put(f"{RECEPCIONES}/{recepcion['id']}",
                       json={"estado": "inactivo"}, headers=h)
        assert r.status_code == 200, r.text

    despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    dia = Radiografia(db_session, EL_DIA, esc["fabrica"]["id"])
    print(f"  con las dos apagadas: ${D(despues['valor_transporte'])} "
          f"en {len(despues['detalles'])} renglón(es)")
    print(f"  {dia}")

    assert dia.renglones == [], (
        f"el renglón del día fijo tenía que desaparecer y quedó en {dia.renglones}"
    )
    assert dia.suma_apagadas == CERO, (
        f"las recepciones apagadas quedaron cargando ${dia.suma_apagadas} de flete "
        f"fantasma: {dia.apagadas}"
    )
    assert D(despues["valor_transporte"]) == del_litro, (
        "el comprobante se queda solo con el día por litro"
    )
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "apagar todo el día")

    # Y PRENDER UNA OTRA VEZ devuelve el día completo, sin rastro de la foto vieja.
    r = client.put(f"{RECEPCIONES}/{uno['id']}", json={"estado": "activo"}, headers=h)
    assert r.status_code == 200, r.text
    vuelve = Radiografia(db_session, EL_DIA, esc["fabrica"]["id"])
    final = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  al prender una otra vez: {vuelve}")
    assert vuelve.suma_fotos == FIJO, (
        f"prender el día otra vez lo dejó en ${vuelve.suma_fotos}"
    )
    assert vuelve.suma_renglones == FIJO
    assert D(final["valor_transporte"]) == FIJO + del_litro
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "prender otra vez")


# ===========================================================================
# EL DÍA QUE SOSTENÍA EL RENGLÓN SE APAGA, PERO QUEDA LECHE VIVA SUELTA
# ===========================================================================
def test_apagar_la_recepcion_liquidada_no_deja_el_dia_en_cero_con_leche_viva(
    client, base_datos, db_session
):
    """El camión hizo el viaje y hay leche viva de ese día: hay que pagarle el fijo.

    EL CAMINO, que es el que dejaba el día en $0,00:

      1. el 16/07 Alex recoge a Aurelio en la ruta fija; se le liquida y el comprobante
         se APRUEBA ($150.000). Ese renglón deja el día RESERVADO: ya está cobrado;
      2. alguien anota TARDE la leche de Marleny de ese mismo día. Entra en $0,00 —el día
         ya se cobró completo, y recoger un proveedor más no cuesta más—;
      3. alguien apaga la recepción de Aurelio, que es la que sostenía ese renglón…

    …y en ese momento el renglón que reservaba el día desaparece, así que el día YA NO
    ESTÁ COBRADO por nadie. Marleny está viva, el camión hizo el viaje y ese viaje vale
    $150.000: si el sistema deja el día en $0,00 se le queda debiendo al conductor.

    ES UN PROBLEMA DE ORDEN, y por eso vale la pena tener la prueba: el reparto de lo
    pendiente le PREGUNTA a los comprobantes si ese día ya se cobró. Preguntando antes de
    que el comprobante se rehaga, la respuesta es la de un renglón que está a punto de
    dejar de existir.

    (Sobre un comprobante ya PAGADO nada de esto ocurre: apagar el día rebota, y eso lo
    cuida el candado. Está probado aparte.)
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    aurelio = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    liq_id = _liquidar_flete(client, h)["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200

    print("\n===== APAGAR LA QUE SOSTENÍA EL RENGLÓN, CON LECHE VIVA =====")
    tarde = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    print(f"  Marleny anotada tarde entra en ${D(tarde['valor_transporte'])} "
          f"(el día ya estaba cobrado)")
    assert D(tarde["valor_transporte"]) == CERO

    r = client.put(f"{RECEPCIONES}/{aurelio['id']}", json={"estado": "inactivo"}, headers=h)
    print(f"  se apaga a Aurelio, que era quien sostenía el renglón -> {r.status_code}")
    assert r.status_code == 200, r.text

    dia = Radiografia(db_session, EL_DIA, esc["fabrica"]["id"])
    print(f"  {dia}")
    print(f"  el camión hizo el viaje: ese día vale ${FIJO}")
    assert dia.suma_fotos == FIJO, (
        f"el día quedó en ${dia.suma_fotos} con leche viva: el camión hizo el viaje y "
        f"no se le paga nada"
    )
    assert dia.suma_apagadas == CERO, "y la apagada no puede quedar con flete fantasma"

    # Y el avance ("¿cómo voy?") le promete al conductor lo mismo: un día completo por
    # cobrar, de $150.000. Es lo que va a decir el comprobante que se le genere.
    pre = client.post(
        f"{LIQUIDACIONES}/previsualizar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador", "tercero_id": esc["alex"]["id"]},
        headers=h,
    ).json()[0]
    print(f"  el avance del conductor: ${D(pre['valor_transporte'])} en "
          f"{[(d['modo_transporte'], d['valor'], d['dia_fijo_ya_cobrado']) for d in pre['detalles']]}")
    assert D(pre["valor_transporte"]) == FIJO, (
        f"el avance le promete ${D(pre['valor_transporte'])} por un día que vale ${FIJO}"
    )
    assert [d["dia_fijo_ya_cobrado"] for d in pre["detalles"]] == [False], (
        "ese día ya no lo cobra ningún comprobante: no puede decir «Ya cobrado»"
    )
    _exigir_los_comprobantes_cuadrados(client, h, db_session, "apagar la liquidada")
