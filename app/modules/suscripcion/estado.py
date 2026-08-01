"""Lógica PURA del estado de la suscripción: solo fechas y aritmética.

Sin imports de la app a propósito: los bordes de cada estado se prueban con
fechas de mentira, sin base de datos ni settings de por medio.

Nota de zona horaria: el backend corre en UTC y el negocio vive en Colombia
(UTC-5), así que `hoy` en el servidor puede ir hasta 5 horas adelantado
respecto a Bogotá. En el peor caso una suscripción "vence" unas horas antes;
los días de gracia absorben ese desfase de sobra y no se complica el código
con zonas horarias.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

ESTADO_EXENTA = "exenta"
ESTADO_ACTIVA = "activa"
ESTADO_POR_VENCER = "por_vencer"
ESTADO_GRACIA = "gracia"
ESTADO_BLOQUEADA = "bloqueada"


@dataclass(frozen=True)
class ResumenEstado:
    estado: str
    # Último día cubierto (pagado o de prueba); None solo para exentas
    limite: date | None
    # Días que faltan para el límite (negativo = días vencidos); None para exentas
    dias_restantes: int | None


def limite_pago(pagada_hasta: date | None, creada: date, dias_prueba: int) -> date:
    """Último día cubierto: lo pagado o, si nunca ha pagado, la prueba
    contada desde la creación de la empresa."""
    if pagada_hasta is not None:
        return pagada_hasta
    return creada + timedelta(days=dias_prueba)


def estado_suscripcion(
    exenta: bool,
    pagada_hasta: date | None,
    creada: date,
    hoy: date,
    dias_aviso: int,
    dias_gracia: int,
    dias_prueba: int,
) -> ResumenEstado:
    """Clasifica la suscripción de una empresa en un punto del tiempo.

    - exenta:     no paga nunca (sin límite ni días restantes)
    - activa:     al día, con más de `dias_aviso` días por delante
    - por_vencer: al día, pero el límite está a `dias_aviso` días o menos
                  (incluye el mismo día del límite: dias_restantes == 0)
    - gracia:     vencida hace entre 1 y `dias_gracia` días (aviso, sin bloqueo)
    - bloqueada:  vencida hace más de `dias_gracia` días (paywall)
    """
    if exenta:
        return ResumenEstado(ESTADO_EXENTA, None, None)
    limite = limite_pago(pagada_hasta, creada, dias_prueba)
    dias_restantes = (limite - hoy).days
    if dias_restantes < -dias_gracia:
        estado = ESTADO_BLOQUEADA
    elif dias_restantes < 0:
        estado = ESTADO_GRACIA
    elif dias_restantes <= dias_aviso:
        estado = ESTADO_POR_VENCER
    else:
        estado = ESTADO_ACTIVA
    return ResumenEstado(estado, limite, dias_restantes)


def sumar_un_mes(fecha: date) -> date:
    """Un mes calendario después, con el día ANCLADO al fin de mes si no existe:
    31-ene → 28-feb (29 en bisiesto), 31-mar → 30-abr. Así 'un mes' significa
    lo que el cliente espera y no se le regalan ni quitan días."""
    if fecha.month == 12:
        anio, mes = fecha.year + 1, 1
    else:
        anio, mes = fecha.year, fecha.month + 1
    dia = min(fecha.day, monthrange(anio, mes)[1])
    return date(anio, mes, dia)
