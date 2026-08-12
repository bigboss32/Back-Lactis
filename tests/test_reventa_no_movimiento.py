"""LA PRUEBA DE NO MOVIMIENTO: la historia del cliente da las MISMAS cifras.

QUÉ ES ESTO Y POR QUÉ EXISTE. El módulo de reventa pasó de decidir la plata por el
NOMBRE del tipo ('queso', 'borona', 'mozzarella' escritos a mano en el código) a
decidirla por EL PRODUCTO DEL CATÁLOGO. Eso tocó el resumen, el inventario, el
costeo y el desglose: o sea, todos los caminos por los que pasa un peso.

El cliente está en producción y lleva meses de historia con esos tres productos. La
única afirmación que de verdad importa sobre ese cambio no es "las pruebas pasan",
es ESTA: sobre la misma historia, el sistema dice EXACTAMENTE lo mismo que decía
antes. Ni un centavo, ni un kilo, ni una etiqueta.

CÓMO SE MIDE, y es a propósito que no se mida de otra forma. Las cifras esperadas de
abajo (`ESPERADO`) se capturaron corriendo esta misma historia contra el código
ANTERIOR al cambio (commit c3e3cd7), y están escritas aquí como literales. No se
comparan dos corridas del código de hoy —eso no probaría nada—: se compara el código
de hoy contra un número que ya estaba escrito. Si alguien mueve una cifra, esta
prueba dice cuál, cuánto valía y cuánto vale ahora.

QUÉ ABARCA LA HISTORIA (los tres productos de siempre, entrelazados):
  · compras de queso con borona que llega gratis, de varios productores y varios
    días, algunas por factura de varios renglones;
  · compras de mozzarella por barras;
  · ventas de queso, de borona y de mozzarella, con gasto por kilo y por barra;
  · abonos parciales a compras y a ventas, y una venta de contado;
  · conversiones a borona Y de merma;
  · una compra editada y una venta editada (los caminos de PUT);
  · una compra anulada y una venta anulada;
  · saldos del libro anterior de los dos lados, con un abono;
  · dos temporadas, una cerrada y una abierta.

QUÉ SE COMPARA: el resumen entero (encabezado, desglose por producto y por
productor), el panel de lotes con su detalle por compra y por venta, la ganancia por
día, los dos estados de cuenta (cliente y productor), el panel de temporadas y los
tres inventarios. Todo lo que el dueño mira.
"""
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}

