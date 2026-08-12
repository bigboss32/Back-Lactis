"""CÓMO SE ESCRIBE UNA CIFRA EN LA RESPUESTA: UN SOLO CRITERIO, EN TODOS LOS CAMINOS.

QUÉ PASABA. La misma cifra salía escrita de dos formas según de DÓNDE hubiera salido la
suma: una que venía de una consulta SQL llegaba con la escala de su columna ('0.00') y
una sumada en Python llegaba pelada ('0'). En una sola respuesta convivían
`kilos_merma: "0.00"` y `kilos_a_borona: "0"`, `costo_borona_vendida: "0.00"` y
`borona_vendida: "0"`. Se contaron 16 caminos que escribían el cero de una forma y 34 de
la otra entre `/resumen`, `/lotes` y `/ganancia-por-dia`.

NO MUEVE UN PESO —los dos son cero y el dueño ve el mismo cero en la pantalla— pero sí
cuesta: quien compara dos respuestas para verificar un despliegue tiene que decidir cada
vez si esa diferencia importa, y una alarma que hay que interpretar es una alarma que se
deja de mirar. Y la pantalla que formatea "0" y "0.00" con reglas distintas termina
mostrando dos anchos de columna en la misma tabla.

EL CRITERIO, Y ES UNO SOLO: LA ESCALA LA DECIDE LA CLASE DE CIFRA, NUNCA DE DÓNDE SALIÓ
LA SUMA.

    · LA PLATA Y LOS KILOS van con DOS decimales siempre  → "0.00", "1425000.00"
    · LO QUE SE CUENTA POR PIEZAS va sin decimales siempre → "0", "137"

Es solo la forma de escribirla: el valor no se toca y no se redondea nada que no
estuviera ya en centavos (la prueba de no movimiento, que compara como NÚMERO, sigue
dando exactamente lo mismo).

CÓMO SE EXIGE, Y SON DOS PRUEBAS QUE SE NECESITAN LAS DOS:

  · LA ESTRUCTURAL recorre las respuestas declaradas de TODAS las rutas del módulo y
    exige que cada campo de cifra lleve su anotación. Es la que no se puede burlar
    agregando un campo nuevo: un `Decimal` pelado en un esquema de salida la rompe el
    día que se escribe, no el día que alguien note el cero raro en producción.
  · LA DE COMPORTAMIENTO pide las respuestas de verdad y mira cómo salieron escritas.
    Es la que atrapa lo que la estructural no ve: un serializador propio que escriba
    distinto, o un campo que salga con una escala que no es ninguna de las dos.
"""
import re
from decimal import Decimal, InvalidOperation
from typing import get_args, get_origin

import pytest
from pydantic import BaseModel

from app.modules.reventa import schemas as esquemas
from app.modules.reventa.router import router
from tests.ayudas_reventa import (
    API, PERIODO, ajuste, compra, historia_gorda, venta,
)
from tests.conftest import auth_headers

# Las dos formas permitidas, y nada más.
DOS_DECIMALES = re.compile(r"^-?\d+\.\d{2}$")
SIN_DECIMALES = re.compile(r"^-?\d+$")

# LA ÚNICA EXCEPCIÓN AL CRITERIO, Y ESTÁ DECLARADA. `existencias.disponible` es el único
# campo del módulo cuya escala depende de OTRO campo de la misma fila (`unidad`): un
# producto que se pesa sale con dos decimales y uno que se cuenta, sin ninguno. Va
# escrita aquí —y no dejada pasar en silencio— y tiene su propia prueba más abajo.
CON_ESCALA_PROPIA = ("existencias.disponible",)


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ==========================================================================
# 1. LA ESTRUCTURAL: ningún campo de cifra sin su anotación
# ==========================================================================
def _modelos_dentro(anotacion) -> list:
    """Los esquemas que hay dentro de una anotación (`Page[X]`, `list[X] | None`...)."""
    encontrados = []
    if isinstance(anotacion, type) and issubclass(anotacion, BaseModel):
        encontrados.append(anotacion)
    for parte in get_args(anotacion):
        encontrados += _modelos_dentro(parte)
    return encontrados


def _tiene_decimal(anotacion) -> bool:
    if anotacion is Decimal:
        return True
    if get_origin(anotacion) is None and not get_args(anotacion):
        return False
    return any(_tiene_decimal(p) for p in get_args(anotacion))


def _como_se_escribe(campo) -> str | None:
    """'dos_decimales' | 'sin_decimales' | None, según la anotación del campo."""
    for meta in campo.metadata:
        funcion = getattr(meta, "func", None)
        if funcion is esquemas._con_dos_decimales:
            return "dos_decimales"
        if funcion is esquemas._sin_decimales:
            return "sin_decimales"
    return None


def _campos_con_serializador_propio(modelo) -> set:
    nombres = set()
    for decorador in modelo.__pydantic_decorators__.field_serializers.values():
        nombres.update(decorador.info.fields)
    return nombres


