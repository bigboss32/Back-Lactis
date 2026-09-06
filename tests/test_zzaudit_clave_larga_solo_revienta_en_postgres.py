"""AUDITORIA (no arregla nada): una clave de producto que NO cabe en su columna.

Es la MISMA familia del defecto que ya cerro la migracion b1c2d3e4f5a6 ("la clave del
producto tiene que CABER en la columna donde se guarda"), pero por el otro extremo:

  · `productos_reventa.clave` es varchar(80) en los dos motores;
  · `clave_de_producto` recorta el nombre a 80 caracteres -- exactamente el ancho de la
    columna -- y DESPUES `clave_sin_chocar_con_el_desglose` le puede PEGAR el sufijo
    `_producto` (9 caracteres) si la clave choca con una fila calculada del desglose;
  · el esquema `ProductoReventaCreate` admite nombres de hasta 80 caracteres.

Total: 80 + 9 = 89 caracteres para una columna de 80. En SQLite (donde corren las
pruebas) el ancho no se valida y el producto se guarda; en Postgres (la base del cliente)
el INSERT se cae con 22001 'value too long for type character varying(80)'.

Estas dos pruebas miden las dos mitades. El lado Postgres esta en
tests/test_zzaudit_migraciones_postgres.py.
"""
import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa/productos"

# Un nombre de 80 caracteres EXACTOS que termina en una de las palabras que el desglose
# se reserva ("merma"): es lo que dispara el sufijo.
_BASE = "Queso costeno artesanal del Guaviare curado en hoja de platano y merma"
NOMBRE_DE_80 = _BASE + "x" * (80 - len(_BASE) - len(" merma")) + " merma"


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_el_esquema_admite_el_nombre_y_la_clave_sale_mas_larga_que_la_columna():
    from app.modules.reventa.schemas import ProductoReventaCreate
    from app.modules.reventa.service import (
        clave_de_producto,
        clave_sin_chocar_con_el_desglose,
    )

    assert len(NOMBRE_DE_80) == 80
    # el esquema lo acepta: max_length=80
    ProductoReventaCreate(nombre=NOMBRE_DE_80)

    recortada = clave_de_producto(NOMBRE_DE_80)
    final = clave_sin_chocar_con_el_desglose(recortada)
    print("\nNOMBRE  (%2d): %s" % (len(NOMBRE_DE_80), NOMBRE_DE_80))
    print("CLAVE recortada (%2d): %s" % (len(recortada), recortada))
    print("CLAVE final     (%2d): %s" % (len(final), final))
    assert len(recortada) == 80
    assert len(final) <= 80, (
        "la clave que el servicio va a guardar tiene %d caracteres y la columna "
        "`productos_reventa.clave` es varchar(80): en Postgres esto es un 500"
        % len(final)
    )


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_la_api_crea_el_producto_con_una_clave_de_89_caracteres(
    client, base_datos
):
    """De punta a punta contra la API real (sobre SQLite, que es donde corre la suite)."""
    h = auth_headers(client, "admin.a")
    r = client.post(API, json={"nombre": NOMBRE_DE_80, "unidad": "kg"}, headers=h)
    print("\nPOST %s -> %s" % (API, r.status_code))
    assert r.status_code in (200, 201), r.text
    clave = r.json()["clave"]
    print("la API guardo la clave (%d caracteres): %s" % (len(clave), clave))
    assert len(clave) <= 80, (
        "la API acepto el producto y le guardo una clave de %d caracteres en una "
        "columna varchar(80). En SQLite pasa; en la base del cliente (Postgres) el "
        "mismo POST responde 500 con 22001 'value too long for type character "
        "varying(80)'." % len(clave)
    )
