"""Revisión adversaria (Front-Lactis F4): la pregunta de la pantalla
(`laDeudaViejaSeLlevoElNeto`, en src/app/features/liquidaciones/cifras-de-la-quincena.ts)
contra el guardia del servidor `_no_sale_un_peso_por_la_deuda`, caso por caso, sin base.

La pantalla oculta "Marcar pagada" cuando: saldo <= 0 Y saldo_anterior > 0 Y pagado <= 0.
"""

from decimal import Decimal

import pytest

from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import _no_sale_un_peso_por_la_deuda


def _pantalla(saldo: str, saldo_anterior: str, pagado: str) -> bool:
    return Decimal(saldo) <= 0 and Decimal(saldo_anterior) > 0 and not Decimal(pagado) > 0


@pytest.mark.parametrize(
    "saldo, saldo_anterior, pagado",
    [
        ("0.00", "120000.00", "0.00"),  # la Q2 de Henri: la deuda se llevó el neto
        ("0.00", "100000.00", "0.00"),  # anticipos $20.000 + deuda $100.000
        ("0.00", "0.00", "0.00"),  # sus propios anticipos: sí se marca pagada
        ("0.00", "120000.00", "30000.00"),  # con abono: sí se marca pagada
        ("30000.00", "120000.00", "0.00"),  # queda saldo: Pagar normal
        ("-20000.00", "120000.00", "0.00"),  # quedó debiendo: el guardia también lo ve
    ],
)
def test_la_pantalla_hace_la_misma_pregunta_que_el_guardia(saldo, saldo_anterior, pagado):
    liq = Liquidacion(
        saldo=Decimal(saldo), saldo_anterior=Decimal(saldo_anterior), pagado=Decimal(pagado)
    )
    rebota = _no_sale_un_peso_por_la_deuda(liq) is not None
    assert rebota == _pantalla(saldo, saldo_anterior, pagado)
