"""ATAQUE, sexta tanda: las puertas intercaladas SOBRE el montaje del renglon en $0,00.

Mismo montaje de test_transporte_memoria_ataque_3 (un papel con un renglon "Ya cobrado"
en $0,00 y uno de $150.000 en la MISMA ruta fija). Se mide cuantas de las secuencias le
mueven la cifra al papel.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import NAPOLES, D, _escenario
from tests.test_transporte_dia_fijo_auditoria import _ok, _put
from tests.test_transporte_memoria_ataque import (
    EL_FIJO, _mem_pinta, _papel, _pinta, _tarifas,
)
from tests.test_transporte_memoria_ataque_3 import _montar
from tests.test_transporte_memoria_ataque_5 import PASOS as _PASOS_DEL_5, SECUENCIAS, _forma

# EL PASO DE "DEVOLVER" ES LA FECHA PROPIA DEL DÍA ATACADO, y aquí no es la misma que
# en la tanda 5. Allá el día vivía el 16/07, así que `fecha_16` lo devolvía a su sitio.
# Acá el 16/07 es el OTRO día del papel —el que entra en $0,00 porque su viaje ya se
# cobró en otro comprobante— y el día atacado vive el 17/07. Con el paso de allá la
# secuencia no devolvía nada: FUSIONABA los dos días en el 16, y entonces el papel
# baja a $0,00 con toda la razón (esa leche pasó a un viaje ya pagado, y el viaje del
# 17 dejó de existir porque no le quedó leche). Se veía como un salto de -$150.000 y
# era la prueba pidiendo algo imposible: que mover un día a otra fecha no cambie nada.
PASOS = dict(_PASOS_DEL_5)
PASOS["fecha_16"] = lambda esc: {"fecha": "2026-07-17"}


@pytest.mark.parametrize("secuencia", SECUENCIAS, ids=lambda s: "_".join(s))
def test_v_las_puertas_sobre_el_montaje_del_cero(client, base_datos, db_session, secuencia):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    antes = _papel(client, h, liq2)
    assert D(antes["valor_transporte"]) == EL_FIJO
    print(f"\n===== V {'-'.join(secuencia)} =====")
    _pinta(antes, "emitido")
    # la tarifa de hoy pasa a POR LITRO (el dueño renegocio)
    _tarifas(client, h, esc, NAPOLES, "litro")
    for paso in secuencia:
        _ok(_put(client, h, otro["id"], **PASOS[paso](esc)), paso)
    despues = _papel(client, h, liq2)
    _pinta(despues, "de vuelta")
    _mem_pinta(db_session, liq2, esc, "de vuelta")
    salto = D(despues["valor_transporte"]) - D(antes["valor_transporte"])
    print(f"      emitido ${antes['valor_transporte']} -> ${despues['valor_transporte']}"
          f"   SALTO ${salto}")
    # el comprobante 1, PAGADO, no se puede mover nunca
    assert D(_papel(client, h, liq1)["valor_transporte"]) == EL_FIJO
    assert _forma(despues) == _forma(antes), f"salto de ${salto}"