def _todos_los_esquemas_de_respuesta() -> dict:
    """Todo lo que el módulo puede devolver, siguiendo los campos hacia adentro."""
    por_ver = []
    for ruta in router.routes:
        por_ver += _modelos_dentro(getattr(ruta, "response_model", None))
    vistos: dict = {}
    while por_ver:
        modelo = por_ver.pop()
        if modelo.__name__ in vistos:
            continue
        vistos[modelo.__name__] = modelo
        for campo in modelo.model_fields.values():
            por_ver += _modelos_dentro(campo.annotation)
    return vistos


def test_todo_campo_de_cifra_de_una_respuesta_dice_como_se_escribe():
    """Ni un `Decimal` pelado en lo que el módulo devuelve."""
    modelos = _todos_los_esquemas_de_respuesta()
    print(f"\n   esquemas de respuesta del módulo: {len(modelos)}")
    pelados = []
    contados = {"dos_decimales": 0, "sin_decimales": 0, "propio": 0}
    for nombre, modelo in sorted(modelos.items()):
        propios = _campos_con_serializador_propio(modelo)
        for campo_nombre, campo in modelo.model_fields.items():
            if not _tiene_decimal(campo.annotation):
                continue
            if campo_nombre in propios:
                contados["propio"] += 1
                continue
            forma = _como_se_escribe(campo)
            if forma is None:
                pelados.append(f"{nombre}.{campo_nombre}")
            else:
                contados[forma] += 1
    print("   campos de cifra por criterio:", contados)
    assert not pelados, (
        "estos campos de cifra salen escritos como los deje la suma de la que "
        f"vinieron, no como diga su clase: {pelados}"
    )
    # Y que de verdad haya campos de las dos clases: una prueba que no encontrara
    # ninguno pasaría sin medir nada.
    assert contados["dos_decimales"] > 50 and contados["sin_decimales"] > 5, contados


# ==========================================================================
# 2. LA DE COMPORTAMIENTO: las respuestas de verdad, en todos los caminos
# ==========================================================================
def _cifras(nodo, ruta, salida: dict) -> None:
    """{camino del campo: {forma: un ejemplo}} de todo lo que sea un número."""
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            _cifras(valor, f"{ruta}.{clave}" if ruta else clave, salida)
    elif isinstance(nodo, list):
        for hijo in nodo:
            _cifras(hijo, ruta, salida)
    elif isinstance(nodo, str):
        try:
            Decimal(nodo)
        except (InvalidOperation, ValueError):
            return
        if DOS_DECIMALES.match(nodo):
            forma = "dos_decimales"
        elif SIN_DECIMALES.match(nodo):
            forma = "sin_decimales"
        else:
            forma = f"OTRA ({nodo})"
        salida.setdefault(ruta, {}).setdefault(forma, nodo)


def _caminos_de_lectura(client, h) -> dict:
    """Todas las respuestas que el dueño mira, cada una con su nombre."""
    respuestas = {}

    def pedir(nombre, url, **kwargs):
        r = client.get(url, headers=h, **kwargs)
        assert r.status_code == 200, f"{nombre}: {r.status_code} {r.text[:200]}"
        respuestas[nombre] = r.json()

    pedir("resumen", f"{API}/resumen", params=PERIODO)
    pedir("lotes", f"{API}/lotes")
    pedir("ganancia_por_dia", f"{API}/ganancia-por-dia", params=PERIODO)
    pedir("temporadas", f"{API}/temporadas")
    pedir("compras", f"{API}/compras", params={"size": 100})
    pedir("ventas", f"{API}/ventas", params={"size": 100})
    pedir("conversiones", f"{API}/conversiones", params={"size": 100})
    pedir("documentos", f"{API}/documentos", params={"size": 100})
    pedir("productos", f"{API}/productos", params={"size": 100})
    pedir("saldos_anteriores", f"{API}/saldos-anteriores", params={"size": 100})
    pedir("estado_cuenta", f"{API}/estado-cuenta",
          params={"cliente": "Don José Pérez"})
    pedir("estado_cuenta_productor", f"{API}/estado-cuenta-productor",
          params={"productor": "Patricia Rojas"})
    return respuestas


def _historia_con_ceros(client, h) -> None:
    """La historia gorda MÁS lo que hace falta para que salgan ceros de verdad.

    Los ceros son la mitad de lo que esta prueba mide, y no salen solos: hace falta una
    venta sin gasto, un abono a medias, un producto que no se ha vendido y una temporada
    para que aparezcan renglones vacíos en cada pantalla.
    """
    historia_gorda(client, h)
    # Una compra que no se ha vendido ni pagado: deja ceros en media docena de columnas.
    compra(client, h, fecha="2026-03-20", productor="Patricia Rojas",
           kilos_brutos="10.00", precio_kilo="1000")
    # Una venta sin gasto de transporte.
    venta(client, h, fecha="2026-03-21", cliente="Tienda La Esquina", tipo="borona",
          kilos="1.00", precio_kilo="4000")
    # Una merma, que no lleva precio.
    ajuste(client, h, fecha="2026-03-22", kilos="0.50", destino="merma")
    # Un saldo del libro anterior sin abonar.
    r = client.post(f"{API}/saldos-anteriores",
                    json={"tipo": "cobrar", "tercero": "Don José Pérez",
                          "fecha": "2026-01-01", "concepto": "Queso de diciembre",
                          "valor_total": "100000"}, headers=h)
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/temporadas",
                    json={"nombre": "Temporada 2026", "fecha_inicio": "2026-01-01"},
                    headers=h)
    assert r.status_code == 201, r.text


