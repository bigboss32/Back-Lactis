"""Lógica PURA del estado de la suscripción: los bordes de cada estado y la
aritmética de 'un mes' con el día anclado al fin de mes. Sin base de datos ni
settings: fechas de mentira alrededor de un límite conocido."""
from datetime import date, timedelta

from app.modules.suscripcion.estado import (
    ESTADO_ACTIVA,
    ESTADO_BLOQUEADA,
    ESTADO_EXENTA,
    ESTADO_GRACIA,
    ESTADO_POR_VENCER,
    estado_suscripcion,
    limite_pago,
    sumar_un_mes,
)

HOY = date(2026, 7, 15)
CREADA = HOY - timedelta(days=365)
AVISO = 5
GRACIA = 5
PRUEBA = 30


def _estado(pagada_hasta, *, exenta=False, creada=CREADA):
    return estado_suscripcion(
        exenta=exenta,
        pagada_hasta=pagada_hasta,
        creada=creada,
        hoy=HOY,
        dias_aviso=AVISO,
        dias_gracia=GRACIA,
        dias_prueba=PRUEBA,
    )


def test_exenta_sin_limite_ni_dias_restantes():
    # Exenta gana sobre cualquier fecha, incluso una vencida hace meses
    resultado = _estado(HOY - timedelta(days=90), exenta=True)
    assert resultado.estado == ESTADO_EXENTA
    assert resultado.limite is None
    assert resultado.dias_restantes is None


def test_activa_con_mas_dias_que_el_aviso():
    resultado = _estado(HOY + timedelta(days=AVISO + 1))
    assert resultado.estado == ESTADO_ACTIVA
    assert resultado.limite == HOY + timedelta(days=AVISO + 1)
    assert resultado.dias_restantes == AVISO + 1


def test_por_vencer_desde_el_borde_del_aviso_hasta_el_dia_del_limite():
    assert _estado(HOY + timedelta(days=AVISO)).estado == ESTADO_POR_VENCER
    # El mismo día del límite todavía no está vencida: avisa, no castiga
    el_mismo_dia = _estado(HOY)
    assert el_mismo_dia.estado == ESTADO_POR_VENCER
    assert el_mismo_dia.dias_restantes == 0


def test_gracia_entre_un_dia_y_los_dias_de_gracia():
    assert _estado(HOY - timedelta(days=1)).estado == ESTADO_GRACIA
    ultimo_dia = _estado(HOY - timedelta(days=GRACIA))
    assert ultimo_dia.estado == ESTADO_GRACIA
    assert ultimo_dia.dias_restantes == -GRACIA


def test_bloqueada_pasada_la_gracia():
    resultado = _estado(HOY - timedelta(days=GRACIA + 1))
    assert resultado.estado == ESTADO_BLOQUEADA
    assert resultado.dias_restantes == -(GRACIA + 1)


def test_sin_pagos_la_prueba_cuenta_desde_la_creacion():
    # Recién creada: la prueba completa por delante
    resultado = _estado(None, creada=HOY)
    assert resultado.estado == ESTADO_ACTIVA
    assert resultado.limite == HOY + timedelta(days=PRUEBA)
    assert resultado.dias_restantes == PRUEBA
    # Prueba vencida ayer: entra en gracia, no directo al bloqueo
    assert _estado(None, creada=HOY - timedelta(days=PRUEBA + 1)).estado == ESTADO_GRACIA


def test_limite_pago_prefiere_lo_pagado_sobre_la_prueba():
    pagada = date(2026, 9, 1)
    assert limite_pago(pagada, CREADA, PRUEBA) == pagada
    assert limite_pago(None, CREADA, PRUEBA) == CREADA + timedelta(days=PRUEBA)


def test_sumar_un_mes_caso_normal():
    assert sumar_un_mes(date(2026, 7, 15)) == date(2026, 8, 15)


def test_sumar_un_mes_ancla_al_fin_de_mes():
    # 31-ene → 28-feb (año normal) y 29-feb (bisiesto)
    assert sumar_un_mes(date(2026, 1, 31)) == date(2026, 2, 28)
    assert sumar_un_mes(date(2024, 1, 31)) == date(2024, 2, 29)
    # 29-feb bisiesto → 29-mar; 31-mar → 30-abr
    assert sumar_un_mes(date(2024, 2, 29)) == date(2024, 3, 29)
    assert sumar_un_mes(date(2026, 3, 31)) == date(2026, 4, 30)


def test_sumar_un_mes_cruza_el_anio_en_diciembre():
    assert sumar_un_mes(date(2026, 12, 31)) == date(2027, 1, 31)
    assert sumar_un_mes(date(2026, 12, 1)) == date(2027, 1, 1)
