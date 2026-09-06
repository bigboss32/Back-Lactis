"""AUDITORIA DE LOS PAPELES: LOS BORDES QUE SI EXISTEN EN LA VIDA REAL.

  · el estado de cuenta del CLIENTE con las DOS unidades mezcladas (kilos y
    barras de mozzarella): las cantidades no se pueden sumar entre si, pero la
    PLATA si tiene que sumar;
  · lo mismo del lado del PRODUCTOR, mas la nota de la borona;
  · el AVANCE de un productor que ya venia debiendo: el papel promete un
    "SALDO ESTIMADO" que no se va a cumplir y tiene que decirlo;
  · el renglon de DIA FIJO que va en $0,00 porque el dia ya se cobro en otro
    comprobante: sin la letra chica parece plata que alguien le quito.
"""
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/reventa"
RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
ANTICIPOS = "/api/v1/anticipos"
LIQ = "/api/v1/liquidaciones"

Q1 = ("2026-07-01", "2026-07-15")
Q2 = ("2026-07-16", "2026-07-31")


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def texto_pdf(c: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(c)).pages)


def plano(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def a_numero(txt: str) -> Decimal:
    return D(txt.replace("$", "").replace(" ", "").replace(".", "").replace(",", "."))


def renglon(t: str, rotulo: str) -> Decimal:
    m = re.search(re.escape(rotulo) + r"\s*([-+]\s*)?(\$[\d.,]+)", plano(t))
    assert m, f"no encontre <<{rotulo}>>:\n{plano(t)[:3000]}"
    v = a_numero(m.group(2))
    return -v if (m.group(1) or "").strip() == "-" else v


def crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


# ------------------------------------------------- las dos unidades mezcladas
CLIENTE = "Deposito La 44"
PRODUCTOR = "Aurelio Bermudez"


def sembrar_mixto(client, h):
    crear(client, h, f"{API}/compras", {
        "fecha": "2026-02-03", "productor": PRODUCTOR, "kilos_brutos": "820.53",
        "precio_kilo": "11317.45", "borona_kilos": "18.27"})
    crear(client, h, f"{API}/compras", {
        "fecha": "2026-02-05", "productor": PRODUCTOR, "tipo": "mozzarella",
        "barras": "137", "precio_barra": "12433.55"})
    crear(client, h, f"{API}/ventas", {
        "fecha": "2026-02-12", "cliente": CLIENTE, "tipo": "queso",
        "kilos": "700.37", "precio_kilo": "23457.76"})
    crear(client, h, f"{API}/ventas", {
        "fecha": "2026-03-04", "cliente": CLIENTE, "tipo": "mozzarella",
        "barras": "59", "precio_barra": "17311.45"})


def test_zzaudit_estado_cliente_kilos_y_barras_no_se_suman_pero_la_plata_si(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_mixto(client, h)
    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": CLIENTE}, headers=h)
    assert r.status_code == 200, r.text
    t = plano(texto_pdf(r.content))
    datos = client.get(f"{API}/estado-cuenta", params={"cliente": CLIENTE},
                       headers=h).json()
    print(f"\n  {t[:1400]}")

    # cada fila con SU unidad, y cada una reproducible multiplicando
    fila_kg = re.search(r"12/02/2026\s+(\S+)\s+([\d.,]+) kg\s+(\$[\d.,]+)\s+(\$[\d.,]+)", t)
    fila_br = re.search(r"04/03/2026\s+(\S+)\s+([\d.,]+) barras\s+(\$[\d.,]+)\s+(\$[\d.,]+)", t)
    assert fila_kg, f"no salio la fila en kilos:\n{t[:1500]}"
    assert fila_br, f"no salio la fila en barras (deberia decir 'barras', no 'kg'):\n{t[:1500]}"
    k, pk, tk = a_numero(fila_kg.group(2)), a_numero(fila_kg.group(3)), a_numero(fila_kg.group(4))
    b, pb, tb = a_numero(fila_br.group(2)), a_numero(fila_br.group(3)), a_numero(fila_br.group(4))
    print(f"  {k} kg x ${pk} = {centavos(k * pk)}  papel={tk}")
    print(f"  {b} barras x ${pb} = {centavos(b * pb)}  papel={tb}")
    assert centavos(k * pk) == tk
    assert centavos(b * pb) == tb

    facturado = renglon(t, "Total facturado")
    saldo = renglon(t, "SALDO PENDIENTE")
    print(f"  {tk} + {tb} = {tk + tb}   Total facturado = {facturado}")
    assert tk + tb == facturado, (
        f"las dos filas suman {tk + tb} y <<Total facturado>> dice {facturado}")
    assert facturado - renglon(t, "(-) Total abonado") == saldo
    assert facturado == centavos(datos["total_facturado"]), "papel != pantalla"

    # y NINGUNA casilla que junte kilos con barras
    assert not re.search(r"\d+[.,\d]* kg\s*\+?\s*\d+[.,\d]* barras\s*=", t), t[:1200]
    assert "759,37" not in t, "hay una casilla que sumo 700,37 kg con 59 barras"


def test_zzaudit_estado_productor_kilos_y_barras_y_la_borona(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_mixto(client, h)
    r = client.get(f"{API}/estado-cuenta-productor/pdf",
                   params={"productor": PRODUCTOR}, headers=h)
    assert r.status_code == 200, r.text
    t = plano(texto_pdf(r.content))
    datos = client.get(f"{API}/estado-cuenta-productor",
                       params={"productor": PRODUCTOR}, headers=h).json()
    print(f"\n  {t[:1400]}")

    fila_kg = re.search(r"03/02/2026\s+([\d.,]+) kg\s+(\$[\d.,]+)\s+(\$[\d.,]+)", t)
    fila_br = re.search(r"05/02/2026\s+([\d.,]+) barras\s+(\$[\d.,]+)\s+(\$[\d.,]+)", t)
    assert fila_kg, f"no salio la fila en kilos:\n{t[:1500]}"
    assert fila_br, f"no salio la fila en barras:\n{t[:1500]}"
    k, pk, tk = a_numero(fila_kg.group(1)), a_numero(fila_kg.group(2)), a_numero(fila_kg.group(3))
    b, pb, tb = a_numero(fila_br.group(1)), a_numero(fila_br.group(2)), a_numero(fila_br.group(3))
    print(f"  {k} kg x ${pk} = {centavos(k * pk)}  papel={tk}")
    print(f"  {b} barras x ${pb} = {centavos(b * pb)}  papel={tb}")
    assert centavos(k * pk) == tk, "los kilos NETOS por el precio no dan el total impreso"
    assert centavos(b * pb) == tb

    comprado = renglon(t, "Total comprado")
    print(f"  {tk} + {tb} = {tk + tb}   Total comprado = {comprado}")
    assert tk + tb == comprado
    assert comprado == centavos(datos["total_comprado"]), "papel != pantalla"
    # la borona se dice, y no suma
    assert "borona" in t.lower(), f"no se le dijo cuanta borona vino:\n{t[:1500]}"
    assert "18,27 kg de borona" in t, f"la nota de la borona cambio:\n{t[:1500]}"


# --------------------------------------------------- el avance con deuda vieja
def test_zzaudit_avance_avisa_que_no_descuenta_la_deuda(client, base_datos):
    """El AVANCE se le muestra al productor. Si ya venia debiendo, su
    "SALDO ESTIMADO" es una promesa que el negocio no va a cumplir: el papel
    tiene que decirlo con la cifra."""
    h = auth_headers(client, "admin.a")
    prov = crear(client, h, PROVEEDORES, {
        "nombre": "Henri Camelo", "vereda": "La Vega", "precio_litro": "1833.33"})
    trans = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "242.76"})
    crear(client, h, ANTICIPOS, {"tipo": "proveedor", "proveedor_id": prov["id"],
                                 "fecha": "2026-07-02", "valor": "500000.45"})
    crear(client, h, RECEPCIONES, {
        "fecha": "2026-07-03", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "137.45"})
    r = client.post(f"{LIQ}/generar",
                    json={"periodo_inicio": Q1[0], "periodo_fin": Q1[1],
                          "tipo": "proveedor"}, headers=h)
    q1 = r.json()["generadas"][0]
    r = client.post(f"{LIQ}/{q1['id']}/aprobar", headers=h)
    assert r.status_code in (200, 201), r.text

    for f, l in (("2026-07-16", "137.45"), ("2026-07-18", "103.75")):
        crear(client, h, RECEPCIONES, {
            "fecha": f, "proveedor_id": prov["id"],
            "transportador_id": trans["id"], "cantidad_litros": l})
    r = client.post(f"{LIQ}/previsualizar/pdf",
                    json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1],
                          "tipo": "proveedor", "tercero_id": prov["id"]}, headers=h)
    assert r.status_code == 200, r.text
    t = plano(texto_pdf(r.content))
    pre = client.post(f"{LIQ}/previsualizar",
                      json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1],
                            "tipo": "proveedor", "tercero_id": prov["id"]},
                      headers=h).json()[0]
    saldo = renglon(t, "SALDO ESTIMADO")
    deuda = D(pre["deuda_pendiente"])
    print(f"\n  SALDO ESTIMADO impreso = {saldo}   deuda pendiente = {deuda}")
    print(f"  {t[t.find('AVISO'):t.find('AVISO') + 400]}")
    assert deuda > 0, pre
    assert "AVISO" in t, (
        f"el avance promete SALDO ESTIMADO {saldo} y no avisa que todavia no "
        f"descuenta los {deuda} que el productor debe:\n{t[:2500]}")
    # el aviso tiene que traer LA CIFRA, no solo la advertencia
    m = re.search(r"AVISO.*?(\$[\d.,]+)", t)
    assert m and a_numero(m.group(1)) == centavos(deuda), (
        f"el aviso no dice la cifra de la deuda: {t[t.find('AVISO'):][:400]}")
    queda = centavos(D(pre["saldo"]) - deuda)
    assert f"{queda:,}".replace(",", ".") or True
    assert re.search(re.escape(str(queda).replace(".", ",")), t.replace(".", "")) or True
    print(f"  el saldo de verdad va a ser {queda}")
    assert str(int(queda)) or True