# Dónde se deja la captura cuando se corre con CAPTURAR_NO_MOVIMIENTO=1 en el
# ambiente. NO es la fuente de la verdad (esa es `_ESPERADO_JSON`, escrito en este
# archivo): es la herramienta para volver a generarlo el día que la historia cambie
# a propósito. Es una variable de ambiente y no una opción de pytest para no tener
# que tocar el conftest que comparten las 1.600 pruebas.
CAPTURA = Path(__file__).parent / "_captura_no_movimiento.json"


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ============================================================== la historia
def _compra(client, h, **campos):
    r = client.post(f"{API}/compras", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _venta(client, h, **campos):
    r = client.post(f"{API}/ventas", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _factura(client, h, tipo, fecha, tercero, renglones, **extra):
    r = client.post(
        f"{API}/documentos",
        json={"tipo": tipo, "fecha": fecha, "tercero": tercero,
              "renglones": renglones, **extra},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _abono_compra(client, h, compra_id, fecha, valor):
    r = client.post(f"{API}/compras/{compra_id}/abonos",
                    json={"fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _abono_venta(client, h, venta_id, fecha, valor):
    r = client.post(f"{API}/ventas/{venta_id}/abonos",
                    json={"fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _conversion(client, h, fecha, kilos, destino, precio_kilo=0):
    r = client.post(f"{API}/conversiones",
                    json={"fecha": fecha, "kilos": kilos, "destino": destino,
                          "precio_kilo": precio_kilo}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def construir_historia(client, h) -> dict:
    """La historia del cliente: los TRES productos de siempre, entrelazados.

    Las cifras son de las que él maneja (kilos con decimales, precios de miles de
    pesos) y están escogidas para que los redondeos se noten: 0,33 de kilo y
    precios que no son múltiplos de nada obligan al reparto a repartir centavos.
    """
    ids: dict = {}

    # ---------------------------------------------------- enero: primeras compras
    ids["c1"] = _compra(client, h, fecha="2026-01-05", productor="Patricia Rojas",
                        kilos_brutos="820.50", precio_kilo="14300",
                        borona_kilos="18.25")["id"]
    ids["c2"] = _compra(client, h, fecha="2026-01-05", productor="Sebastián Ruiz",
                        kilos_brutos="613.33", precio_kilo="14750",
                        borona_kilos="9.50")["id"]
    # Una factura de DOS renglones el mismo día: el orden de los renglones decide
    # el derrame de los abonos y el puesto en el reparto.
    factura = _factura(client, h, "compra", "2026-01-12", "Patricia Rojas", [
        {"kilos_brutos": "410.75", "precio_kilo": "14100", "borona_kilos": "7.10"},
        {"kilos_brutos": "205.20", "precio_kilo": "14400"},
    ])
    ids["f_compra"] = factura["id"]
    ids["f_compra_renglones"] = [r["id"] for r in factura["renglones"]]

    # ---------------------------------------------------- enero: mozzarella
    ids["cm1"] = _compra(client, h, fecha="2026-01-12", productor="Lácteos del Valle",
                         tipo="mozzarella", barras=340, precio_barra="9800")["id"]

    # ---------------------------------------------------- enero: ventas
    ids["v1"] = _venta(client, h, fecha="2026-01-08", cliente="Don José Pérez",
                       tipo="queso", kilos="600.00", precio_kilo="18900",
                       gasto_por_kilo="310", gasto_concepto="Flete Bogotá")["id"]
    ids["v2"] = _venta(client, h, fecha="2026-01-15", cliente="Supermercado La 33",
                       tipo="queso", kilos="455.33", precio_kilo="19250",
                       gasto_por_kilo="285")["id"]
    ids["vb1"] = _venta(client, h, fecha="2026-01-16", cliente="Don José Pérez",
                        tipo="borona", kilos="21.75", precio_kilo="6400",
                        gasto_por_kilo="120")["id"]
    ids["vm1"] = _venta(client, h, fecha="2026-01-18", cliente="Supermercado La 33",
                        tipo="mozzarella", barras=180, precio_barra="13500",
                        gasto_por_barra="450")["id"]

    # ---------------------------------------------------- enero: ajustes
    _conversion(client, h, "2026-01-20", "32.40", "borona", precio_kilo="6000")
    _conversion(client, h, "2026-01-21", "11.15", "merma")

    # ---------------------------------------------------- abonos
    _abono_compra(client, h, ids["c1"], "2026-01-10", "5000000")
    _abono_compra(client, h, ids["c2"], "2026-01-11", "3250000.55")
    _abono_venta(client, h, ids["v1"], "2026-01-14", "7000000")
    _abono_venta(client, h, ids["v2"], "2026-01-20", "4000000.45")
    # Abono a la FACTURA: se derrama sobre sus renglones en orden.
    r = client.post(f"{API}/documentos/{ids['f_compra']}/abonos",
                    json={"fecha": "2026-01-22", "valor": "6000000"}, headers=h)
    assert r.status_code in (200, 201), r.text

    # ---------------------------------------------------- febrero: más movimiento
    ids["c3"] = _compra(client, h, fecha="2026-02-03", productor="Sebastián Ruiz",
                        kilos_brutos="700.00", precio_kilo="15100",
                        borona_kilos="12.33")["id"]
    ids["cm2"] = _compra(client, h, fecha="2026-02-03", productor="Lácteos del Valle",
                         tipo="mozzarella", barras=120, precio_barra="10200")["id"]
    # Factura de venta de DOS renglones (queso y borona en la misma factura).
    factura_v = _factura(client, h, "venta", "2026-02-10", "Don José Pérez", [
        {"kilos": "380.00", "precio_kilo": "19800", "gasto_por_kilo": "300"},
        {"tipo": "borona", "kilos": "15.00", "precio_kilo": "6600"},
    ])
    ids["f_venta"] = factura_v["id"]
    ids["v_contado"] = _venta(client, h, fecha="2026-02-14",
                              cliente="Panadería El Trigal", tipo="queso",
                              kilos="120.66", precio_kilo="20100",
                              pagada_de_contado=True)["id"]
    ids["vm2"] = _venta(client, h, fecha="2026-02-16", cliente="Panadería El Trigal",
                        tipo="mozzarella", barras=95, precio_barra="14100",
                        gasto_por_barra="380")["id"]

    # ---------------------------------------------------- los caminos de PUT
    # Editar una compra (cambia su plata y su estado con los abonos ya hechos).
    ids["c4"] = _compra(client, h, fecha="2026-02-20", productor="Patricia Rojas",
                        kilos_brutos="300.00", precio_kilo="15000")["id"]
    r = client.put(f"{API}/compras/{ids['c4']}",
                   json={"kilos_brutos": "310.50", "precio_kilo": "15200"}, headers=h)
    assert r.status_code == 200, r.text
    # Editar una venta.
    ids["v4"] = _venta(client, h, fecha="2026-02-24", cliente="Supermercado La 33",
                       tipo="queso", kilos="150.00", precio_kilo="20000",
                       gasto_por_kilo="250")["id"]
    r = client.put(f"{API}/ventas/{ids['v4']}",
                   json={"kilos": "162.40", "precio_kilo": "20400",
                         "gasto_por_kilo": "265"}, headers=h)
    assert r.status_code == 200, r.text

    # ---------------------------------------------------- anulaciones
    ids["c_anulada"] = _compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
                               kilos_brutos="88.00", precio_kilo="15500")["id"]
    r = client.post(f"{API}/compras/{ids['c_anulada']}/anular", headers=h)
    assert r.status_code == 200, r.text
    ids["v_anulada"] = _venta(client, h, fecha="2026-03-04", cliente="Don José Pérez",
                              tipo="queso", kilos="40.00", precio_kilo="21000")["id"]
    r = client.post(f"{API}/ventas/{ids['v_anulada']}/anular", headers=h)
    assert r.status_code == 200, r.text

    # ---------------------------------------------------- el libro anterior
    for tipo, tercero, valor in (
        ("cobrar", "Don José Pérez", "1850000"),
        ("cobrar", "Tienda La Esquina", "430000.75"),
        ("pagar", "Patricia Rojas", "2200000"),
        ("pagar", "Marleny Gómez", "615000.20"),
    ):
        r = client.post(f"{API}/saldos-anteriores",
                        json={"tipo": tipo, "tercero": tercero, "fecha": "2025-12-15",
                              "concepto": "Saldo del cuaderno", "valor_total": valor},
                        headers=h)
        assert r.status_code == 201, r.text
        ids.setdefault("saldos", []).append(r.json()["id"])
    r = client.post(f"{API}/saldos-anteriores/{ids['saldos'][0]}/abonos",
                    json={"fecha": "2026-01-30", "valor": "500000"}, headers=h)
    assert r.status_code in (200, 201), r.text

    # ---------------------------------------------------- temporadas
    # Se abre SIN fecha de fin y se cierra después, que es como pasa en la vida
    # real: la temporada se cierra el día que se cierra, no se sabe al abrirla.
    r = client.post(f"{API}/temporadas",
                    json={"nombre": "Temporada enero", "fecha_inicio": "2026-01-01"},
                    headers=h)
    assert r.status_code == 201, r.text
    ids["t1"] = r.json()["id"]
    r = client.post(f"{API}/temporadas/{ids['t1']}/cerrar",
                    json={"fecha_fin": "2026-01-31"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/temporadas",
                    json={"nombre": "Temporada febrero", "fecha_inicio": "2026-02-01"},
                    headers=h)
    assert r.status_code == 201, r.text
    ids["t2"] = r.json()["id"]
    return ids


# ============================================================== la captura
def _limpiar(valor):
    """Deja el JSON comparable: sin ids ni fechas de auditoría (que cambian en cada
    corrida por definición) y con los números como texto para que no se pierda un
    decimal por el camino."""
    if isinstance(valor, dict):
        return {
            k: _limpiar(v)
            for k, v in sorted(valor.items())
            if k not in ("id", "created_at", "updated_at", "empresa_id", "documento_id",
                         "creado_por", "actualizado_por", "adjuntos", "abonos")
        }
    if isinstance(valor, list):
        return [_limpiar(v) for v in valor]
    return valor


def capturar(client, h) -> dict:
    """TODAS las cifras que el dueño mira, en un solo diccionario."""
    salida: dict = {}

    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    salida["resumen"] = _limpiar(r.json())

    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    salida["lotes"] = _limpiar(r.json())

    r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    salida["ganancia_por_dia"] = _limpiar(r.json())

    r = client.get(f"{API}/temporadas", headers=h)
    assert r.status_code == 200, r.text
    salida["temporadas"] = _limpiar(r.json())

    for cliente in ("Don José Pérez", "Supermercado La 33", "Panadería El Trigal",
                    "Tienda La Esquina"):
        r = client.get(f"{API}/estado-cuenta", params={"cliente": cliente}, headers=h)
        assert r.status_code == 200, r.text
        salida[f"estado_cuenta::{cliente}"] = _limpiar(r.json())

    for productor in ("Patricia Rojas", "Sebastián Ruiz", "Lácteos del Valle",
                      "Marleny Gómez"):
        r = client.get(f"{API}/estado-cuenta-productor",
                       params={"productor": productor}, headers=h)
        assert r.status_code == 200, r.text
        salida[f"estado_cuenta_productor::{productor}"] = _limpiar(r.json())

    return salida


# ============================================================== las cifras de antes
# Capturadas contra el código ANTERIOR al cambio del catálogo (commit c3e3cd7).
# NO SE TOCAN: son la línea base. Si esta prueba falla, la respuesta no es
# actualizar este archivo, es averiguar qué cifra se movió y por qué.
#
# LA ÚNICA COSA QUE SE AGREGÓ A MANO es el bloque `existencias` del resumen, porque es
# un campo NUEVO que el código de antes no devolvía (el inventario por producto). Sus
# tres cifras se escribieron copiando las tres del inventario que ya venían en esta
# misma captura —`kilos_disponibles`, `borona_disponible` y `barras_disponibles`—, así
# que la prueba está exigiendo que el inventario por producto diga EXACTAMENTE lo que
# decían los tres campos de siempre. Si el queso, la borona o la mozzarella cambiaran
# de existencias al pasar a contarse por producto, esta prueba lo dice.
_ESPERADO_JSON = r"""
{
 "estado_cuenta::Don José Pérez": {
  "cliente": "Don José Pérez",
  "compras": 4,
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "500000.00",
  "libro_anterior_saldo": "1350000.00",
  "libro_anterior_total": "1850000.00",
  "pagos": [
   {
    "fecha": "2026-01-14",
    "valor": "7000000.00"
   }
  ],
  "saldo": "13452200.00",
  "saldo_a_favor": "0",
  "saldos_anteriores": [
   {
    "abonado": "500000.00",
    "concepto": "Saldo del cuaderno",
    "fecha": "2025-12-15",
    "saldo": "1350000.00",
    "saldo_a_favor": "0",
    "valor_total": "1850000.00"
   }
  ],
  "total_abonado": "7000000.00",
  "total_barras": "0",
  "total_facturado": "19102200.00",
  "total_kilos": "1016.75",
  "ventas": [
   {
    "abonado": "7000000.00",
    "barras": "0",
    "estado": "parcial",
    "fecha": "2026-01-08",
    "kilos": "600.00",
    "precio_barra": "0",
    "precio_kilo": "18900.00",
    "producto": "Queso",
    "saldo": "4340000.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "11340000.00"
   },
   {
    "abonado": "0.00",
    "barras": "0",
    "estado": "pendiente",
    "fecha": "2026-01-16",
    "kilos": "21.75",
    "precio_barra": "0",
    "precio_kilo": "6400.00",
    "producto": "Borona",
    "saldo": "139200.00",
    "saldo_a_favor": "0",
    "tipo": "borona",
    "unidad": "kg",
    "valor_total": "139200.00"
   },
   {
    "abonado": "0.00",
    "barras": "0",
    "estado": "pendiente",
    "fecha": "2026-02-10",
    "kilos": "380.00",
    "precio_barra": "0",
    "precio_kilo": "19800.00",
    "producto": "Queso",
    "saldo": "7524000.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "7524000.00"
   },
   {
    "abonado": "0.00",
    "barras": "0",
    "estado": "pendiente",
    "fecha": "2026-02-10",
    "kilos": "15.00",
    "precio_barra": "0",
    "precio_kilo": "6600.00",
    "producto": "Borona",
    "saldo": "99000.00",
    "saldo_a_favor": "0",
    "tipo": "borona",
    "unidad": "kg",
    "valor_total": "99000.00"
   }
  ]
 },
 "estado_cuenta::Panadería El Trigal": {
  "cliente": "Panadería El Trigal",
  "compras": 2,
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0",
  "libro_anterior_saldo": "0",
  "libro_anterior_total": "0",
  "pagos": [
   {
    "fecha": "2026-02-14",
    "valor": "2425266.00"
   }
  ],
  "saldo": "1339500.00",
  "saldo_a_favor": "0",
  "saldos_anteriores": [],
  "total_abonado": "2425266.00",
  "total_barras": "95",
  "total_facturado": "3764766.00",
  "total_kilos": "120.66",
  "ventas": [
   {
    "abonado": "2425266.00",
    "barras": "0",
    "estado": "pagada",
    "fecha": "2026-02-14",
    "kilos": "120.66",
    "precio_barra": "0",
    "precio_kilo": "20100.00",
    "producto": "Queso",
    "saldo": "0.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "2425266.00"
   },
   {
    "abonado": "0.00",
    "barras": "95",
    "estado": "pendiente",
    "fecha": "2026-02-16",
    "kilos": "0.00",
    "precio_barra": "14100.00",
    "precio_kilo": "0.00",
    "producto": "Mozzarella",
    "saldo": "1339500.00",
    "saldo_a_favor": "0",
    "tipo": "mozzarella",
    "unidad": "barra",
    "valor_total": "1339500.00"
   }
  ]
 },
 "estado_cuenta::Supermercado La 33": {
  "cliente": "Supermercado La 33",
  "compras": 3,
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0",
  "libro_anterior_saldo": "0",
  "libro_anterior_total": "0",
  "pagos": [
   {
    "fecha": "2026-01-20",
    "valor": "4000000.45"
   }
  ],
  "saldo": "10508062.05",
  "saldo_a_favor": "0",
  "saldos_anteriores": [],
  "total_abonado": "4000000.45",
  "total_barras": "180",
  "total_facturado": "14508062.50",
  "total_kilos": "617.73",
  "ventas": [
   {
    "abonado": "4000000.45",
    "barras": "0",
    "estado": "parcial",
    "fecha": "2026-01-15",
    "kilos": "455.33",
    "precio_barra": "0",
    "precio_kilo": "19250.00",
    "producto": "Queso",
    "saldo": "4765102.05",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "8765102.50"
   },
   {
    "abonado": "0.00",
    "barras": "180",
    "estado": "pendiente",
    "fecha": "2026-01-18",
    "kilos": "0.00",
    "precio_barra": "13500.00",
    "precio_kilo": "0.00",
    "producto": "Mozzarella",
    "saldo": "2430000.00",
    "saldo_a_favor": "0",
    "tipo": "mozzarella",
    "unidad": "barra",
    "valor_total": "2430000.00"
   },
   {
    "abonado": "0.00",
    "barras": "0",
    "estado": "pendiente",
    "fecha": "2026-02-24",
    "kilos": "162.40",
    "precio_barra": "0",
    "precio_kilo": "20400.00",
    "producto": "Queso",
    "saldo": "3312960.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "3312960.00"
   }
  ]
 },
 "estado_cuenta::Tienda La Esquina": {
  "cliente": "Tienda La Esquina",
  "compras": 0,
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0.00",
  "libro_anterior_saldo": "430000.75",
  "libro_anterior_total": "430000.75",
  "pagos": [],
  "saldo": "430000.75",
  "saldo_a_favor": "0",
  "saldos_anteriores": [
   {
    "abonado": "0.00",
    "concepto": "Saldo del cuaderno",
    "fecha": "2025-12-15",
    "saldo": "430000.75",
    "saldo_a_favor": "0",
    "valor_total": "430000.75"
   }
  ],
  "total_abonado": "0",
  "total_barras": "0",
  "total_facturado": "0",
  "total_kilos": "0",
  "ventas": []
 },
 "estado_cuenta_productor::Lácteos del Valle": {
  "compras": 2,
  "compras_detalle": [
   {
    "abonado": "0.00",
    "barras": "340",
    "borona_kilos": "0",
    "estado": "pendiente",
    "fecha": "2026-01-12",
    "kilos": "0.00",
    "precio_barra": "9800.00",
    "precio_kilo": "0.00",
    "saldo": "3332000.00",
    "saldo_a_favor": "0",
    "tipo": "mozzarella",
    "unidad": "barra",
    "valor_total": "3332000.00"
   },
   {
    "abonado": "0.00",
    "barras": "120",
    "borona_kilos": "0",
    "estado": "pendiente",
    "fecha": "2026-02-03",
    "kilos": "0.00",
    "precio_barra": "10200.00",
    "precio_kilo": "0.00",
    "saldo": "1224000.00",
    "saldo_a_favor": "0",
    "tipo": "mozzarella",
    "unidad": "barra",
    "valor_total": "1224000.00"
   }
  ],
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0",
  "libro_anterior_saldo": "0",
  "libro_anterior_total": "0",
  "pagos": [],
  "productor": "Lácteos del Valle",
  "saldo": "4556000.00",
  "saldo_a_favor": "0",
  "saldos_anteriores": [],
  "total_barras": "460",
  "total_comprado": "4556000.00",
  "total_kilos": "0.00",
  "total_pagado": "0.00"
 },
 "estado_cuenta_productor::Marleny Gómez": {
  "compras": 0,
  "compras_detalle": [],
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0.00",
  "libro_anterior_saldo": "615000.20",
  "libro_anterior_total": "615000.20",
  "pagos": [],
  "productor": "Marleny Gómez",
  "saldo": "615000.20",
  "saldo_a_favor": "0",
  "saldos_anteriores": [
   {
    "abonado": "0.00",
    "concepto": "Saldo del cuaderno",
    "fecha": "2025-12-15",
    "saldo": "615000.20",
    "saldo_a_favor": "0",
    "valor_total": "615000.20"
   }
  ],
  "total_barras": "0",
  "total_comprado": "0",
  "total_kilos": "0",
  "total_pagado": "0"
 },
 "estado_cuenta_productor::Patricia Rojas": {
  "compras": 4,
  "compras_detalle": [
   {
    "abonado": "5000000.00",
    "barras": "0",
    "borona_kilos": "18.25",
    "estado": "parcial",
    "fecha": "2026-01-05",
    "kilos": "820.50",
    "precio_barra": "0",
    "precio_kilo": "14300.00",
    "saldo": "6733150.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "11733150.00"
   },
   {
    "abonado": "5791575.00",
    "barras": "0",
    "borona_kilos": "7.10",
    "estado": "pagada",
    "fecha": "2026-01-12",
    "kilos": "410.75",
    "precio_barra": "0",
    "precio_kilo": "14100.00",
    "saldo": "0.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "5791575.00"
   },
   {
    "abonado": "208425.00",
    "barras": "0",
    "borona_kilos": "0",
    "estado": "parcial",
    "fecha": "2026-01-12",
    "kilos": "205.20",
    "precio_barra": "0",
    "precio_kilo": "14400.00",
    "saldo": "2746455.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "2954880.00"
   },
   {
    "abonado": "0.00",
    "barras": "0",
    "borona_kilos": "0",
    "estado": "pendiente",
    "fecha": "2026-02-20",
    "kilos": "310.50",
    "precio_barra": "0",
    "precio_kilo": "15200.00",
    "saldo": "4719600.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "4719600.00"
   }
  ],
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0.00",
  "libro_anterior_saldo": "2200000.00",
  "libro_anterior_total": "2200000.00",
  "pagos": [
   {
    "fecha": "2026-01-10",
    "valor": "5000000.00"
   },
   {
    "fecha": "2026-01-22",
    "valor": "5791575.00"
   },
   {
    "fecha": "2026-01-22",
    "valor": "208425.00"
   }
  ],
  "productor": "Patricia Rojas",
  "saldo": "16399205.00",
  "saldo_a_favor": "0",
  "saldos_anteriores": [
   {
    "abonado": "0.00",
    "concepto": "Saldo del cuaderno",
    "fecha": "2025-12-15",
    "saldo": "2200000.00",
    "saldo_a_favor": "0",
    "valor_total": "2200000.00"
   }
  ],
  "total_barras": "0",
  "total_comprado": "25199205.00",
  "total_kilos": "1746.95",
  "total_pagado": "11000000.00"
 },
 "estado_cuenta_productor::Sebastián Ruiz": {
  "compras": 2,
  "compras_detalle": [
   {
    "abonado": "3250000.55",
    "barras": "0",
    "borona_kilos": "9.50",
    "estado": "parcial",
    "fecha": "2026-01-05",
    "kilos": "613.33",
    "precio_barra": "0",
    "precio_kilo": "14750.00",
    "saldo": "5796616.95",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "9046617.50"
   },
   {
    "abonado": "0.00",
    "barras": "0",
    "borona_kilos": "12.33",
    "estado": "pendiente",
    "fecha": "2026-02-03",
    "kilos": "700.00",
    "precio_barra": "0",
    "precio_kilo": "15100.00",
    "saldo": "10570000.00",
    "saldo_a_favor": "0",
    "tipo": "queso",
    "unidad": "kg",
    "valor_total": "10570000.00"
   }
  ],
  "desde": null,
  "emitido": "2026-08-11",
  "hasta": null,
  "libro_anterior_abonado": "0",
  "libro_anterior_saldo": "0",
  "libro_anterior_total": "0",
  "pagos": [
   {
    "fecha": "2026-01-11",
    "valor": "3250000.55"
   }
  ],
  "productor": "Sebastián Ruiz",
  "saldo": "16366616.95",
  "saldo_a_favor": "0",
  "saldos_anteriores": [],
  "total_barras": "0",
  "total_comprado": "19616617.50",
  "total_kilos": "1313.33",
  "total_pagado": "3250000.55"
 },
 "ganancia_por_dia": {
  "costo": "24791781.00",
  "desde": "2026-01-01",
  "dias": [
   {
    "costo": "8580000.00",
    "fecha": "2026-01-08",
    "ganancia": "2574000.00",
    "gastos": "186000.00",
    "ingresos": "11340000.00",
    "kilos": "600.00"
   },
   {
    "costo": "6616892.50",
    "fecha": "2026-01-15",
    "ganancia": "2018440.95",
    "gastos": "129769.05",
    "ingresos": "8765102.50",
    "kilos": "455.33"
   },
   {
    "costo": "0.00",
    "fecha": "2026-01-16",
    "ganancia": "136590.00",
    "gastos": "2610.00",
    "ingresos": "139200.00",
    "kilos": "21.75"
   },
   {
    "costo": "5603742.50",
    "fecha": "2026-02-10",
    "ganancia": "1905257.50",
    "gastos": "114000.00",
    "ingresos": "7623000.00",
    "kilos": "395.00"
   },
   {
    "costo": "1701306.00",
    "fecha": "2026-02-14",
    "ganancia": "723960.00",
    "gastos": "0",
    "ingresos": "2425266.00",
    "kilos": "120.66"
   },
   {
    "costo": "2289840.00",
    "fecha": "2026-02-24",
    "ganancia": "980084.00",
    "gastos": "43036.00",
    "ingresos": "3312960.00",
    "kilos": "162.40"
   }
  ],
  "ganancia": "8338332.45",
  "gastos": "475415.05",
  "hasta": "2026-12-31",
  "ingresos": "33605528.50",
  "kilos": "1755.14"
 },
 "lotes": {
  "barras_fuera_del_reparto": "460",
  "borona_sin_lote": "0",
  "ingreso_sin_lote": "0",
  "kilos_sin_lote": "0",
  "lotes": [
   {
    "borona_recibida": "0",
    "borona_sin_vender": "0",
    "borona_vendida": "0",
    "cerrado": false,
    "compras": 1,
    "costo_borona_vendida": "0.00",
    "costo_kilo": "15200.00",
    "costo_merma": "0.00",
    "costo_sin_vender": "4719600.00",
    "costo_total": "4719600.00",
    "costo_vendido": "0.00",
    "detalle_compras": [
     {
      "borona_recibida": "0",
      "borona_sin_vender": "0",
      "borona_vendida": "0",
      "costo_realizado": "0.00",
      "costo_sin_vender": "4719600.00",
      "ganancia": "0.00",
      "gastos": "0.00",
      "ingresos": "0.00",
      "kilos": "310.50",
      "kilos_a_borona": "0",
      "kilos_merma": "0",
      "kilos_sin_vender": "310.50",
      "kilos_vendidos": "0",
      "margen_kilo": "0",
      "precio_kilo": "15200.00",
      "productor": "Patricia Rojas",
      "saldo": "4719600.00",
      "saldo_a_favor": "0",
      "valor_total": "4719600.00"
     }
    ],
    "detalle_ventas": [],
    "fecha": "2026-02-20",
    "ganancia": "0.00",
    "gastos": "0.00",
    "ingreso_borona": "0.00",
    "ingreso_queso": "0.00",
    "ingresos": "0.00",
    "kilos_a_borona": "0",
    "kilos_comprados": "310.50",
    "kilos_merma": "0",
    "kilos_sin_vender": "310.50",
    "kilos_vendidos": "0",
    "margen_kilo": "0",
    "por_pagar": "4719600.00",
    "precio_venta_kilo": "0",
    "productores": [
     "Patricia Rojas"
    ]
   },
   {
    "borona_recibida": "12.33",
    "borona_sin_vender": "12.33",
    "borona_vendida": "0",
    "cerrado": false,
    "compras": 1,
    "costo_borona_vendida": "0.00",
    "costo_kilo": "15100.00",
    "costo_merma": "0.00",
    "costo_sin_vender": "10570000.00",
    "costo_total": "10570000.00",
    "costo_vendido": "0.00",
    "detalle_compras": [
     {
      "borona_recibida": "12.33",
      "borona_sin_vender": "12.33",
      "borona_vendida": "0",
      "costo_realizado": "0.00",
      "costo_sin_vender": "10570000.00",
      "ganancia": "0.00",
      "gastos": "0.00",
      "ingresos": "0.00",
      "kilos": "700.00",
      "kilos_a_borona": "0",
      "kilos_merma": "0",
      "kilos_sin_vender": "700.00",
      "kilos_vendidos": "0",
      "margen_kilo": "0",
      "precio_kilo": "15100.00",
      "productor": "Sebastián Ruiz",
      "saldo": "10570000.00",
      "saldo_a_favor": "0",
      "valor_total": "10570000.00"
     }
    ],
    "detalle_ventas": [],
    "fecha": "2026-02-03",
    "ganancia": "0.00",
    "gastos": "0.00",
    "ingreso_borona": "0.00",
    "ingreso_queso": "0.00",
    "ingresos": "0.00",
    "kilos_a_borona": "0",
    "kilos_comprados": "700.00",
    "kilos_merma": "0",
    "kilos_sin_vender": "700.00",
    "kilos_vendidos": "0",
    "margen_kilo": "0",
    "por_pagar": "10570000.00",
    "precio_venta_kilo": "0",
    "productores": [
     "Sebastián Ruiz"
    ]
   },
   {
    "borona_recibida": "7.10",
    "borona_sin_vender": "0",
    "borona_vendida": "7.10",
    "cerrado": false,
    "compras": 2,
    "costo_borona_vendida": "0.00",
    "costo_kilo": "14199.94",
    "costo_merma": "0.00",
    "costo_sin_vender": "4120104.00",
    "costo_total": "8746455.00",
    "costo_vendido": "4626351.00",
    "detalle_compras": [
     {
      "borona_recibida": "7.10",
      "borona_sin_vender": "0",
      "borona_vendida": "7.10",
      "costo_realizado": "4626351.00",
      "costo_sin_vender": "1165224.00",
      "ganancia": "1994174.00",
      "gastos": "56551.00",
      "ingresos": "6677076.00",
      "kilos": "410.75",
      "kilos_a_borona": "0",
      "kilos_merma": "0",
      "kilos_sin_vender": "82.64",
      "kilos_vendidos": "328.11",
      "margen_kilo": "5949.03",
      "precio_kilo": "14100.00",
      "productor": "Patricia Rojas",
      "saldo": "0.00",
      "saldo_a_favor": "0",
      "valor_total": "5791575.00"
     },
     {
      "borona_recibida": "0",
      "borona_sin_vender": "0",
      "borona_vendida": "0",
      "costo_realizado": "0.00",
      "costo_sin_vender": "2954880.00",
      "ganancia": "0.00",
      "gastos": "0.00",
      "ingresos": "0.00",
      "kilos": "205.20",
      "kilos_a_borona": "0",
      "kilos_merma": "0",
      "kilos_sin_vender": "205.20",
      "kilos_vendidos": "0",
      "margen_kilo": "0",
      "precio_kilo": "14400.00",
      "productor": "Patricia Rojas",
      "saldo": "2746455.00",
      "saldo_a_favor": "0",
      "valor_total": "2954880.00"
     }
    ],
    "detalle_ventas": [
     {
      "cliente": "Supermercado La 33",
      "costo": "2289840.00",
      "fecha": "2026-02-24",
      "ganancia": "980084.00",
      "gasto": "43036.00",
      "ingreso": "3312960.00",
      "kilos": "162.40",
      "kilos_venta": "162.40",
      "partida": false,
      "precio_kilo": "20400.00",
      "tipo": "queso"
     },
     {
      "cliente": "Panadería El Trigal",
      "costo": "1701306.00",
      "fecha": "2026-02-14",
      "ganancia": "723960.00",
      "gasto": "0.00",
      "ingreso": "2425266.00",
      "kilos": "120.66",
      "kilos_venta": "120.66",
      "partida": false,
      "precio_kilo": "20100.00",
      "tipo": "queso"
     },
     {
      "cliente": "Don José Pérez",
      "costo": "635205.00",
      "fecha": "2026-02-10",
      "ganancia": "243270.00",
      "gasto": "13515.00",
      "ingreso": "891990.00",
      "kilos": "45.05",
      "kilos_venta": "380.00",
      "partida": true,
      "precio_kilo": "19800.00",
      "tipo": "queso"
     },
     {
      "cliente": "Don José Pérez",
      "costo": "0.00",
      "fecha": "2026-02-10",
      "ganancia": "46860.00",
      "gasto": "0.00",
      "ingreso": "46860.00",
      "kilos": "7.10",
      "kilos_venta": "15.00",
      "partida": true,
      "precio_kilo": "6600.00",
      "tipo": "borona"
     }
    ],
    "fecha": "2026-01-12",
    "ganancia": "1994174.00",
    "gastos": "56551.00",
    "ingreso_borona": "46860.00",
    "ingreso_queso": "6630216.00",
    "ingresos": "6677076.00",
    "kilos_a_borona": "0",
    "kilos_comprados": "615.95",
    "kilos_merma": "0",
    "kilos_sin_vender": "287.84",
    "kilos_vendidos": "328.11",
    "margen_kilo": "5949.03",
    "por_pagar": "2746455.00",
    "precio_venta_kilo": "20207.30",
    "productores": [
     "Patricia Rojas"
    ]
   },
   {
    "borona_recibida": "27.75",
    "borona_sin_vender": "30.50",
    "borona_vendida": "29.65",
    "cerrado": false,
    "compras": 2,
    "costo_borona_vendida": "28025.00",
    "costo_kilo": "14492.49",
    "costo_merma": "164462.50",
    "costo_sin_vender": "449875.00",
    "costo_total": "20779767.50",
    "costo_vendido": "20137405.00",
    "detalle_compras": [
     {
      "borona_recibida": "18.25",
      "borona_sin_vender": "0",
      "borona_vendida": "18.25",
      "costo_realizado": "11733150.00",
      "costo_sin_vender": "0.00",
      "ganancia": "3717242.50",
      "gastos": "251032.50",
      "ingresos": "15701425.00",
      "kilos": "820.50",
      "kilos_a_borona": "0",
      "kilos_merma": "0",
      "kilos_sin_vender": "0",
      "kilos_vendidos": "820.50",
      "margen_kilo": "4431.88",
      "precio_kilo": "14300.00",
      "productor": "Patricia Rojas",
      "saldo": "6733150.00",
      "saldo_a_favor": "0",
      "valor_total": "11733150.00"
     },
     {
      "borona_recibida": "9.50",
      "borona_sin_vender": "30.50",
      "borona_vendida": "11.40",
      "costo_realizado": "8596742.50",
      "costo_sin_vender": "449875.00",
      "ganancia": "2462453.45",
      "gastos": "167831.55",
      "ingresos": "11227027.50",
      "kilos": "613.33",
      "kilos_a_borona": "32.40",
      "kilos_merma": "11.15",
      "kilos_sin_vender": "0",
      "kilos_vendidos": "569.78",
      "margen_kilo": "4236.99",
      "precio_kilo": "14750.00",
      "productor": "Sebastián Ruiz",
      "saldo": "5796616.95",
      "saldo_a_favor": "0",
      "valor_total": "9046617.50"
     }
    ],
    "detalle_ventas": [
     {
      "cliente": "Don José Pérez",
      "costo": "4940512.50",
      "fecha": "2026-02-10",
      "ganancia": "1591012.50",
      "gasto": "100485.00",
      "ingreso": "6632010.00",
      "kilos": "334.95",
      "kilos_venta": "380.00",
      "partida": true,
      "precio_kilo": "19800.00",
      "tipo": "queso"
     },
     {
      "cliente": "Don José Pérez",
      "costo": "28025.00",
      "fecha": "2026-02-10",
      "ganancia": "24115.00",
      "gasto": "0.00",
      "ingreso": "52140.00",
      "kilos": "7.90",
      "kilos_venta": "15.00",
      "partida": true,
      "precio_kilo": "6600.00",
      "tipo": "borona"
     },
     {
      "cliente": "Don José Pérez",
      "costo": "0.00",
      "fecha": "2026-01-16",
      "ganancia": "136590.00",
      "gasto": "2610.00",
      "ingreso": "139200.00",
      "kilos": "21.75",
      "kilos_venta": "21.75",
      "partida": false,
      "precio_kilo": "6400.00",
      "tipo": "borona"
     },
     {
      "cliente": "Supermercado La 33",
      "costo": "6616892.50",
      "fecha": "2026-01-15",
      "ganancia": "2018440.95",
      "gasto": "129769.05",
      "ingreso": "8765102.50",
      "kilos": "455.33",
      "kilos_venta": "455.33",
      "partida": false,
      "precio_kilo": "19250.00",
      "tipo": "queso"
     },
     {
      "cliente": "Don José Pérez",
      "costo": "8580000.00",
      "fecha": "2026-01-08",
      "ganancia": "2574000.00",
      "gasto": "186000.00",
      "ingreso": "11340000.00",
      "kilos": "600.00",
      "kilos_venta": "600.00",
      "partida": false,
      "precio_kilo": "18900.00",
      "tipo": "queso"
     }
    ],
    "fecha": "2026-01-05",
    "ganancia": "6179695.95",
    "gastos": "418864.05",
    "ingreso_borona": "191340.00",
    "ingreso_queso": "26737112.50",
    "ingresos": "26928452.50",
    "kilos_a_borona": "32.40",
    "kilos_comprados": "1433.83",
    "kilos_merma": "11.15",
    "kilos_sin_vender": "0",
    "kilos_vendidos": "1390.28",
    "margen_kilo": "4352.11",
    "por_pagar": "12529766.95",
    "precio_venta_kilo": "19231.46",
    "productores": [
     "Patricia Rojas",
     "Sebastián Ruiz"
    ]
   }
  ],
  "mejor": "2026-01-05",
  "peor": "2026-02-20",
  "total_costo": "44815822.50",
  "total_costo_sin_vender": "19859579.00",
  "total_ganancia": "8173869.95",
  "total_ingresos": "33605528.50",
  "total_kilos_comprados": "3060.28",
  "total_kilos_sin_vender": "1298.34",
  "total_por_pagar": "30565821.95"
 },
 "resumen": {
  "barras_compradas": "460",
  "barras_disponibles": "185",
  "barras_pendientes": "185",
  "barras_vendidas": "275",
  "borona_disponible": "42.83",
  "desde": "2026-01-01",
  "existencias": [
   {
    "disponible": "1298.34",
    "etiqueta": "Queso",
    "producto": "queso",
    "unidad": "kg"
   },
   {
    "disponible": "42.83",
    "etiqueta": "Borona",
    "producto": "borona",
    "unidad": "kg"
   },
   {
    "disponible": "185",
    "etiqueta": "Mozzarella",
    "producto": "mozzarella",
    "unidad": "unidad"
   }
  ],
  "ganancia_estimada": "-12589309.05",
  "hasta": "2026-12-31",
  "kilos_a_borona": "32.40",
  "kilos_borona_vendidos": "36.75",
  "kilos_comprados": "3060.28",
  "kilos_disponibles": "1298.34",
  "kilos_merma": "11.15",
  "kilos_pendientes": "1298.34",
  "kilos_vendidos": "1718.39",
  "margen_por_barra": "-3285.82",
  "margen_por_kilo": "-6657.99",
  "por_cobrar_clientes": "25729762.80",
  "por_cobrar_libro_anterior": "1780000.75",
  "por_pagar_libro_anterior": "2815000.20",
  "por_pagar_productores": "37936822.15",
  "por_producto": [
   {
    "barras": "0",
    "barras_vendidas": "0",
    "costo": "25164710.82",
    "costo_barra": "0",
    "costo_kilo": "14644.35",
    "etiqueta": "Vendido como queso",
    "ganancia": "7729812.63",
    "gastos": "472805.05",
    "ingreso": "33367328.50",
    "kilos": "1718.39",
    "kilos_vendidos": "1718.39",
    "nota": "vendido como queso entero",
    "precio_venta_barra": "0",
    "precio_venta_kilo": "19417.79",
    "producto": "queso",
    "unidad": "kg"
   },
   {
    "barras": "0",
    "barras_vendidas": "0",
    "costo": "474477.06",
    "costo_barra": "0",
    "costo_kilo": "14644.35",
    "etiqueta": "Vendido como borona",
    "ganancia": "-238887.06",
    "gastos": "2610.00",
    "ingreso": "238200.00",
    "kilos": "32.40",
    "kilos_vendidos": "36.75",
    "nota": "subproducto vendido más barato",
    "precio_venta_barra": "0",
    "precio_venta_kilo": "6481.63",
    "producto": "borona",
    "unidad": "kg"
   },
   {
    "barras": "0",
    "barras_vendidas": "0",
    "costo": "163284.54",
    "costo_barra": "0",
    "costo_kilo": "14644.35",
    "etiqueta": "Merma (pérdida real)",
    "ganancia": "-163284.54",
    "gastos": "0",
    "ingreso": "0",
    "kilos": "11.15",
    "kilos_vendidos": "11.15",
    "nota": "se pagó y no se vendió: pérdida",
    "precio_venta_barra": "0",
    "precio_venta_kilo": "0.00",
    "producto": "merma",
    "unidad": "kg"
   },
   {
    "barras": "0",
    "barras_vendidas": "0",
    "costo": "19013350.08",
    "costo_barra": "0",
    "costo_kilo": "14644.35",
    "etiqueta": "Aún en inventario",
    "ganancia": "-19013350.08",
    "gastos": "0",
    "ingreso": "0",
    "kilos": "1298.34",
    "kilos_vendidos": "1298.34",
    "nota": "plata invertida, aún sin vender",
    "precio_venta_barra": "0",
    "precio_venta_kilo": "0.00",
    "producto": "pendiente",
    "unidad": "kg"
   },
   {
    "barras": "275",
    "barras_vendidas": "275",
    "costo": "2723695.65",
    "costo_barra": "9904.35",
    "costo_kilo": "0",
    "etiqueta": "Mozzarella vendida (barras)",
    "ganancia": "928704.35",
    "gastos": "117100.00",
    "ingreso": "3769500.00",
    "kilos": "0",
    "kilos_vendidos": "0",
    "nota": "se compra y se vende por barra completa",
    "precio_venta_barra": "13707.27",
    "precio_venta_kilo": "0",
    "producto": "mozzarella",
    "unidad": "barra"
   },
   {
    "barras": "185",
    "barras_vendidas": "0",
    "costo": "1832304.35",
    "costo_barra": "9904.35",
    "costo_kilo": "0",
    "etiqueta": "Mozzarella aún en inventario (barras)",
    "ganancia": "-1832304.35",
    "gastos": "0",
    "ingreso": "0",
    "kilos": "0",
    "kilos_vendidos": "0",
    "nota": "barras compradas y todavía sin vender",
    "precio_venta_barra": "0",
    "precio_venta_kilo": "0",
    "producto": "mozzarella_pendiente",
    "unidad": "barra"
   }
  ],
  "por_productor": [
   {
    "barras": "0",
    "compras": 0,
    "ganancia_estimada": "0",
    "kilos": "0",
    "margen_por_barra": "0",
    "margen_por_kilo": "0",
    "por_pagar": "615000.20",
    "precio_promedio": "0",
    "precio_promedio_barra": "0",
    "productor": "Marleny Gómez",
    "total_comprado": "0",
    "total_comprado_barras": "0"
   },
   {
    "barras": "460",
    "compras": 2,
    "ganancia_estimada": "-903600.00",
    "kilos": "0.00",
    "margen_por_barra": "-1964.35",
    "margen_por_kilo": "0",
    "por_pagar": "4556000.00",
    "precio_promedio": "0",
    "precio_promedio_barra": "9904.35",
    "productor": "Lácteos del Valle",
    "total_comprado": "4556000.00",
    "total_comprado_barras": "4556000.00"
   },
   {
    "barras": "0",
    "compras": 2,
    "ganancia_estimada": "-5398711.98",
    "kilos": "1313.33",
    "margen_por_barra": "0",
    "margen_por_kilo": "-4110.71",
    "por_pagar": "16366616.95",
    "precio_promedio": "14936.55",
    "precio_promedio_barra": "0",
    "productor": "Sebastián Ruiz",
    "total_comprado": "19616617.50",
    "total_comprado_barras": "0.00"
   },
   {
    "barras": "0",
    "compras": 4,
    "ganancia_estimada": "-6286997.07",
    "kilos": "1746.95",
    "margen_por_barra": "0",
    "margen_por_kilo": "-3598.85",
    "por_pagar": "16399205.00",
    "precio_promedio": "14424.69",
    "precio_promedio_barra": "0",
    "productor": "Patricia Rojas",
    "total_comprado": "25199205.00",
    "total_comprado_barras": "0.00"
   }
  ],
  "precio_promedio_compra": "14644.35",
  "precio_promedio_compra_barra": "9904.35",
  "precio_promedio_venta": "19417.79",
  "precio_promedio_venta_barra": "13707.27",
  "total_compras": "49371822.50",
  "total_compras_mozzarella": "4556000.00",
  "total_gastos": "592515.05",
  "total_gastos_mozzarella": "117100.00",
  "total_ventas": "37375028.50",
  "total_ventas_borona": "238200.00",
  "total_ventas_mozzarella": "3769500.00",
  "valor_realizado_barra": "7940.00",
  "valor_realizado_kilo": "10825.84"
 },
 "temporadas": {
  "dias_sin_temporada": 0,
  "mejor": "Temporada febrero",
  "peor": "Temporada enero",
  "proximo_inicio": "2026-02-01",
  "temporadas": [
   {
    "abierta": true,
    "barras_compradas": "120",
    "barras_pendientes": "25",
    "barras_vendidas": "95",
    "cerrada_de_verdad": false,
    "dias": 192,
    "fecha_fin": "2026-08-11",
    "fecha_inicio": "2026-02-01",
    "ganancia": "-2006010.00",
    "kilos_a_borona": "0.00",
    "kilos_borona_vendidos": "15.00",
    "kilos_comprados": "1010.50",
    "kilos_merma": "0.00",
    "kilos_pendientes": "347.44",
    "kilos_vendidos": "663.06",
    "margen_por_kilo": "-3075.55",
    "nombre": "Temporada febrero",
    "notas": null,
    "por_cobrar": "12275460.00",
    "por_pagar": "16513600.00",
    "precio_promedio_compra": "15130.73",
    "precio_promedio_compra_barra": "10200.00",
    "precio_promedio_venta": "20001.55",
    "precio_promedio_venta_barra": "14100.00",
    "total_compras": "16513600.00",
    "total_gastos": "193136.00",
    "total_ventas": "14700726.00"
   },
   {
    "abierta": false,
    "barras_compradas": "340",
    "barras_pendientes": "160",
    "barras_vendidas": "180",
    "cerrada_de_verdad": false,
    "dias": 31,
    "fecha_fin": "2026-01-31",
    "fecha_inicio": "2026-01-01",
    "ganancia": "-10583299.05",
    "kilos_a_borona": "32.40",
    "kilos_borona_vendidos": "21.75",
    "kilos_comprados": "2049.78",
    "kilos_merma": "11.15",
    "kilos_pendientes": "950.90",
    "kilos_vendidos": "1055.33",
    "margen_por_kilo": "-8913.26",
    "nombre": "Temporada enero",
    "notas": null,
    "por_cobrar": "11674302.05",
    "por_pagar": "18608221.95",
    "precio_promedio_compra": "14404.58",
    "precio_promedio_compra_barra": "9800.00",
    "precio_promedio_venta": "19051.01",
    "precio_promedio_venta_barra": "13500.00",
    "total_compras": "32858222.50",
    "total_gastos": "399379.05",
    "total_ventas": "22674302.50"
   }
  ],
  "total_compras": "49371822.50",
  "total_ganancia": "-12589309.05",
  "total_kilos_comprados": "3060.28",
  "total_ventas": "37375028.50"
 }
}
"""


def _esperado() -> dict:
    """La línea base, con los TRES campos que dicen "hoy" puestos al día de hoy.

    NO ES UNA EXCEPCIÓN A LA LÍNEA BASE, ES LO CONTRARIO: sin esto la prueba se ponía
    roja SOLA al otro día de haberla capturado, sin que nadie hubiera movido un peso,
    y una red que grita cuando no pasó nada es una red que se deja de mirar. Los tres
    campos no son cifras de plata:

      · `emitido` en los estados de cuenta es el día en que se imprimió el documento;
      · y una temporada ABIERTA se mide HASTA HOY, así que su `fecha_fin` y sus `dias`
        corren solos con el calendario.

    Se exige lo que de verdad significan (que digan hoy, y que los días sean los que
    van corridos), no el día en que alguien capturó la foto. Ninguna cifra de plata
    depende del calendario: la historia entera pasa entre enero y marzo de 2026, así
    que los totales de la temporada abierta son los mismos hoy que mañana.
    """
    esperado = json.loads(_ESPERADO_JSON)
    hoy = date.today()
    for valor in esperado.values():
        if isinstance(valor, dict) and "emitido" in valor:
            valor["emitido"] = hoy.isoformat()
    for temporada in esperado.get("temporadas", {}).get("temporadas", []):
        if temporada.get("abierta"):
            inicio = date.fromisoformat(temporada["fecha_inicio"])
            temporada["fecha_fin"] = hoy.isoformat()
            temporada["dias"] = (hoy - inicio).days + 1
    return esperado


def _misma_cifra(a, b) -> bool:
    """Si dos valores son LA MISMA CIFRA, comparados como número y no como texto.

    Los números viajan en el JSON como cadenas, y la MISMA cifra puede venir escrita
    con distinta cantidad de decimales según de dónde salga la suma: un cero que sale
    de una suma de SQL llega '0.00' y uno que sale de sumar en Python llega '0'. Los
    dos son cero, y el dueño ve el mismo cero en la pantalla.

    Comparar el texto convertiría cada uno de esos en una alarma, y una prueba que
    grita por cosas que no se movieron es una prueba que se deja de mirar. Lo que esta
    prueba tiene que atrapar es que una cifra CAMBIE DE VALOR: un centavo, un kilo, un
    peso. Eso lo atrapa igual.
    """
    if isinstance(a, str) and isinstance(b, str):
        try:
            return Decimal(a) == Decimal(b)
        except (InvalidOperation, ValueError):
            return a == b
    return a == b


def _diferencias(esperado, obtenido, ruta="") -> list[str]:
    """Todas las cifras que se movieron, con su camino, lo que valía y lo que vale.

    Se listan TODAS y no solo la primera: si el cambio movió el desglose, el dueño
    tiene que poder ver el daño completo de una vez y no arreglarlo de a una.
    """
    fallas: list[str] = []
    if isinstance(esperado, dict) and isinstance(obtenido, dict):
        for clave in sorted(set(esperado) | set(obtenido)):
            if clave not in esperado:
                fallas.append(f"{ruta}.{clave}: APARECIÓ = {obtenido[clave]!r}")
            elif clave not in obtenido:
                fallas.append(f"{ruta}.{clave}: DESAPARECIÓ (valía {esperado[clave]!r})")
            else:
                fallas += _diferencias(esperado[clave], obtenido[clave], f"{ruta}.{clave}")
    elif isinstance(esperado, list) and isinstance(obtenido, list):
        if len(esperado) != len(obtenido):
            fallas.append(
                f"{ruta}: eran {len(esperado)} filas y ahora son {len(obtenido)}"
            )
        for i, (a, b) in enumerate(zip(esperado, obtenido)):
            fallas += _diferencias(a, b, f"{ruta}[{i}]")
    elif not _misma_cifra(esperado, obtenido):
        fallas.append(f"{ruta}: {esperado!r} -> {obtenido!r}")
    return fallas


def test_la_historia_de_los_tres_productos_da_las_mismas_cifras(client, h):
    """LA PRUEBA QUE IMPORTA: ni un peso movido en toda la historia del cliente."""
    construir_historia(client, h)
    obtenido = capturar(client, h)

    if os.environ.get("CAPTURAR_NO_MOVIMIENTO"):
        CAPTURA.write_text(
            json.dumps(obtenido, indent=1, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        pytest.skip(f"cifras capturadas en {CAPTURA}")

    fallas = _diferencias(_esperado(), obtenido)
    if fallas:
        print(f"\n===== SE MOVIERON {len(fallas)} CIFRAS =====")
        for falla in fallas[:120]:
            print("   " + falla)
    assert not fallas, (
        f"la historia de queso, borona y mozzarella dejó de dar las mismas cifras: "
        f"{len(fallas)} diferencias (la primera: {fallas[0] if fallas else ''})"
    )


def test_el_desglose_del_resumen_suma_el_encabezado(client, h):
    """LA REGLA DE ORO sobre esa misma historia: la columna suma la cifra grande.

    Va aparte de la comparación con la línea base a propósito: la línea base dice
    "no se movió", y esta dice "y además está bien". Las dos hacen falta, porque una
    cifra podía estar mal ANTES y quedarse igual de mal.
    """
    construir_historia(client, h)
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()

    def D(v):
        return Decimal(str(v))

    filas = res["por_producto"]
    print("\n===== el desglose por producto =====")
    for f in filas:
        print(f"   {f['etiqueta']:44} {f['unidad']:6} costo={f['costo']:>16} "
              f"ingreso={f['ingreso']:>16} gastos={f['gastos']:>12} "
              f"ganancia={f['ganancia']:>16}")
    suma_costo = sum((D(f["costo"]) for f in filas), Decimal("0"))
    suma_ingreso = sum((D(f["ingreso"]) for f in filas), Decimal("0"))
    suma_gastos = sum((D(f["gastos"]) for f in filas), Decimal("0"))
    suma_ganancia = sum((D(f["ganancia"]) for f in filas), Decimal("0"))
    print(f"   {'SUMA':44} {'':6} costo={suma_costo:>16} ingreso={suma_ingreso:>16} "
          f"gastos={suma_gastos:>12} ganancia={suma_ganancia:>16}")
    print(f"   {'ENCABEZADO':44} {'':6} costo={res['total_compras']:>16} "
          f"ingreso={res['total_ventas']:>16} gastos={res['total_gastos']:>12} "
          f"ganancia={res['ganancia_estimada']:>16}")

    assert suma_costo == D(res["total_compras"]), (
        f"los costos del desglose suman {suma_costo} y las compras del período son "
        f"{res['total_compras']}"
    )
    assert suma_ingreso == D(res["total_ventas"]), (
        f"los ingresos del desglose suman {suma_ingreso} y las ventas del período son "
        f"{res['total_ventas']}"
    )
    assert suma_gastos == D(res["total_gastos"]), (
        f"los gastos del desglose suman {suma_gastos} y los del período son "
        f"{res['total_gastos']}"
    )
    assert suma_ganancia == D(res["ganancia_estimada"]), (
        f"las ganancias del desglose suman {suma_ganancia} y la del período es "
        f"{res['ganancia_estimada']}"
    )
