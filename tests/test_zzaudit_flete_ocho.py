"""AUDITORÍA DEL FLETE — sonda 37: lo que el aviso de Generar le AFIRMA al dueño
cuando la leche se anota tarde sobre un viaje fijo que todavía está en BORRADOR.

El aviso dice "ya se le pagaron completos en otro comprobante" y "No hay nada que
corregir". El otro comprobante está en BORRADOR: no se le ha pagado un peso, y sí
hay algo que corregir —esos 137,45 L quedan con el flete en $0,00 y no entran al
comprobante mientras no se anule y se vuelva a generar—.
"""
import pytest

from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
    FIJO,
    LIQ,
    escenario,
    foto,
    generar,
    leer,
    pinta,
    recibir,
)


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_el_aviso_de_generar_afirma_un_pago_que_no_ocurrio(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    liq = leer(client, h, liq_a)
    pinta(liq, "S37 comprobante en BORRADOR, sin un peso pagado")
    assert liq["estado"] == "borrador"
    assert not (liq.get("pagos") or [])

    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    gen = generar(client, h)
    for o in gen["omitidas"]:
        print(f"\n  MOTIVO ({o['motivo_codigo']}): {o['motivo']}")
    assert gen["omitidas"]
    texto = gen["omitidas"][0]["motivo"]
    print(f"    fotos del dia: Aurelio (44,23 L) ${foto(db_session, a['id'])} + "
          f"Marleny (137,45 L) ${foto(db_session, tarde['id'])}")
    assert "ya se le pagaron" not in texto, (
        "el aviso le afirma al dueno un pago que no ocurrio: el comprobante que "
        "cobra ese viaje esta en BORRADOR")
    assert "No hay nada que corregir" not in texto, (
        "si hay algo que corregir: 137,45 L de leche viva quedaron con el flete en "
        "$0,00 y no entran al comprobante mientras no se anule y se vuelva a generar")
