"""Estado de resultados con el costo de lo VENDIDO, no la leche del mes.

EL PROBLEMA QUE RESUELVE, reportado por el usuario mirando su Contabilidad: se
restaba TODA la leche que entró en el mes contra TODO el queso que se vendió en el
mes. Pero la leche del 1 de julio se convierte en queso que puede venderse 60 días
después: no son el mismo queso. La utilidad salía negativa sin que el negocio
estuviera perdiendo, porque la plata de la leche estaba ahí, en la bodega.

LAS DOS PRUEBAS QUE MANDAN:

1. La utilidad ya no miente: se compra leche y no se vende nada, y el resultado NO
   es una pérdida por el valor de la leche.
2. Los tres renglones de ingresos suman EXACTO el total facturado. Es la parte que
   el usuario verifica a mano, y si no cuadrara desconfiaría del resto.

Los números se imprimen porque él los revisa con calculadora.
"""
from decimal import Decimal

from tests.conftest import auth_headers

# Se reusan los ayudantes de la cadena de producción: es la misma siembra
from tests.test_lotes_produccion import (
    D,
    cliente_nuevo,
    montar_leche,
    producir,
    producto_de,
    recibir,
    tipo_queso,
    vender,
)


def estado(client, h, desde, hasta):
    r = client.get(
        "/api/v1/contabilidad/estado-resultados",
        params={"desde": desde, "hasta": hasta},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def imprimir(e, titulo):
    print(f"\n===== {titulo} =====")
    print(f"  Queso vendido            {e['queso_vendido']:>14}")
    print(f"  Otras ventas             {e['otras_ventas']:>14}")
    print(f"  (-) Descuentos           {e['descuentos']:>14}")
    print(f"  = Total facturado        {e['ingresos_ventas']:>14}")
    print(f"  (-) Costo de lo vendido  {e['costo_queso_vendido']:>14}")
    print(f"  (-) Transporte despachos {e['transporte_despachos']:>14}")
    print(f"  (-) Queso dañado         {e['queso_danado']:>14}")
    print(f"  = UTILIDAD BRUTA         {e['utilidad_bruta']:>14}")
    print(f"  (-) Gastos               {e['total_gastos']:>14}")
    print(f"  = UTILIDAD NETA          {e['utilidad_neta']:>14}")
    print(f"  --- informativo (no entra en la utilidad):")
    print(f"  Leche comprada           {e['costo_leche']:>14}")
    print(f"  Transporte de la leche   {e['costo_transporte']:>14}")
    print(f"  Leche sin usar           {e['leche_sin_usar']:>14}")
    print(f"  Queso en bodega          {e['queso_en_bodega']:>14}")


# ---------------------------------------------------------------------------
# 1. La utilidad ya no miente
# ---------------------------------------------------------------------------
def test_comprar_leche_y_no_vender_no_es_perdida(client, base_datos):
    """El caso que hacía salir el número negativo: entra leche, se hace queso, y
    todavía no se ha vendido. Antes eso era una "pérdida" del valor de la leche.
    Ahora la utilidad es cero y la plata aparece como inventario."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # 1.000 litros: 1.800.000 de leche + 100.000 de flete
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-05", tipo, litros=1000, kilos=100)

    e = estado(client, h, "2026-07-01", "2026-07-31")
    imprimir(e, "1. SE COMPRÓ Y NO SE VENDIÓ")
    # No se vendió nada, así que no hay ingresos NI costo de lo vendido
    assert D(e["ingresos_ventas"]) == 0
    assert D(e["costo_queso_vendido"]) == 0
    # LA CIFRA QUE ANTES MENTÍA: la utilidad es 0, no -1.900.000
    assert D(e["utilidad_bruta"]) == 0
    assert D(e["utilidad_bruta"]) != -D(e["costo_leche"]) - D(e["costo_transporte"])
    # Y la plata aparece donde está: en el queso de la bodega
    assert D(e["costo_leche"]) == 1_800_000
    assert D(e["costo_transporte"]) == 100_000
    assert D(e["queso_en_bodega"]) == 1_900_000
    assert D(e["leche_sin_usar"]) == 0


def test_la_leche_que_no_se_ha_usado_sale_como_inventario(client, base_datos):
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    # Solo se usan 600 de los 1.000 litros
    producir(client, h, "2026-07-05", tipo, litros=600, kilos=60)

    e = estado(client, h, "2026-07-01", "2026-07-31")
    imprimir(e, "2. LECHE QUE SIGUE EN EL TANQUE")
    assert D(e["utilidad_bruta"]) == 0
    # 400 litros a 1.800 + su parte del flete (40.000)
    assert D(e["leche_sin_usar"]) == 400 * 1800 + 40_000
    # Y los 600 litros que sí se usaron están en el queso de la bodega
    assert D(e["queso_en_bodega"]) == 600 * 1800 + 60_000
    # Los dos juntos son toda la leche que se compró: nada se perdió
    assert (
        D(e["leche_sin_usar"]) + D(e["queso_en_bodega"])
        == D(e["costo_leche"]) + D(e["costo_transporte"])
    )


def test_el_queso_del_mes_pasado_vendido_este_mes(client, base_datos):
    """El caso de los 60 días: el queso se hizo en julio y se vendió en septiembre.
    En julio la utilidad es cero (no se vendió) y en septiembre aparece completa,
    con el costo de la leche de julio."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-05", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    vender(client, h, "2026-09-10", cliente, producto_de(client, h, tipo),
           kilos=100, precio=25000)

    julio = estado(client, h, "2026-07-01", "2026-07-31")
    septiembre = estado(client, h, "2026-09-01", "2026-09-30")
    imprimir(julio, "3a. JULIO (se hizo el queso)")
    imprimir(septiembre, "3b. SEPTIEMBRE (se vendió)")

    # Julio: se pagó la leche pero no se vendió nada -> utilidad cero, no pérdida
    assert D(julio["utilidad_bruta"]) == 0
    assert D(julio["costo_leche"]) == 1_800_000
    assert D(julio["queso_en_bodega"]) == 0  # ya se vendió, así que hoy no hay nada
    # Septiembre: el ingreso Y el costo de la leche de julio, juntos
    assert D(septiembre["ingresos_ventas"]) == 2_500_000
    assert D(septiembre["costo_queso_vendido"]) == 1_900_000
    assert D(septiembre["utilidad_bruta"]) == 600_000
    # Y en septiembre no se compró leche: la cifra informativa está en cero
    assert D(septiembre["costo_leche"]) == 0


# ---------------------------------------------------------------------------
# 4. Los tres renglones de ingresos suman el total facturado
# ---------------------------------------------------------------------------
def test_los_ingresos_abiertos_suman_el_total_facturado(client, base_datos):
    """Es el desglose que el usuario cuadra a mano: queso + otras ventas -
    descuentos tiene que dar el total facturado, exacto."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-02", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    producto = producto_de(client, h, tipo)
    # Una venta con descuento
    r = client.post(
        "/api/v1/ventas",
        json={"cliente_id": cliente["id"], "fecha": "2026-07-15", "descuento": "150000",
              "detalles": [{"producto_id": producto["id"], "cantidad": "80",
                            "precio_unitario": "25000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    # Y un producto que NO es queso (un insumo que se revende)
    otro = client.post(
        "/api/v1/inventario/productos",
        json={"nombre": "Bolsas", "categoria": "insumo", "unidad": "unidad"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/inventario/movimientos",
        json={"producto_id": otro["id"], "fecha": "2026-07-10", "tipo": "entrada",
              "cantidad": "500", "costo_unitario": "100"},
        headers=h,
    )
    r2 = client.post(
        "/api/v1/ventas",
        json={"cliente_id": cliente["id"], "fecha": "2026-07-16",
              "detalles": [{"producto_id": otro["id"], "cantidad": "200",
                            "precio_unitario": "300"}]},
        headers=h,
    )
    assert r2.status_code == 201, r2.text

    e = estado(client, h, "2026-07-01", "2026-07-31")
    imprimir(e, "4. LOS INGRESOS ABIERTOS")
    # 80 kg x 25.000 = 2.000.000 de queso; 200 bolsas x 300 = 60.000 de otras
    assert D(e["queso_vendido"]) == 2_000_000
    assert D(e["otras_ventas"]) == 60_000
    assert D(e["descuentos"]) == 150_000
    # EL CUADRE: los tres renglones dan el total facturado
    assert D(e["queso_vendido"]) + D(e["otras_ventas"]) - D(e["descuentos"]) == D(
        e["ingresos_ventas"]
    )
    assert D(e["ingresos_ventas"]) == 1_910_000  # 2.000.000 + 60.000 - 150.000


def test_el_transporte_del_despacho_se_resta(client, base_datos):
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-02", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    r = client.post(
        "/api/v1/ventas",
        json={"cliente_id": cliente["id"], "fecha": "2026-07-15",
              "gasto_concepto": "Transporte a Bogotá", "gasto_por_kilo": "1200",
              "detalles": [{"producto_id": producto_de(client, h, tipo)["id"],
                            "cantidad": "100", "precio_unitario": "25000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text

    e = estado(client, h, "2026-07-01", "2026-07-31")
    imprimir(e, "5. EL FLETE DEL DESPACHO")
    assert D(e["transporte_despachos"]) == 120_000
    # 2.500.000 - 1.900.000 - 120.000
    assert D(e["utilidad_bruta"]) == 480_000
    # El flete NO está dentro de lo que pagó el cliente
    assert D(e["ingresos_ventas"]) == 2_500_000


def test_el_queso_danado_si_se_resta(client, base_datos):
    """Lo que se dañó sí es pérdida del mes: salió sin dejar un peso."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-02", tipo, litros=1000, kilos=100)
    producto = producto_de(client, h, tipo)
    cliente = cliente_nuevo(client, h)
    vender(client, h, "2026-07-15", cliente, producto, kilos=60, precio=25000)
    # 20 kg se dañaron
    client.post(
        "/api/v1/inventario/movimientos",
        json={"producto_id": producto["id"], "fecha": "2026-07-20", "tipo": "ajuste",
              "cantidad": "-20", "referencia": "Se dañó en bodega"},
        headers=h,
    )

    e = estado(client, h, "2026-07-01", "2026-07-31")
    imprimir(e, "6. QUESO QUE SE DAÑÓ")
    assert D(e["queso_danado"]) == 380_000  # 20 kg x 19.000
    # 1.500.000 - 1.140.000 - 380.000
    assert D(e["utilidad_bruta"]) == -20_000
    # Y los 20 kg que quedan siguen como inventario
    assert D(e["queso_en_bodega"]) == 380_000