def test_una_cifra_se_escribe_igual_en_todos_los_caminos(client, h):
    """El mismo campo no puede salir con dos escalas, en ninguna pantalla."""
    _historia_con_ceros(client, h)
    formas: dict = {}
    for nombre, cuerpo in _caminos_de_lectura(client, h).items():
        _cifras(cuerpo, nombre, formas)

    print(f"\n   ===== {len(formas)} campos de cifra medidos =====")
    por_forma: dict = {}
    for campo, ejemplos in sorted(formas.items()):
        for forma in ejemplos:
            por_forma.setdefault(forma, []).append(campo)
    for forma, campos in sorted(por_forma.items()):
        print(f"   {forma}: {len(campos)} campos")

    raras = {c: e for c, e in formas.items()
             if any(f.startswith("OTRA") for f in e)}
    assert not raras, (
        "hay cifras que no salen ni con dos decimales ni como entero: "
        + str(list(raras.items())[:8])
    )
    mezclados = {
        c: sorted(e.values()) for c, e in formas.items()
        if len(e) > 1 and not c.endswith(CON_ESCALA_PROPIA)
    }
    assert not mezclados, (
        "el MISMO campo sale escrito de dos formas según de dónde salga la suma: "
        + str(mezclados)
    )


def test_la_plata_y_los_kilos_siempre_con_dos_decimales(client, h):
    """Y lo que se cuenta por piezas, siempre sin decimales.

    Los campos se clasifican por lo que DICE SU ESQUEMA, no por una lista escrita a
    mano: así una anotación mal puesta se ve aquí, y agregar un campo no obliga a
    acordarse de venir a apuntarlo.
    """
    _historia_con_ceros(client, h)
    esperada: dict = {}
    for modelo in _todos_los_esquemas_de_respuesta().values():
        for campo_nombre, campo in modelo.model_fields.items():
            forma = _como_se_escribe(campo)
            if forma:
                esperada.setdefault(campo_nombre, set()).add(forma)
    # Un nombre de campo que significara dos cosas distintas en dos esquemas dejaría
    # esta prueba sin criterio: se exige que no exista.
    ambiguos = {c: f for c, f in esperada.items() if len(f) > 1}
    assert not ambiguos, f"el mismo nombre de campo con dos criterios: {ambiguos}"

    formas: dict = {}
    for nombre, cuerpo in _caminos_de_lectura(client, h).items():
        _cifras(cuerpo, nombre, formas)

    fallas = []
    medidos = 0
    for camino, ejemplos in sorted(formas.items()):
        nombre_campo = camino.rsplit(".", 1)[-1]
        debe_ser = esperada.get(nombre_campo)
        if debe_ser is None:
            continue  # no es un campo de cifra (fechas, contadores, `orden`...)
        medidos += 1
        for forma, ejemplo in ejemplos.items():
            if forma != next(iter(debe_ser)):
                fallas.append(f"{camino} = '{ejemplo}' (tenía que ser {debe_ser})")
    print(f"\n   {medidos} campos de cifra comprobados en las respuestas de verdad")
    assert not fallas, "\n".join(fallas)
    assert medidos > 40, f"solo se midieron {medidos} campos: la historia no dio para más"


def test_el_disponible_se_escribe_en_la_unidad_de_su_producto(client, h):
    """El único campo cuya escala depende de OTRO campo de la misma fila.

    "0,00 barras de mozzarella" no se lee y "3 kg" de un producto que se pesa esconde
    los gramos, así que este campo se escribe según la unidad que trae al lado. Es la
    excepción declarada al criterio, y por eso se mide aparte en vez de dejarla pasar
    en silencio.
    """
    _historia_con_ceros(client, h)
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    filas = r.json()["existencias"]
    print("\n   existencias:", [(e["producto"], e["unidad"], e["disponible"])
                                for e in filas])
    assert filas, "la historia tiene productos: la lista no puede venir vacía"
    for e in filas:
        if e["unidad"] == "kg":
            assert DOS_DECIMALES.match(e["disponible"]), e
        else:
            assert SIN_DECIMALES.match(e["disponible"]), e
    assert {e["unidad"] for e in filas} == {"kg", "unidad"}, (
        "hacen falta las dos unidades para que esta prueba mida algo"
    )
