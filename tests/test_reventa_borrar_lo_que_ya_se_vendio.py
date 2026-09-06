"""BORRAR NO PUEDE DEJAR LA BODEGA EN NEGATIVO — las cuatro puertas, con cifras.

QUÉ PASABA Y CUÁNTO COSTABA. Anular una compra cuyo queso ya se vendió estaba
prohibido desde hace rato, y editarla para bajarle los kilos también. BORRARLA no:
137,45 kg comprados, 120,23 vendidos, y el DELETE respondía 204. La bodega quedaba en
-120,23 kg, y desde ahí NINGUNA venta volvía a pasar el control de existencias —el
dueño se queda sin poder trabajar sin entender por qué—. Además esos 120,23 kg
vendidos se quedaban sin compra de dónde sacar su costo, así que la ganancia del
resumen salía inflada por todo lo que costaron.

Lo mismo con los ajustes (las conversiones), que ni siquiera tenían dónde preguntar:
44,23 kg pasados a borona, 40,00 vendidos, y borrar el ajuste dejaba la borona en
-40,00 kg.

Y con dos cosas más que encontró la revisión de la familia completa:

  · LOS KILOS QUE LLEGAN GRATIS ENCIMA DE UNA COMPRA no los miraba NADIE —ni borrar,
    ni anular, ni editar—. Esa mercancía es la más frágil de todas porque no tiene
    compra propia: si se va la compra que la trajo, no queda nada sosteniéndola.
  · LA FACTURA ENTERA se validaba renglón por renglón: dos renglones de 100 kg con 50
    vendidos pasan de a uno —100 cabe en los 150 que hay— y entre los dos se llevan
    200.
  · EL RECHAZO MANDABA AL DUEÑO A BUSCAR UNA VENTA QUE NO EXISTE. El disponible baja
    por DOS caminos —las ventas y los ajustes— y el mensaje afirmaba siempre "ya se
    vendió": con 137,45 kg de queso, 44,23 pasados a borona y CERO vendidos, le decía
    "Anule primero las ventas que se lo llevaron". Revisa sus ventas, no encuentra
    ninguna, y se queda con la compra mal anotada. Y la puerta de EDITAR ni siquiera
    ofrecía salida: decía cuánto quedaba y nada más.

Lo que estas pruebas fijan: que las cuatro puertas rebotan cuando tienen que rebotar,
que no dejan NADA a medio escribir cuando rebotan, que lo que sí se puede borrar se
borra y devuelve el inventario exacto, y que después de un rechazo el dueño tiene por
dónde salir.
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import (
    API,
    CERO,
    D,
    exigir_quieto,
    foto,
    regla_de_oro,
    resumen,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ------------------------------------------------------------------ ayudas
def existencias(client, h) -> dict:
    """Lo que el resumen dice que hay en bodega, por producto."""
    return {e["producto"]: D(e["disponible"]) for e in resumen(client, h)["existencias"]}


def comprar(client, h, **campos):
    return client.post(f"{API}/compras", json=campos, headers=h)


def vender(client, h, **campos):
    return client.post(f"{API}/ventas", json=campos, headers=h)


def ajustar(client, h, **campos):
    return client.post(f"{API}/conversiones", json=campos, headers=h)


def detalle(r) -> str:
    return r.json().get("error", {}).get("detail", "")


def compra_guardada(client, h, compra_id):
    """La compra tal como quedó en la base, o None si ya no está.

    Se busca en la lista porque no hay puerta para pedir UNA compra suelta; lo que
    importa aquí es si sigue viva después de un rechazo.
    """
    r = client.get(f"{API}/compras", params={"size": 100}, headers=h)
    assert r.status_code == 200, r.text
    return next((c for c in r.json()["items"] if c["id"] == compra_id), None)


def historia_corta(client, h):
    """Se compran 137,45 kg a $14.317 y se venden 120,23. Quedan 17,22 kg."""
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317")
    assert c.status_code == 201, c.text
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="120.23", precio_kilo="21533",
               gasto_por_kilo="137")
    assert v.status_code == 201, v.text
    return c.json(), v.json()


def historia_con_borona_gratis(client, h):
    """La compra trae 25,36 kg de borona GRATIS encima; se venden 20,11.

    Quedan 5,25 kg de borona que NO tienen compra propia: la única fila que los
    sostiene es la compra del queso.
    """
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317", borona_kilos="25.36")
    assert c.status_code == 201, c.text
    v = vender(client, h, fecha="2026-02-12", cliente="Tienda La Esquina",
               tipo="borona", kilos="20.11", precio_kilo="4133")
    assert v.status_code == 201, v.text
    return c.json(), v.json()


# ============================================================================
# 1. BORRAR EL RENGLÓN
# ============================================================================
def test_borrar_una_compra_cuya_mercancia_ya_se_vendio_rebota(client, h):
    """137,45 kg comprados, 120,23 vendidos, 17,22 en bodega: el DELETE rebota."""
    compra, _ = historia_corta(client, h)
    antes = existencias(client, h)
    assert antes["queso"] == D("17.22"), "la historia no quedó como se esperaba"
    retrato = foto(client, h)

    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    print(f"\n   DELETE de la compra ya vendida -> {r.status_code} · {detalle(r)}")
    assert r.status_code == 422, "se dejó borrar una compra cuyo queso ya se vendió"

    # Y NO QUEDÓ NADA A MEDIO ESCRIBIR: ni una cifra del negocio se movió. Es lo que
    # de verdad importa de un rechazo — la compra se borra con sus adjuntos y su
    # cabecera, así que un rechazo a mitad de camino dejaría la factura partida.
    exigir_quieto(retrato, foto(client, h), "el borrado rechazado")
    assert existencias(client, h)["queso"] == D("17.22")
    sigue = compra_guardada(client, h, compra["id"])
    assert sigue is not None, "la compra rechazada desapareció de todos modos"
    assert D(sigue["kilos_netos"]) == D("137.45")


def test_el_rechazo_le_dice_al_dueno_las_dos_cifras_y_la_salida(client, h):
    """Un rechazo sin salida es un rechazo a medias: tiene que decir qué hacer."""
    compra, _ = historia_corta(client, h)
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    texto = detalle(r)
    print(f"\n   mensaje: {texto}")
    assert r.status_code == 422
    assert "ya se vendió" in texto
    assert "137.45" in texto, "no dice cuánto trajo la compra que se quiere borrar"
    assert "17.22" in texto, "no dice cuánto queda sin vender"
    assert "Anule primero las ventas" in texto, "no dice por dónde salir"
    assert "corrija la compra" in texto


def test_borrar_una_compra_sin_vender_si_pasa_y_devuelve_el_inventario_exacto(client, h):
    """El guardia no puede volverse un estorbo: lo que no ha salido sí se borra.

    Dos compras, 137,45 kg y 44,23 kg. Se borra la segunda y la bodega tiene que
    quedar EXACTAMENTE en la primera, ni un gramo de más ni de menos.
    """
    uno = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                  kilos_brutos="137.45", precio_kilo="14317")
    dos = comprar(client, h, fecha="2026-02-05", productor="Sebastián Ruiz",
                  kilos_brutos="44.23", precio_kilo="14317")
    assert uno.status_code == 201 and dos.status_code == 201
    res = resumen(client, h)
    print(f"\n   con las dos compras: bodega {existencias(client, h)['queso']} kg, "
          f"total_compras {res['total_compras']}")
    assert existencias(client, h)["queso"] == D("181.68")  # 137,45 + 44,23
    assert D(res["total_compras"]) == D("2601112.56")  # 1.967.871,65 + 633.240,91

    r = client.delete(f"{API}/compras/{dos.json()['id']}", headers=h)
    print(f"   DELETE de la que no se ha vendido -> {r.status_code}")
    assert r.status_code == 204, r.text

    res = resumen(client, h)
    print(f"   después: bodega {existencias(client, h)['queso']} kg, "
          f"total_compras {res['total_compras']}")
    assert existencias(client, h)["queso"] == D("137.45")
    assert D(res["total_compras"]) == D("1967871.65")
    regla_de_oro(res, "tras borrar la compra sin vender")


def test_borrar_hasta_el_ultimo_gramo_disponible_si_se_puede(client, h):
    """El borde exacto: se compran dos veces y se vende justo lo de la primera.

    120,23 + 137,45 = 257,68 comprados, 120,23 vendidos. Borrar la compra de 137,45
    deja la bodega en CERO clavado, que es el último borrado que la regla permite.
    """
    uno = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                  kilos_brutos="120.23", precio_kilo="14317")
    dos = comprar(client, h, fecha="2026-02-04", productor="Sebastián Ruiz",
                  kilos_brutos="137.45", precio_kilo="14317")
    assert uno.status_code == 201 and dos.status_code == 201
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="120.23", precio_kilo="21533")
    assert v.status_code == 201, v.text
    assert existencias(client, h)["queso"] == D("137.45")

    r = client.delete(f"{API}/compras/{dos.json()['id']}", headers=h)
    print(f"\n   DELETE que deja la bodega en cero clavado -> {r.status_code}")
    assert r.status_code == 204, r.text
    assert existencias(client, h)["queso"] == CERO

    # Y un gramo más ya no: la otra compra está sosteniendo lo que se vendió.
    r = client.delete(f"{API}/compras/{uno.json()['id']}", headers=h)
    print(f"   DELETE de la que sostiene lo vendido -> {r.status_code} · {detalle(r)}")
    assert r.status_code == 422
    assert existencias(client, h)["queso"] == CERO


# ============================================================================
# 2. LOS KILOS QUE LLEGARON GRATIS (la mercancía sin compra propia)
# ============================================================================
def test_borrar_la_compra_que_trajo_los_kilos_gratis_ya_vendidos_rebota(client, h):
    """El queso de la compra no se ha tocado; la borona que trajo encima sí.

    25,36 kg de borona gratis, 20,11 vendidos, 5,25 en bodega. Borrar la compra se
    lleva los 25,36 y deja la borona en -20,11.
    """
    compra, _ = historia_con_borona_gratis(client, h)
    antes = existencias(client, h)
    print(f"\n   antes: {dict(antes)}")
    assert antes["queso"] == D("137.45") and antes["borona"] == D("5.25")
    retrato = foto(client, h)

    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    texto = detalle(r)
    print(f"   DELETE -> {r.status_code} · {texto}")
    assert r.status_code == 422, (
        "se borró la compra que sostenía una borona ya vendida: el queso estaba "
        "entero, pero la borona no tiene otra fila que la sostenga"
    )
    assert "Borona" in texto, "el mensaje habla del producto equivocado"
    assert "gratis" in texto, "no le dice al dueño de qué kilos le está hablando"
    assert "25.36" in texto and "5.25" in texto
    exigir_quieto(retrato, foto(client, h), "el borrado rechazado por la borona")


def test_anular_esa_misma_compra_tambien_rebota_por_la_borona(client, h):
    """Anular le quita a la bodega EXACTAMENTE lo mismo que borrar, así que tiene
    que exigir lo mismo. Este guardia miraba solo el queso y dejaba pasar la borona
    que la compra traía encima."""
    compra, _ = historia_con_borona_gratis(client, h)
    retrato = foto(client, h)
    r = client.post(f"{API}/compras/{compra['id']}/anular", headers=h)
    print(f"\n   POST anular -> {r.status_code} · {detalle(r)}")
    assert r.status_code == 422, "anular se llevó una borona que ya estaba vendida"
    assert "Borona" in detalle(r)
    exigir_quieto(retrato, foto(client, h), "la anulación rechazada por la borona")


def test_bajarle_los_kilos_gratis_a_una_compra_ya_vendida_rebota(client, h):
    """La tercera puerta de la misma mercancía: editarla para quitarle lo gratis.

    De 25,36 kg gratis con 20,11 vendidos, bajarlos a 5,00 dejaría la borona en
    -15,11. Bajarlos hasta 20,11 —lo que ya salió— sí se puede: deja cero.
    """
    compra, _ = historia_con_borona_gratis(client, h)
    r = client.put(f"{API}/compras/{compra['id']}",
                   json={"borona_kilos": "5.00"}, headers=h)
    print(f"\n   PUT bajando la borona gratis de 25,36 a 5,00 -> {r.status_code} · "
          f"{detalle(r)}")
    assert r.status_code == 422
    assert existencias(client, h)["borona"] == D("5.25")

    ok = client.put(f"{API}/compras/{compra['id']}",
                    json={"borona_kilos": "20.11"}, headers=h)
    print(f"   PUT bajándola hasta lo vendido (20,11) -> {ok.status_code}")
    assert ok.status_code == 200, ok.text
    assert existencias(client, h)["borona"] == CERO


def test_corregirle_el_precio_a_una_compra_con_borona_gratis_sigue_pasando(client, h):
    """EL GUARDIA NUEVO NO PUEDE ESTORBAR LO DE TODOS LOS DÍAS.

    Por PUT llega un payload PARCIAL: corregir el precio no manda los kilos gratis,
    y si el guardia leyera cero donde la compra tiene 25,36 rebotaría una corrección
    que no le mueve un kilo a nadie.
    """
    compra, _ = historia_con_borona_gratis(client, h)
    r = client.put(f"{API}/compras/{compra['id']}",
                   json={"precio_kilo": "15033"}, headers=h)
    print(f"\n   PUT corrigiendo solo el precio -> {r.status_code} · {detalle(r)}")
    assert r.status_code == 200, r.text
    # 137,45 × 15.033 = 2.066.285,85
    assert D(r.json()["valor_total"]) == D("2066285.85")
    assert D(r.json()["borona_kilos"]) == D("25.36"), "el PUT parcial borró lo gratis"
    disponible = existencias(client, h)
    assert disponible["queso"] == D("137.45") and disponible["borona"] == D("5.25")


# ============================================================================
# 3. LA FACTURA ENTERA (el conjunto, no renglón por renglón)
# ============================================================================
def factura_de_dos_renglones(client, h):
    """Una factura de compra con dos renglones de 100 kg: 200 kg en total."""
    r = client.post(f"{API}/documentos", json={
        "tipo": "compra", "fecha": "2026-02-03", "tercero": "Patricia Rojas",
        "renglones": [
            {"tipo": "queso", "kilos_brutos": "100.00", "precio_kilo": "14317"},
            {"tipo": "queso", "kilos_brutos": "100.00", "precio_kilo": "15033"},
        ]}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_borrar_la_factura_entera_se_mide_sumando_sus_renglones(client, h):
    """EL CASO QUE SOLO CAE SUMANDO: 200 kg en dos renglones, 50 vendidos.

    Quedan 150 kg. Renglón por renglón los dos pasan —100 cabe en 150— y entre los
    dos se llevan 200: la bodega quedaría en -50. Sumando primero, rebota.
    """
    factura = factura_de_dos_renglones(client, h)
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="50.00", precio_kilo="21533")
    assert v.status_code == 201, v.text
    assert existencias(client, h)["queso"] == D("150.00")
    retrato = foto(client, h)

    r = client.delete(f"{API}/documentos/{factura['id']}", headers=h)
    print(f"\n   DELETE de la factura de 100 + 100 con 50 vendidos -> "
          f"{r.status_code} · {detalle(r)}")
    assert r.status_code == 422, (
        "cada renglón cabía por separado en los 150 kg disponibles, pero los dos "
        "juntos se llevaban 200"
    )
    assert "esta factura" in detalle(r), "el mensaje habla de un renglón y no de la factura"
    exigir_quieto(retrato, foto(client, h), "el borrado rechazado de la factura")
    sigue = client.get(f"{API}/documentos/{factura['id']}", headers=h)
    assert sigue.status_code == 200
    assert sigue.json()["cantidad_renglones"] == 2, "la factura quedó partida"


def test_borrar_la_factura_entera_sin_vender_si_pasa(client, h):
    """Sin nada vendido, la factura se va completa y la bodega queda en cero."""
    factura = factura_de_dos_renglones(client, h)
    assert existencias(client, h)["queso"] == D("200.00")
    r = client.delete(f"{API}/documentos/{factura['id']}", headers=h)
    print(f"\n   DELETE de la factura sin vender -> {r.status_code}")
    assert r.status_code == 204, r.text
    assert existencias(client, h).get("queso", CERO) == CERO
    assert client.get(f"{API}/documentos/{factura['id']}", headers=h).status_code == 404
    res = resumen(client, h)
    assert D(res["total_compras"]) == CERO
    regla_de_oro(res, "tras borrar la factura entera")


def test_anular_la_factura_entera_rebota_y_no_la_deja_medio_anulada(client, h):
    """LA CUARTA PUERTA DE LA FACTURA: anularla completa.

    Esta no valida el conjunto de una vez, y no le hace falta: anula renglón por
    renglón y CADA anulación le baja el disponible a la siguiente, así que el segundo
    renglón se mide contra los 50 kg que quedan y rebota. La suma no se puede colar.

    LO QUE SÍ HABÍA QUE FIJAR ES QUE NO QUEDE PARTIDA. El primer renglón sí alcanzaba
    a anularse antes del rechazo del segundo; si la transacción no se devolviera, la
    factura quedaría con un renglón anulado y otro vivo, y el `total` que el dueño
    lee en su papel dejaría de ser la suma de sus renglones.
    """
    factura = factura_de_dos_renglones(client, h)
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="50.00", precio_kilo="21533")
    assert v.status_code == 201, v.text
    retrato = foto(client, h)

    r = client.post(f"{API}/documentos/{factura['id']}/anular", headers=h)
    print(f"\n   ANULAR la factura de 100 + 100 con 50 vendidos -> "
          f"{r.status_code} · {detalle(r)}")
    assert r.status_code == 422
    exigir_quieto(retrato, foto(client, h), "la anulación rechazada de la factura")

    doc = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    # 100 × 14.317 + 100 × 15.033 = 2.935.000, y NADA anulado.
    assert D(doc["total"]) == D("2935000.00"), "la factura quedó medio anulada"
    assert D(doc["total_anulado"]) == CERO
    assert existencias(client, h)["queso"] == D("150.00")


def test_anular_la_factura_entera_sin_vender_si_pasa(client, h):
    """Y el guardia no se volvió un candado: sin nada vendido la factura se anula
    completa, y sus 2.935.000 se pasan enteros a la columna de lo anulado."""
    factura = factura_de_dos_renglones(client, h)
    r = client.post(f"{API}/documentos/{factura['id']}/anular", headers=h)
    print(f"\n   ANULAR la factura sin vender -> {r.status_code}")
    assert r.status_code == 200, r.text
    doc = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    assert D(doc["total"]) == CERO
    assert D(doc["total_anulado"]) == D("2935000.00")
    assert existencias(client, h)["queso"] == CERO


def test_rehacer_los_renglones_de_una_factura_vendida_sigue_pasando(client, h):
    """LO QUE NO PUEDE ROMPERSE: rehacer los renglones BORRA y vuelve a escribir.

    Si ese borrado intermedio preguntara por la bodega, la mediría a mitad del
    reemplazo —los viejos borrados y los nuevos sin escribir— y corregirle el precio
    a una factura ya vendida sería imposible.
    """
    factura = factura_de_dos_renglones(client, h)
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="150.00", precio_kilo="21533")
    assert v.status_code == 201, v.text
    assert existencias(client, h)["queso"] == D("50.00")

    r = client.put(f"{API}/documentos/{factura['id']}", json={
        "tipo": "compra",
        "renglones": [
            {"tipo": "queso", "kilos_brutos": "100.00", "precio_kilo": "14500"},
            {"tipo": "queso", "kilos_brutos": "100.00", "precio_kilo": "15100"},
        ]}, headers=h)
    print(f"\n   PUT rehaciendo los renglones con los mismos kilos -> "
          f"{r.status_code} · {detalle(r)}")
    assert r.status_code == 200, r.text
    assert existencias(client, h)["queso"] == D("50.00")
    # 100 × 14.500 + 100 × 15.100 = 2.960.000
    assert D(r.json()["total"]) == D("2960000.00")

    # Y bajar el conjunto por debajo de lo vendido sigue rebotando.
    malo = client.put(f"{API}/documentos/{factura['id']}", json={
        "tipo": "compra",
        "renglones": [{"tipo": "queso", "kilos_brutos": "100.00",
                       "precio_kilo": "14500"}]}, headers=h)
    print(f"   PUT dejando 100 kg cuando se vendieron 150 -> {malo.status_code} · "
          f"{detalle(malo)}")
    assert malo.status_code == 422
    assert existencias(client, h)["queso"] == D("50.00")


# ============================================================================
# 4. LOS AJUSTES (conversiones)
# ============================================================================
def test_borrar_un_ajuste_cuyo_subproducto_ya_se_vendio_rebota(client, h):
    """137,45 kg de queso, 44,23 pasados a borona, 40,00 de borona vendidos.

    Borrar el ajuste le devuelve los 44,23 al queso y se los quita a la borona, que
    quedaría en -40,00. Este servicio no tenía NINGÚN control de borrado.
    """
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317")
    assert c.status_code == 201, c.text
    a = ajustar(client, h, fecha="2026-02-10", kilos="44.23", destino="borona",
                precio_kilo="1833.33")
    assert a.status_code == 201, a.text
    v = vender(client, h, fecha="2026-02-12", cliente="Tienda La Esquina",
               tipo="borona", kilos="40.00", precio_kilo="4133")
    assert v.status_code == 201, v.text
    antes = existencias(client, h)
    print(f"\n   antes: {dict(antes)}")
    assert antes["queso"] == D("93.22") and antes["borona"] == D("4.23")
    retrato = foto(client, h)

    r = client.delete(f"{API}/conversiones/{a.json()['id']}", headers=h)
    texto = detalle(r)
    print(f"   DELETE del ajuste -> {r.status_code} · {texto}")
    assert r.status_code == 422, "se borró un ajuste cuya borona ya estaba vendida"
    assert "Borona" in texto and "44.23" in texto and "4.23" in texto
    assert "ventas de Borona" in texto, "no le dice al dueño qué deshacer primero"
    exigir_quieto(retrato, foto(client, h), "el borrado rechazado del ajuste")


def test_borrar_un_ajuste_sin_vender_devuelve_los_kilos_exactos(client, h):
    """Sin borona vendida, el ajuste se borra y los 44,23 kg vuelven al queso."""
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317")
    assert c.status_code == 201, c.text
    a = ajustar(client, h, fecha="2026-02-10", kilos="44.23", destino="borona",
                precio_kilo="1833.33")
    assert a.status_code == 201, a.text
    assert existencias(client, h) == {"queso": D("93.22"), "borona": D("44.23"),
                                      "mozzarella": CERO}

    r = client.delete(f"{API}/conversiones/{a.json()['id']}", headers=h)
    print(f"\n   DELETE del ajuste sin vender -> {r.status_code}")
    assert r.status_code == 204, r.text
    despues = existencias(client, h)
    print(f"   después: {dict(despues)}")
    assert despues["queso"] == D("137.45"), "los kilos no volvieron completos"
    assert despues["borona"] == CERO
    regla_de_oro(resumen(client, h), "tras borrar el ajuste")


def test_borrar_un_ajuste_de_merma_siempre_se_puede(client, h):
    """La merma no le entra a NADIE: borrarla solo devuelve kilos, nunca los quita.

    Y por eso no se le pregunta a la bodega: no hay ningún inventario que pueda
    quedar en negativo.
    """
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317")
    assert c.status_code == 201, c.text
    a = ajustar(client, h, fecha="2026-02-10", kilos="11.11", destino="merma")
    assert a.status_code == 201, a.text
    assert existencias(client, h)["queso"] == D("126.34")
    # Se vende casi todo lo que queda: aun así la merma se puede deshacer, porque
    # devolverla SUBE el inventario del queso.
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="126.34", precio_kilo="21533")
    assert v.status_code == 201, v.text
    assert existencias(client, h)["queso"] == CERO

    r = client.delete(f"{API}/conversiones/{a.json()['id']}", headers=h)
    print(f"\n   DELETE del ajuste de merma con el queso vendido -> {r.status_code}")
    assert r.status_code == 204, r.text
    assert existencias(client, h)["queso"] == D("11.11")


# ============================================================================
# 5. DESPUÉS DEL RECHAZO, EL DUEÑO PUEDE SEGUIR TRABAJANDO
# ============================================================================
def test_despues_del_rechazo_se_deshace_la_venta_y_entonces_si_se_borra(client, h):
    """LA SALIDA QUE EL MENSAJE PROMETE, ejecutada paso por paso.

    Rebota el borrado, se anula la venta que se llevó el queso, la bodega vuelve a
    137,45 kg y ahí sí se borra la compra. Un rechazo con salida es una regla; un
    rechazo sin salida es una pared.
    """
    compra, venta = historia_corta(client, h)
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    assert r.status_code == 422
    print(f"\n   1) borrar la compra -> {r.status_code} · {detalle(r)}")

    a = client.post(f"{API}/ventas/{venta['id']}/anular", headers=h)
    print(f"   2) anular la venta de 120,23 kg -> {a.status_code}")
    assert a.status_code == 200, a.text
    assert existencias(client, h)["queso"] == D("137.45")

    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    print(f"   3) borrar la compra otra vez -> {r.status_code}")
    assert r.status_code == 204, r.text
    assert existencias(client, h).get("queso", CERO) == CERO
    regla_de_oro(resumen(client, h), "tras deshacer la venta y borrar")


def test_despues_del_rechazo_todo_lo_demas_sigue_funcionando(client, h):
    """El rechazo no puede dejar la sesión torcida: lo de todos los días sigue.

    Y ANULAR SIGUE SIENDO ANULAR: la compra que no se ha vendido se anula sin
    problema, que es la prueba de que el guardia nuevo no se volvió un candado
    general.
    """
    compra, _ = historia_corta(client, h)
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    assert r.status_code == 422

    otra = comprar(client, h, fecha="2026-03-01", productor="Sebastián Ruiz",
                   kilos_brutos="44.23", precio_kilo="14000")
    print(f"\n   comprar después del rechazo -> {otra.status_code}")
    assert otra.status_code == 201, otra.text
    assert existencias(client, h)["queso"] == D("61.45")  # 17,22 + 44,23

    v = vender(client, h, fecha="2026-03-02", cliente="Don José Pérez",
               tipo="queso", kilos="61.45", precio_kilo="21000")
    print(f"   vender los 61,45 kg que hay -> {v.status_code} · {detalle(v)}")
    assert v.status_code == 201, "la bodega quedó tocada por un borrado que se rechazó"
    assert existencias(client, h)["queso"] == CERO

    sin_vender = comprar(client, h, fecha="2026-03-05", productor="Patricia Rojas",
                         kilos_brutos="12.07", precio_kilo="14000")
    assert sin_vender.status_code == 201
    an = client.post(f"{API}/compras/{sin_vender.json()['id']}/anular", headers=h)
    print(f"   anular una compra sin vender -> {an.status_code}")
    assert an.status_code == 200, an.text
    assert existencias(client, h)["queso"] == CERO
    regla_de_oro(resumen(client, h), "después de todo el trajín")


def test_la_compra_de_un_producto_por_unidades_se_mide_en_unidades(client, h):
    """Cada producto contra SU inventario: 137 barras compradas, 59 vendidas.

    Borrarla dejaría la mozzarella en -59 unidades. Y el mensaje tiene que hablar de
    unidades, no de kilos, que en una compra por unidades valen cero.
    """
    c = comprar(client, h, fecha="2026-02-05", productor="Sebastián Ruiz",
                tipo="mozzarella", barras="137", precio_barra="12433")
    assert c.status_code == 201, c.text
    v = vender(client, h, fecha="2026-03-04", cliente="Don José Pérez",
               tipo="mozzarella", barras="59", precio_barra="17311",
               gasto_por_barra="211")
    assert v.status_code == 201, v.text
    assert existencias(client, h)["mozzarella"] == D("78")

    r = client.delete(f"{API}/compras/{c.json()['id']}", headers=h)
    texto = detalle(r)
    print(f"\n   DELETE de 137 barras con 59 vendidas -> {r.status_code} · {texto}")
    assert r.status_code == 422
    assert "unidades" in texto, "le habla de kilos a un producto que se cuenta"
    assert "Mozzarella" in texto and "78" in texto and "137" in texto
    assert existencias(client, h)["mozzarella"] == D("78")


def test_una_quesera_no_se_bloquea_por_lo_que_pasa_en_la_otra(client, h, base_datos):
    """LA REGLA MULTIEMPRESA sobre el guardia nuevo: cada bodega es la suya.

    La quesera B vende su queso; eso no puede impedirle a la quesera A borrar una
    compra suya que nadie ha tocado.
    """
    hb = auth_headers(client, "admin.b")
    cb = comprar(client, hb, fecha="2026-02-03", productor="Patricia Rojas",
                 kilos_brutos="137.45", precio_kilo="14317")
    assert cb.status_code == 201, cb.text
    vb = vender(client, hb, fecha="2026-02-12", cliente="Don José Pérez",
                tipo="queso", kilos="120.23", precio_kilo="21533")
    assert vb.status_code == 201, vb.text

    ca = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                 kilos_brutos="44.23", precio_kilo="14317")
    assert ca.status_code == 201, ca.text
    r = client.delete(f"{API}/compras/{ca.json()['id']}", headers=h)
    print(f"\n   la A borra lo suyo mientras la B tiene queso vendido -> {r.status_code}")
    assert r.status_code == 204, r.text
    assert existencias(client, h).get("queso", CERO) == CERO
    # Y a la B no le pasó nada: sus 17,22 kg siguen ahí.
    assert existencias(client, hb)["queso"] == D("17.22")

    # Y al revés: la B no puede borrar la suya, que sí está vendida.
    rb = client.delete(f"{API}/compras/{cb.json()['id']}", headers=hb)
    print(f"   la B borra la suya, que sí está vendida -> {rb.status_code}")
    assert rb.status_code == 422


# ============================================================================
# 6. EL RECHAZO NO PUEDE MANDARLO A BUSCAR UNA VENTA QUE NO EXISTE
# ============================================================================
#
# EL DISPONIBLE BAJA POR DOS CAMINOS —las ventas y los ajustes— y el guardia solo
# sabe que no alcanza. Afirmar "ya se vendió" cuando lo que se llevó los kilos fue un
# ajuste es peor que no decir nada: el dueño revisa su lista de ventas, no encuentra
# ninguna, y se queda con la compra mal anotada sin saber qué deshacer. Medido: 137,45
# kg de queso con 44,23 pasados a borona y CERO vendidos, y el rechazo del borrado le
# hablaba de "las ventas que se lo llevaron".
def historia_con_un_ajuste(client, h):
    """137,45 kg comprados, 44,23 pasados a borona, NADA vendido.

    Quedan 93,22 kg de queso y 44,23 de borona.
    """
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317")
    assert c.status_code == 201, c.text
    a = ajustar(client, h, fecha="2026-02-10", kilos="44.23", destino="borona",
                precio_kilo="1833.33")
    assert a.status_code == 201, a.text
    assert existencias(client, h) == {"queso": D("93.22"), "borona": D("44.23"),
                                      "mozzarella": CERO}
    return c.json(), a.json()


def test_borrar_una_compra_cuyos_kilos_se_pasaron_a_borona_no_habla_de_ventas(client, h):
    """Rebota igual —93,22 no alcanzan para los 137,45 que se lleva— pero nombra
    bien lo que pasó y ofrece las dos salidas."""
    compra, _ = historia_con_un_ajuste(client, h)
    retrato = foto(client, h)

    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    texto = detalle(r)
    print(f"\n   DELETE de la compra con 44,23 kg pasados a borona y 0 vendidos -> "
          f"{r.status_code} · {texto}")
    assert r.status_code == 422, "la bodega habría quedado en -44,23 kg de queso"
    assert "ya se vendió" not in texto, "no se vendió nada: lo dice y es falso"
    assert "ya salió de la bodega" in texto
    assert "93.22" in texto and "137.45" in texto
    assert "sin usar" in texto, "sigue llamando 'sin vender' a lo que no se vendió"
    assert "los ajustes de Queso" in texto, "no le dice que puede deshacer el ajuste"
    assert "corrija la compra en vez de borrarla" in texto
    exigir_quieto(retrato, foto(client, h), "el borrado rechazado por un ajuste")


def test_anular_esa_misma_compra_dice_lo_mismo(client, h):
    """Las dos puertas tienen que contar la misma historia: es el mismo guardia."""
    compra, _ = historia_con_un_ajuste(client, h)
    r = client.post(f"{API}/compras/{compra['id']}/anular", headers=h)
    texto = detalle(r)
    print(f"\n   ANULAR la misma compra -> {r.status_code} · {texto}")
    assert r.status_code == 422
    assert "ya salió de la bodega" in texto and "ya se vendió" not in texto
    assert "los ajustes de Queso" in texto
    assert "en vez de anularla" in texto


def test_la_salida_que_le_ofrece_el_mensaje_funciona_de_verdad(client, h):
    """UN RECHAZO VALE POR SU SALIDA: se deshace el ajuste y entonces sí se borra.

    Es lo mismo que la prueba de la venta, con el otro camino: el dueño hace lo que
    el mensaje le dijo y la operación pasa.
    """
    compra, ajuste = historia_con_un_ajuste(client, h)
    assert client.delete(f"{API}/compras/{compra['id']}", headers=h).status_code == 422

    d = client.delete(f"{API}/conversiones/{ajuste['id']}", headers=h)
    print(f"\n   se deshace el ajuste -> {d.status_code}")
    assert d.status_code == 204, d.text
    assert existencias(client, h)["queso"] == D("137.45")

    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    print(f"   y ahora sí se borra la compra -> {r.status_code}")
    assert r.status_code == 204, r.text
    assert existencias(client, h).get("queso", CERO) == CERO
    regla_de_oro(resumen(client, h), "tras deshacer el ajuste y borrar la compra")


def test_borrar_un_ajuste_cuyos_kilos_se_fueron_en_otro_ajuste(client, h):
    """LA MISMA TRAMPA UN PISO MÁS ABAJO: 44,23 kg a borona y 40,00 de borona a merma.

    Quedan 4,23 kg de borona. Borrar el primer ajuste se los quitaría todos y dejaría
    la borona en -40,00, y la borona no se vendió: se mermó.
    """
    _, ajuste = historia_con_un_ajuste(client, h)
    merma = ajustar(client, h, fecha="2026-02-11", kilos="40.00", destino="merma",
                    producto_origen="borona")
    assert merma.status_code == 201, merma.text
    assert existencias(client, h)["borona"] == D("4.23")
    retrato = foto(client, h)

    r = client.delete(f"{API}/conversiones/{ajuste['id']}", headers=h)
    texto = detalle(r)
    print(f"\n   DELETE del ajuste cuya borona se mermó -> {r.status_code} · {texto}")
    assert r.status_code == 422
    assert "ya se vendió" not in texto, "la borona no se vendió: se mermó"
    assert "ya salió de la bodega" in texto
    assert "4.23" in texto and "44.23" in texto
    assert "los ajustes de Borona" in texto
    assert "después sí borre el ajuste" in texto
    exigir_quieto(retrato, foto(client, h), "el borrado rechazado del ajuste encadenado")


def test_el_rechazo_de_editar_dice_hasta_donde_si_se_puede(client, h):
    """LA CUARTA PUERTA TAMBIÉN TIENE SALIDA, y la suya es una cifra.

    Editar no hay que deshacerlo: basta con no bajar la compra por debajo de lo que
    ya salió. El mensaje no decía NADA de qué hacer; ahora dice el piso exacto, y ese
    piso tiene que pasar de verdad.
    """
    compra, _ = historia_corta(client, h)  # 137,45 comprados, 120,23 vendidos
    r = client.put(f"{API}/compras/{compra['id']}",
                   json={"kilos_brutos": "50.00"}, headers=h)
    texto = detalle(r)
    print(f"\n   PUT bajando 137,45 a 50 con 120,23 vendidos -> {r.status_code} · {texto}")
    assert r.status_code == 422
    assert "17.22" in texto, "no dice cuánto queda"
    assert "87.45" in texto, "no dice cuánto le está quitando"
    assert "al menos 120.23 kg" in texto, "no dice hasta dónde sí se puede"
    assert existencias(client, h)["queso"] == D("17.22")

    ok = client.put(f"{API}/compras/{compra['id']}",
                    json={"kilos_brutos": "120.23"}, headers=h)
    print(f"   PUT al piso exacto que le dijo el mensaje -> {ok.status_code} · {detalle(ok)}")
    assert ok.status_code == 200, "el mensaje ofreció una salida que no funciona"
    assert existencias(client, h)["queso"] == CERO
    regla_de_oro(resumen(client, h), "tras bajar la compra hasta lo ya vendido")
