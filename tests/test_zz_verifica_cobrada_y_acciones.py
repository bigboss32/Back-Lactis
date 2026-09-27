"""VERIFICADOR ADVERSARIAL de tests/test_zz_existentes_cobrada.py (lente cobrada_y_acciones).

Solo lee la app: nada de app/ se modifica. El monkeypatch de `estado_visible` en la
prueba 1 vive solo dentro de la prueba y sirve para imprimir el papel como lo imprimía
el código ANTES de 0274078 (estado=liquidacion.estado).
"""
import io
from decimal import Decimal

from pypdf import PdfReader
from sqlalchemy import select

from app.modules.auditoria.models import Auditoria
from app.modules.liquidaciones import models as liq_models
from tests.conftest import auth_headers
from tests.test_zz_existentes_cobrada import (
    API, ANT, PD, Q1, Q2, REC, _anticipo, _aprobar, _generar, _leer, _proveedor, _recepcion,
)


def _texto_pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    return " ".join(crudo.split())


def _sin_emitido(t):
    i = t.find("Emitido:")
    return t[:i] + t[i + len("Emitido: 00/00/0000 00:00"):] if i >= 0 else t


def test_v1_papel_viejo_impreso_antes_de_cobrar_difiere_en_mas_que_el_estado(
    client, base_datos, monkeypatch
):
    """Hecho (4): 'solo Emitido y esa línea difieren'. El papel que se entrega al aprobar
    Q1 —antes de que exista Q2— no puede llevar la nota 'ya se le cobró'."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])

    # El papel como lo imprimía el código de antes de 0274078, el día de aprobar.
    with monkeypatch.context() as m:
        m.setattr(liq_models.Liquidacion, "estado_visible",
                  property(lambda self: self.estado))
        viejo = _texto_pdf(client, h, q1["id"])

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)
    nuevo = _texto_pdf(client, h, q1["id"])

    print("\n  VIEJO:", viejo[viejo.find("Estado:"):viejo.find("Estado:") + 30])
    print("  NUEVO:", nuevo[nuevo.find("Estado:"):nuevo.find("Estado:") + 45])
    assert "Estado: APROBADA" in viejo
    assert "Estado: PAGADA · QUEDÓ DEBIENDO" in nuevo
    assert "ya se le cobró" not in viejo
    assert "ya se le cobró" in nuevo
    print("  nota 'ya se le cobró' en el viejo:", "ya se le cobró" in viejo,
          "· en el nuevo:", "ya se le cobró" in nuevo)
    # Difieren en más que el estado y el Emitido.
    viejo_n = _sin_emitido(viejo).replace("Estado: APROBADA", "Estado: X")
    nuevo_n = _sin_emitido(nuevo).replace("Estado: PAGADA · QUEDÓ DEBIENDO", "Estado: X")
    assert viejo_n != nuevo_n


def test_v2_el_pdf_de_la_cobrada_no_dice_aprobada_en_ninguna_parte(client, base_datos):
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)
    papel = _texto_pdf(client, h, q1["id"])
    print("\n  'APROBADA' en el papel:", "APROBADA" in papel.upper())
    assert "APROBADA" not in papel.upper()


def test_v3_la_lista_de_recepcion_manda_aprobada_y_trabado_para_el_dia(client, base_datos):
    """F3 medido en la LISTA (la que pinta el @switch), no solo en GET de uno."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    rec1 = _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)
    r = client.get(f"{REC}?page_size=100", headers=h)
    assert r.status_code == 200, r.text
    fila = [x for x in r.json()["items"] if x["id"] == rec1["id"]][0]
    print(f"\n  lista recepción: liquidacion_estado={fila['liquidacion_estado']} "
          f"leche_pagada={fila['leche_pagada']} flete_pagado={fila.get('flete_pagado')}")
    assert fila["liquidacion_estado"] == "aprobada"
    assert fila["leche_pagada"] is True
    assert _leer(client, h, q1["id"])["estado_visible"] == PD


def test_v4_anticipo_de_pd_pendiente_si_se_edita_y_el_de_la_cobrada_no(client, base_datos):
    """F2: el desacuerdo es SOLO en la cobrada. En la pendiente el botón sí funciona."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])
    a = client.get(f"{ANT}/{ant['id']}", headers=h).json()
    print(f"\n  pendiente: bloqueado={a['bloqueado']} liquidacion_estado={a['liquidacion_estado']}")
    r = client.put(f"{ANT}/{ant['id']}", json={"valor": "290000"}, headers=h)
    print(f"  PUT sobre la pendiente -> {r.status_code}")
    assert a["bloqueado"] is False and r.status_code == 200
    q1 = _leer(client, h, q1["id"])
    print(f"  Q1 tras editar el anticipo: estado={q1['estado']} saldo={q1['saldo']}")
    assert q1["estado"] == "borrador"  # lo que promete el aviso 'vuelve a borrador'


def test_v5_la_bitacora_trae_lo_necesario_para_deducir_el_rotulo(client, base_datos, db_session):
    """F5: estado_visible = f(estado, saldo), y los dos van en la bitácora."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])
    _texto_pdf(client, h, q1["id"])
    fila = [f for f in db_session.scalars(select(Auditoria).where(Auditoria.accion == "imprimir")).all()
            if str(f.entidad_id) == q1["id"]][0]
    d = fila.despues
    deducido = PD if d["estado"] in ("aprobada", "parcial", "pagada") and Decimal(str(d["saldo"])) < 0 \
        else d["estado"]
    print(f"\n  bitácora: estado={d['estado']} saldo={d['saldo']} -> rótulo deducido={deducido!r} "
          f"created_at={getattr(fila, 'created_at', None)}")
    assert deducido == PD
    assert getattr(fila, "created_at", None) is not None
