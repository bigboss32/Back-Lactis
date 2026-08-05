"""LA MIGRACIÓN: una factura por cada compra y por cada venta que ya existe.

Hay un cliente REAL en producción con historia cargada en reventa, y esta migración
corre sola sobre SU base en el próximo despliegue. Lo que hay que poder afirmar es que
NO MUEVE NI UN PESO, y esta prueba lo mide en vez de confiarlo: ejercita el backfill de
verdad (la función de la migración, importada del archivo) sobre datos sembrados a mano
y comprueba las cifras de control antes y después.

Además comprueba que el POST-VUELO DE VERDAD REVIENTA. Un chequeo que nunca se ha visto
fallar no es un chequeo, es un comentario: aquí se le sabotea el conteo a propósito y se
exige que la migración se niegue a seguir.

Las filas BORRADAS EN SUAVE se quedan a propósito sin factura (una fila borrada no la
lee ninguna pantalla), y eso también se fija aquí para que nadie lo "arregle" después.
"""
import importlib.util
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.empresas.models import Empresa
from app.modules.reventa.models import CompraQueso, DocumentoReventa, VentaQueso

MIGRACION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "f7c3a1d9e4b2_documentos_de_reventa.py"
)


def _cargar_migracion():
    """Carga el archivo de la migración como módulo para llamarle el backfill.

    Es el mismo camino que usa test_transportador_rutas_integridad para auditar su
    backfill: la única forma de probar código que corre UNA vez y sobre datos que
    nadie puede volver a ver.
    """
    spec = importlib.util.spec_from_file_location("mig_documentos", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


AHORA = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def base_vieja():
    """Una base con el esquema NUEVO pero con los datos como los dejó el sistema
    VIEJO: compras y ventas sin `documento_id` y sin ninguna factura.

    No se recrea el esquema viejo columna por columna a propósito: lo que hay que
    probar es el BACKFILL (las cuatro sentencias que reparten las cabeceras y los
    chequeos que las verifican), no el `ALTER TABLE`, que es alembic estándar y solo
    corre en Postgres.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    empresa_a, empresa_b = uuid.uuid4(), uuid.uuid4()
    autor = uuid.uuid4()
    with engine.begin() as conn:
        for eid, nombre, nit in ((empresa_a, "Quesera A", "900A"), (empresa_b, "Quesera B", "900B")):
            conn.execute(insert(Empresa.__table__).values(
                id=eid, nombre=nombre, nit=nit, created_at=AHORA, updated_at=AHORA,
            ))
        # ---- COMPRAS: cifras feas, una anulada, una de barras y una borrada en suave
        compras = [
            dict(kilos_brutos=Decimal("123.45"), kilos_netos=Decimal("123.45"),
                 borona_kilos=Decimal("46.7"), precio_kilo=Decimal("9877"),
                 valor_total=Decimal("1219315.65"), abonado=Decimal("500000.00"),
                 productor="Yeferson", estado="parcial", tipo="queso",
                 observaciones="la del sabado"),
            dict(kilos_brutos=Decimal("77.77"), kilos_netos=Decimal("77.77"),
                 borona_kilos=Decimal("0"), precio_kilo=Decimal("10333"),
                 valor_total=Decimal("803597.41"), abonado=Decimal("0"),
                 productor="Marlion", estado="pendiente", tipo="queso",
                 observaciones=None),
            dict(kilos_brutos=Decimal("0"), kilos_netos=Decimal("0"),
                 borona_kilos=Decimal("0"), precio_kilo=Decimal("0"),
                 barras=Decimal("30"), precio_barra=Decimal("12345"),
                 valor_total=Decimal("370350.00"), abonado=Decimal("370350.00"),
                 productor="Yubigildo", estado="pagada", tipo="mozzarella",
                 observaciones=None),
            dict(kilos_brutos=Decimal("10"), kilos_netos=Decimal("10"),
                 borona_kilos=Decimal("0"), precio_kilo=Decimal("9000"),
                 valor_total=Decimal("90000.00"), abonado=Decimal("0"),
                 productor="Anulada", estado="anulada", tipo="queso",
                 observaciones=None),
        ]
        for i, compra in enumerate(compras):
            conn.execute(insert(CompraQueso.__table__).values(
                id=uuid.uuid4(), empresa_id=empresa_a if i < 3 else empresa_b,
                fecha=date(2026, 7, 1 + i), created_at=AHORA, updated_at=AHORA,
                created_by=autor, updated_by=autor, **compra,
            ))
        # Una compra BORRADA EN SUAVE: no puede recibir factura.
        conn.execute(insert(CompraQueso.__table__).values(
            id=uuid.uuid4(), empresa_id=empresa_a, fecha=date(2026, 6, 30),
            productor="Borrada", tipo="queso", kilos_brutos=Decimal("5"),
            kilos_netos=Decimal("5"), borona_kilos=Decimal("0"),
            precio_kilo=Decimal("1000"), valor_total=Decimal("5000.00"),
            abonado=Decimal("0"), estado="inactivo", created_at=AHORA, updated_at=AHORA,
            deleted_at=AHORA,
        ))
        # ---- VENTAS
        ventas = [
            dict(kilos=Decimal("99.11"), precio_kilo=Decimal("15777"),
                 valor_total=Decimal("1563658.47"), abonado=Decimal("1563658.47"),
                 gasto_concepto="flete", gasto_por_kilo=Decimal("317"),
                 gasto_monto=Decimal("31417.87"), cliente="Tienda La 33",
                 estado="pagada", tipo="queso"),
            dict(kilos=Decimal("12.35"), precio_kilo=Decimal("4333"),
                 valor_total=Decimal("53512.55"), abonado=Decimal("36341.53"),
                 cliente="Doña Rosa", estado="parcial", tipo="borona"),
            dict(kilos=Decimal("0"), precio_kilo=Decimal("0"), barras=Decimal("7"),
                 precio_barra=Decimal("21999"), valor_total=Decimal("153993.00"),
                 abonado=Decimal("0"), cliente="Tienda La 33", estado="pendiente",
                 tipo="mozzarella"),
        ]
        for i, venta in enumerate(ventas):
            conn.execute(insert(VentaQueso.__table__).values(
                id=uuid.uuid4(), empresa_id=empresa_a if i < 2 else empresa_b,
                fecha=date(2026, 7, 10 + i), created_at=AHORA, updated_at=AHORA,
                created_by=autor, updated_by=autor, **venta,
            ))
        # Y el estado como lo dejó el sistema viejo: sin ninguna cabecera.
        conn.execute(text("UPDATE compras_queso SET documento_id = NULL"))
        conn.execute(text("UPDATE ventas_queso SET documento_id = NULL"))
        conn.execute(text("DELETE FROM documentos_reventa"))
    try:
        yield engine, empresa_a, empresa_b, autor
    finally:
        Base.metadata.drop_all(bind=engine)


def _cifras(conn, tabla):
    fila = conn.execute(text(
        f"SELECT COUNT(*), COALESCE(SUM(valor_total), 0), COALESCE(SUM(abonado), 0) "
        f"FROM {tabla} WHERE deleted_at IS NULL"
    )).one()
    return int(fila[0]), Decimal(str(fila[1])), Decimal(str(fila[2]))


def test_la_migracion_no_mueve_una_cifra_y_le_pone_factura_a_todo(base_vieja):
    """LAS CIFRAS DE CONTROL: antes y después, idénticas al peso.

    Sembradas a mano (ver la fixture):
      COMPRAS vivas: 4 filas · facturado $2.483.263,06 · abonado $870.350,00
      VENTAS  vivas: 3 filas · facturado $1.771.164,02 · abonado $1.600.000,00
    """
    engine, empresa_a, empresa_b, autor = base_vieja
    migracion = _cargar_migracion()

    with engine.begin() as conn:
        antes_compras = _cifras(conn, "compras_queso")
        antes_ventas = _cifras(conn, "ventas_queso")
        print("\n===== PRE-VUELO =====")
        print(f"  compras: {antes_compras[0]} filas · facturado {antes_compras[1]} "
              f"· abonado {antes_compras[2]}")
        print(f"  ventas:  {antes_ventas[0]} filas · facturado {antes_ventas[1]} "
              f"· abonado {antes_ventas[2]}")
        assert antes_compras == (4, Decimal("2483263.06"), Decimal("870350.00"))
        assert antes_ventas == (3, Decimal("1771164.02"), Decimal("1600000.00"))

        creados = migracion.backfill_documentos(conn)
        print(f"  cabeceras creadas: {creados}")
        assert creados == {"compra": 4, "venta": 3}

        despues_compras = _cifras(conn, "compras_queso")
        despues_ventas = _cifras(conn, "ventas_queso")
        print("===== POST-VUELO =====")
        print(f"  compras: {despues_compras[0]} filas · facturado {despues_compras[1]} "
              f"· abonado {despues_compras[2]}")
        print(f"  ventas:  {despues_ventas[0]} filas · facturado {despues_ventas[1]} "
              f"· abonado {despues_ventas[2]}")
        assert despues_compras == antes_compras, "la migración movió la plata de las compras"
        assert despues_ventas == antes_ventas, "la migración movió la plata de las ventas"

        # Toda fila viva tiene su factura, y ninguna fila borrada la tiene.
        sin_factura = conn.execute(text(
            "SELECT COUNT(*) FROM compras_queso WHERE deleted_at IS NULL "
            "AND documento_id IS NULL"
        )).scalar()
        borradas_con_factura = conn.execute(text(
            "SELECT COUNT(*) FROM compras_queso WHERE deleted_at IS NOT NULL "
            "AND documento_id IS NOT NULL"
        )).scalar()
        print(f"  filas vivas sin factura: {sin_factura} · "
              f"borradas con factura: {borradas_con_factura}")
        assert sin_factura == 0
        assert borradas_con_factura == 0

        # Y cada cabecera copió la fecha, el nombre, la nota y la autoría de su fila.
        filas = conn.execute(
            select(
                CompraQueso.fecha, CompraQueso.productor, CompraQueso.observaciones,
                CompraQueso.empresa_id, CompraQueso.created_by, CompraQueso.orden,
                DocumentoReventa.fecha, DocumentoReventa.tercero,
                DocumentoReventa.observaciones, DocumentoReventa.empresa_id,
                DocumentoReventa.created_by, DocumentoReventa.tipo,
                DocumentoReventa.estado,
            ).join(DocumentoReventa, CompraQueso.documento_id == DocumentoReventa.id)
        ).all()
        print("===== CADA CABECERA CONTRA SU FILA =====")
        assert len(filas) == 4
        for f in filas:
            print(f"  {f[1]:10} {f[0]} -> cabecera {f[7]:10} {f[6]} tipo={f[11]}")
            assert f[0] == f[6], "la fecha de la cabecera no es la de la fila"
            assert f[1] == f[7], "el tercero de la cabecera no es el productor"
            assert f[2] == f[8], "la nota no se copió"
            assert f[3] == f[9], "¡la cabecera quedó en otra empresa!"
            assert f[4] == f[10], "la autoría no se copió"
            assert f[11] == "compra"
            assert f[12] == "activo"
            assert f[5] == 0, "cada fila vieja queda sola en su factura: renglón cero"

        tipos = conn.execute(text(
            "SELECT tipo, COUNT(*) FROM documentos_reventa GROUP BY tipo ORDER BY tipo"
        )).all()
        print(f"  cabeceras por tipo: {tipos}")
        assert dict(tipos) == {"compra": 4, "venta": 3}


def test_la_venta_migrada_queda_con_su_cliente_en_la_cabecera(base_vieja):
    engine, *_ = base_vieja
    migracion = _cargar_migracion()
    with engine.begin() as conn:
        migracion.backfill_documentos(conn)
        filas = conn.execute(
            select(VentaQueso.cliente, VentaQueso.fecha, DocumentoReventa.tercero,
                   DocumentoReventa.fecha, DocumentoReventa.tipo)
            .join(DocumentoReventa, VentaQueso.documento_id == DocumentoReventa.id)
        ).all()
        print("\n===== LAS VENTAS MIGRADAS =====")
        assert len(filas) == 3
        for f in filas:
            print(f"  {f[0]:12} {f[1]} -> cabecera {f[2]:12} {f[3]} tipo={f[4]}")
            assert f[0] == f[2]
            assert f[1] == f[3]
            assert f[4] == "venta"


def test_el_post_vuelo_revienta_si_las_cabeceras_no_cuadran(base_vieja):
    """UN CHEQUEO QUE NUNCA SE HA VISTO FALLAR NO ES UN CHEQUEO.

    Se le mete una cabecera de sobra ANTES de correr el backfill: al final habría 5
    facturas de compra para 4 filas, y eso significaría facturas vacías en la
    pantalla del dueño. La migración tiene que negarse a seguir, con un mensaje que
    se entienda, y alembic deshace toda la transacción.
    """
    engine, empresa_a, _, autor = base_vieja
    migracion = _cargar_migracion()
    with engine.begin() as conn:
        conn.execute(insert(DocumentoReventa.__table__).values(
            id=uuid.uuid4(), empresa_id=empresa_a, tipo="compra",
            fecha=date(2026, 7, 1), tercero="Intrusa", estado="activo",
            created_at=AHORA, updated_at=AHORA,
        ))
        with pytest.raises(RuntimeError) as excinfo:
            migracion.backfill_documentos(conn)
    mensaje = str(excinfo.value)
    print("\n===== EL POST-VUELO REVENTÓ, COMO TENÍA QUE SER =====")
    print("  " + mensaje.replace("\n", "\n  "))
    assert "MIGRACIÓN ABORTADA" in mensaje
    assert "5 facturas de compra" in mensaje
    assert "4 filas" in mensaje
    assert "alembic deshace toda la transacción" in mensaje


class ConexionSaboteada:
    """Una conexión que le rompe A PROPÓSITO el UPDATE que reparte los documento_id.

    Le cambia el `WHERE deleted_at IS NULL` por un `WHERE 1 = 0`, o sea que el UPDATE
    no toca ni una fila. Todo lo demás pasa derecho. Es la forma de comprobar que el
    post-vuelo se da cuenta.
    """

    def __init__(self, real):
        self._real = real

    def execute(self, sentencia, *args, **kwargs):
        sql = str(sentencia)
        if "SET documento_id" in sql:
            sentencia = text(sql.replace("deleted_at IS NULL", "1 = 0"))
        return self._real.execute(sentencia, *args, **kwargs)


def test_el_post_vuelo_revienta_si_alguna_fila_viva_queda_sin_factura(base_vieja):
    """El otro chequeo: toda fila viva tiene que quedar con su cabecera.

    Sin este chequeo, una fila sin factura no daría ningún error: simplemente no
    saldría en la pantalla de facturas, y el dueño no vería una compra que sí
    registró. Un dato que se esconde en silencio es lo peor que puede pasar aquí.
    """
    engine, *_ = base_vieja
    migracion = _cargar_migracion()
    with engine.begin() as conn:
        with pytest.raises(RuntimeError) as excinfo:
            migracion.backfill_documentos(ConexionSaboteada(conn))
    mensaje = str(excinfo.value)
    print("\n===== SIN REPARTIR LOS documento_id =====")
    print("  " + mensaje.replace("\n", "\n  "))
    assert "MIGRACIÓN ABORTADA" in mensaje
    assert "sin factura" in mensaje