def test_avisa_del_queso_vendido_que_no_se_pudo_costear(client, base_datos):
    """Si se vendió queso que no salió de ninguna producción, su costo no se sabe y
    la utilidad se ve mejor de lo que es. No se puede callar."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-10", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    # Despacho con fecha ANTERIOR a la producción: no hay lote de dónde salga
    vender(client, h, "2026-06-20", cliente, producto_de(client, h, tipo),
           kilos=30, precio=25000)

    e = estado(client, h, "2026-06-01", "2026-06-30")
    imprimir(e, "7. VENDIDO SIN COSTO CONOCIDO")
    assert D(e["queso_vendido_sin_costo"]) == 750_000
    # El ingreso está, pero sin costo: la utilidad se ve completa y eso se avisa
    assert D(e["ingresos_ventas"]) == 750_000
    assert D(e["costo_queso_vendido"]) == 0


def test_la_contabilidad_y_la_pantalla_de_lotes_dicen_lo_mismo(client, base_datos):
    """Las dos usan el MISMO reparto. Si cada una armara el suyo, podrían acabar
    diciendo cosas distintas del mismo queso, y eso es lo que hace desconfiar."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Carmen"], 800, transportador)
    recibir(client, h, "2026-07-08", prov["Libardo"], 900, transportador)
    producir(client, h, "2026-07-02", tipo, litros=800, kilos=84)
    producir(client, h, "2026-07-09", tipo, litros=900, kilos=95)
    cliente = cliente_nuevo(client, h)
    producto = producto_de(client, h, tipo)
    client.post(
        "/api/v1/ventas",
        json={"cliente_id": cliente["id"], "fecha": "2026-07-20",
              "gasto_concepto": "Transporte a Bogotá", "gasto_por_kilo": "1100",
              "detalles": [{"producto_id": producto["id"], "cantidad": "120",
                            "precio_unitario": "25500"}]},
        headers=h,
    )

    e = estado(client, h, "2026-07-01", "2026-07-31")
    p = client.get("/api/v1/produccion/lotes", headers=h).json()
    de_lotes_costo = sum(
        D(v["costo"]) for l in p["lotes"] for v in l["detalle_ventas"]
    )
    de_lotes_flete = sum(
        D(v["gasto"]) for l in p["lotes"] for v in l["detalle_ventas"]
    )
    de_lotes_bodega = sum(D(l["costo_en_bodega"]) for l in p["lotes"])
    print("\n===== 8. LAS DOS PANTALLAS DICEN LO MISMO =====")
    print(f"  costo de lo vendido: contabilidad {e['costo_queso_vendido']}"
          f" | lotes {de_lotes_costo}")
    print(f"  transporte:          contabilidad {e['transporte_despachos']}"
          f" | lotes {de_lotes_flete}")
    print(f"  queso en bodega:     contabilidad {e['queso_en_bodega']}"
          f" | lotes {de_lotes_bodega}")
    assert D(e["costo_queso_vendido"]) == de_lotes_costo
    assert D(e["transporte_despachos"]) == de_lotes_flete
    assert D(e["queso_en_bodega"]) == de_lotes_bodega


def test_sin_datos_no_revienta(client, base_datos):
    h = auth_headers(client, "admin.a")
    e = estado(client, h, "2026-07-01", "2026-07-31")
    print("\n===== 9. SIN DATOS =====")
    print(f"  utilidad bruta {e['utilidad_bruta']} | neta {e['utilidad_neta']}")
    assert D(e["utilidad_bruta"]) == 0
    assert D(e["utilidad_neta"]) == 0
    assert D(e["queso_en_bodega"]) == 0
    assert D(e["leche_sin_usar"]) == 0
