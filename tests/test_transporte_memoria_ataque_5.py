"""ATAQUE A LA MEMORIA, quinta tanda: las puertas INTERCALADAS, en los dos modos.

Cada secuencia deja el dia EXACTAMENTE como estaba (mismo estado, misma ruta, misma
fecha). El comprobante tiene que quedar identico al emitido: misma cifra, mismos
renglones y mismo modo impreso.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO, LIQUIDACIONES, NAPOLES, D, _escenario, _liquidar_flete, _recibir,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, _ok, _put
from tests.test_transporte_memoria_ataque import (
    CERO, EL_FIJO, LITROS, POR_LITRO, RUTA, _de_la_ruta, _emitir, _mem_pinta,
    _memoria, _papel, _pinta, _tarifas,
)

OTRA_FECHA = "2026-07-19"

# Cada paso es (nombre, campos del PUT). El calculo de los campos necesita `esc`, asi
# que se guardan como funciones.
PASOS = {
    "apagar": lambda esc: {"estado": "inactivo"},
    "prender": lambda esc: {"estado": "activo"},
    "a_napoles": lambda esc: {"ruta_id": esc["napoles"]["id"]},
    "a_fabrica": lambda esc: {"ruta_id": esc["fabrica"]["id"]},
    "fecha_19": lambda esc: {"fecha": OTRA_FECHA},
    "fecha_16": lambda esc: {"fecha": EL_DIA},
}

SECUENCIAS = [
    ["apagar", "a_napoles", "a_fabrica", "prender"],
    ["apagar", "fecha_19", "fecha_16", "prender"],
    ["a_napoles", "apagar", "prender", "a_fabrica"],
    ["fecha_19", "apagar", "prender", "fecha_16"],
    ["apagar", "a_napoles", "prender", "a_fabrica"],
    ["a_napoles", "fecha_19", "fecha_16", "a_fabrica"],
    ["apagar", "fecha_19", "prender", "fecha_16"],
    ["a_napoles", "apagar", "a_fabrica", "prender"],
    ["fecha_19", "a_napoles", "a_fabrica", "fecha_16"],
    ["apagar", "prender", "apagar", "prender"],
    ["a_napoles", "a_fabrica", "a_napoles", "a_fabrica"],
    ["apagar", "a_napoles", "fecha_19", "fecha_16", "a_fabrica", "prender"],
    ["fecha_19", "apagar", "a_napoles", "a_fabrica", "prender", "fecha_16"],
]


def _forma(papel):
    return sorted(
        (d["fecha"], d["ruta_nombre"], d["modo_transporte"], D(d["litros"]),
         D(d["precio_litro"]), D(d["valor"]))
        for d in papel["detalles"]
    )


@pytest.mark.parametrize("secuencia", SECUENCIAS, ids=lambda s: "_".join(s))
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_w_las_puertas_intercaladas(client, base_datos, db_session, modo, secuencia):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    print(f"\n===== W {'-'.join(secuencia)}, emitido {modo} =====")
    antes = _papel(client, h, liq_id)
    _pinta(antes, "emitido")
    for paso in secuencia:
        _ok(_put(client, h, dia["id"], **PASOS[paso](esc)), paso)
    despues = _papel(client, h, liq_id)
    _pinta(despues, "de vuelta")
    _mem_pinta(db_session, liq_id, esc, "de vuelta")
    print(f"      emitido ${antes['valor_transporte']} -> ${despues['valor_transporte']}"
          f"   (hoy diria ${hoy})")
    assert _forma(despues) == _forma(antes), (
        f"el papel cambio de forma o de cifra: emitido ${antes['valor_transporte']}, "
        f"quedo ${despues['valor_transporte']}"
    )