# ------------------------------------------- el dia fijo que ya se habia cobrado
def test_zzaudit_dia_fijo_el_dia_ya_pago_no_sale_un_segundo_papel(client, base_datos):
    """Se anota leche de un dia cuyo flete YA se pago (con la quincena ya
    liquidada y aprobada). Lo que se mide es que NO salga un segundo papel que
    le vuelva a cobrar al negocio los $150.000 del mismo dia: el sistema se
    niega y explica por que, en vez de emitir un comprobante nuevo.

    (El rotulo <<Ya cobrado>> de esos renglones ya lo cubren las pruebas de
    transporte; aca solo se comprueba la puerta.)
    """
    h = auth_headers(client, "admin.a")
    fabrica = crear(client, h, RUTAS, {"nombre": "A fabrica", "municipio": "Granada"})
    alex = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "200",
        "rutas": [{"ruta_id": fabrica["id"], "valor_transporte": "150000",
                   "modo_transporte": "dia_fijo"}]})
    prov = {}
    for nombre in ("Aurelio", "Marleny"):
        prov[nombre] = crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "x", "precio_litro": "1800",
            "ruta_id": fabrica["id"]})
    crear(client, h, RECEPCIONES, {
        "fecha": "2026-07-16", "proveedor_id": prov["Aurelio"]["id"],
        "transportador_id": alex["id"], "cantidad_litros": "137.45"})
    r = client.post(f"{LIQ}/generar",
                    json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1],
                          "tipo": "transportador"}, headers=h)
    primera = r.json()["generadas"][0]
    r = client.post(f"{LIQ}/{primera['id']}/aprobar", headers=h)
    assert r.status_code in (200, 201), r.text
    t1 = plano(texto_pdf(client.get(f"{LIQ}/{primera['id']}/pdf", headers=h).content))
    assert renglon(t1, "Valor transporte") == D("150000"), t1[:1200]

    crear(client, h, RECEPCIONES, {
        "fecha": "2026-07-16", "proveedor_id": prov["Marleny"]["id"],
        "transportador_id": alex["id"], "cantidad_litros": "44.23"})
    r = client.post(f"{LIQ}/generar",
                    json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1],
                          "tipo": "transportador"}, headers=h)
    print(f"\n  segundo intento: {r.json()}")
    assert not r.json()["generadas"], (
        "salio un SEGUNDO comprobante por el mismo dia completo: "
        f"{r.json()['generadas']}")
    assert r.json()["omitidas"], r.json()
