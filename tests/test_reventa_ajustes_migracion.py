"""LA MIGRACIÓN QUE LE PONE NOMBRE A LOS PRODUCTOS DE CADA AJUSTE Y DE CADA COMPRA.

Hay un cliente REAL en producción con historia cargada en reventa, y esta migración
(`c5d9e3a7b1f4`) corre sola sobre SU base en el próximo despliegue. Lo que hay que
poder afirmar es que NO MUEVE NI UN PESO NI UN KILO, y esta prueba lo mide en vez de
confiarlo: ejercita el relleno de verdad —la función importada del archivo de la
migración— sobre datos sembrados a mano, y comprueba las cifras de control antes y
después.

Y COMPRUEBA QUE LOS DOS POST-VUELOS DE VERDAD REVIENTAN. Un chequeo que nunca se ha
visto fallar no es un chequeo, es un comentario: aquí se les sabotea la cuenta a
propósito y se exige que la migración se niegue a seguir.

Son dos chequeos y hacen falta los dos:
  · que no se haya movido nada (ninguna cantidad, ningún peso);
  · que el relleno haya quedado COMPLETO, porque una fila que se quede sin decir de
    qué producto habla es justamente la que el sistema tendría que volver a adivinar,
    que es el defecto que esta migración viene a cerrar.
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
from app.modules.reventa.models import CompraQueso, ConversionBorona

MIGRACION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "c5d9e3a7b1f4_el_ajuste_dice_de_que_producto_a_cual.py"
)
AHORA = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)


def _cargar_migracion():
    """Carga el archivo de la migración como módulo para llamarle el relleno.

    Es el mismo camino que usan las otras pruebas de migración de este proyecto: la
    única forma de probar código que corre UNA vez y sobre datos que nadie va a poder
    volver a ver como estaban.
    """
    spec = importlib.util.spec_from_file_location("mig_ajustes", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture()
def base_vieja():
    """Una base con el esquema NUEVO pero con los datos como los dejó el sistema
    VIEJO: ajustes sin producto de origen ni de destino, y compras con kilos gratis
    sin decir a quién le entraron.

    No se recrea el esquema viejo columna por columna a propósito: lo que hay que
    probar es el RELLENO y sus chequeos, no el `ALTER TABLE`, que es alembic estándar.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    empresa_a, empresa_b = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        for eid, nombre, nit in (
            (empresa_a, "Quesera A", "900A"), (empresa_b, "Quesera B", "900B")
        ):
            conn.execute(insert(Empresa.__table__).values(
                id=eid, nombre=nombre, nit=nit, created_at=AHORA, updated_at=AHORA,
            ))
        # ---- COMPRAS: con y sin kilos gratis, una anulada, una de barras y una
        #      borrada en suave. Cifras feas a propósito.
        compras = [
            dict(empresa_id=empresa_a, fecha=date(2026, 2, 3), productor="Yeferson",
                 kilos_brutos=Decimal("820.53"), kilos_netos=Decimal("820.53"),
                 borona_kilos=Decimal("18.27"), precio_kilo=Decimal("14317"),
                 valor_total=Decimal("11751527.01"), abonado=Decimal("5000000.33"),
                 estado="parcial", tipo="queso"),
            dict(empresa_id=empresa_a, fecha=date(2026, 2, 17), productor="Marlion",
                 kilos_brutos=Decimal("633.87"), kilos_netos=Decimal("633.87"),
                 borona_kilos=Decimal("0"), precio_kilo=Decimal("13871"),
                 valor_total=Decimal("8792312.77"), abonado=Decimal("0"),
                 estado="pendiente", tipo="queso"),
            dict(empresa_id=empresa_a, fecha=date(2026, 2, 19), productor="Yubigildo",
                 kilos_brutos=Decimal("0"), kilos_netos=Decimal("0"),
                 borona_kilos=Decimal("0"), precio_kilo=Decimal("0"),
                 barras=Decimal("137"), precio_barra=Decimal("12433"),
                 valor_total=Decimal("1703321.00"), abonado=Decimal("1703321.00"),
                 estado="pagada", tipo="mozzarella"),
            dict(empresa_id=empresa_a, fecha=date(2026, 2, 21), productor="Anulada",
                 kilos_brutos=Decimal("99.99"), kilos_netos=Decimal("99.99"),
                 borona_kilos=Decimal("3.33"), precio_kilo=Decimal("11111"),
                 valor_total=Decimal("1111000.89"), abonado=Decimal("0"),
                 estado="anulada", tipo="queso"),
            # LA QUESERA DE AL LADO: su historia se rellena igual, con SUS claves,
            # que son las mismas porque la siembra es la misma en toda empresa.
            dict(empresa_id=empresa_b, fecha=date(2026, 2, 5), productor="Otra",
                 kilos_brutos=Decimal("410.11"), kilos_netos=Decimal("410.11"),
                 borona_kilos=Decimal("7.09"), precio_kilo=Decimal("15033"),
                 valor_total=Decimal("6165183.63"), abonado=Decimal("0"),
                 estado="pendiente", tipo="queso"),
        ]
        for fila in compras:
            conn.execute(insert(CompraQueso.__table__).values(
                id=uuid.uuid4(), created_at=AHORA, updated_at=AHORA, **fila
            ))
        # Una BORRADA EN SUAVE con kilos gratis: también tiene que quedar rellenada,
        # porque si algún día se restaura, sus kilos tienen que saber a quién entraron.
        conn.execute(insert(CompraQueso.__table__).values(
            id=uuid.uuid4(), created_at=AHORA, updated_at=AHORA, deleted_at=AHORA,
            empresa_id=empresa_a, fecha=date(2026, 2, 7), productor="Borrada",
            kilos_brutos=Decimal("50.50"), kilos_netos=Decimal("50.50"),
            borona_kilos=Decimal("5.05"), precio_kilo=Decimal("10000"),
            valor_total=Decimal("505000.00"), abonado=Decimal("0"),
            estado="pendiente", tipo="queso",
        ))
        # ---- AJUSTES: los dos destinos, en las dos empresas, y uno borrado en suave.
        ajustes = [
            dict(empresa_id=empresa_a, fecha=date(2026, 2, 10),
                 kilos=Decimal("30.30"), destino="borona",
                 precio_kilo=Decimal("3100")),
            dict(empresa_id=empresa_a, fecha=date(2026, 2, 21),
                 kilos=Decimal("11.11"), destino="merma", precio_kilo=Decimal("0")),
            dict(empresa_id=empresa_a, fecha=date(2026, 3, 2),
                 kilos=Decimal("7.77"), destino="borona",
                 precio_kilo=Decimal("3250")),
            dict(empresa_id=empresa_b, fecha=date(2026, 3, 4),
                 kilos=Decimal("2.02"), destino="merma", precio_kilo=Decimal("0")),
        ]
        for fila in ajustes:
            conn.execute(insert(ConversionBorona.__table__).values(
                id=uuid.uuid4(), created_at=AHORA, updated_at=AHORA, estado="activo",
                **fila
            ))
        conn.execute(insert(ConversionBorona.__table__).values(
            id=uuid.uuid4(), created_at=AHORA, updated_at=AHORA, deleted_at=AHORA,
            estado="activo", empresa_id=empresa_a, fecha=date(2026, 3, 5),
            kilos=Decimal("1.01"), destino="borona", precio_kilo=Decimal("3000"),
        ))
        # EL PUNTO DE PARTIDA ES EL DE JUSTO DESPUÉS DEL `ADD COLUMN`, que es donde el
        # relleno de verdad empieza: `producto_origen` es NOT NULL con server_default
        # 'queso', así que alembic la deja llena en todas las filas; las otras dos
        # nacen en nulo. Ponerlas así a mano es lo que hace que esta prueba mida el
        # relleno y no el `ALTER TABLE`, que es alembic estándar.
        conn.execute(text(
            "UPDATE conversiones_borona SET producto_origen = 'queso', "
            "producto_destino = NULL"
        ))
        conn.execute(text("UPDATE compras_queso SET subproducto_tipo = NULL"))
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_el_relleno_no_mueve_ni_una_cifra_y_queda_completo(base_vieja):
    """LO QUE IMPORTA: las cifras de control antes y después son las mismas, y el
    relleno quedó completo. Se imprimen las dos tablas para que quede el rastro."""
    mig = _cargar_migracion()
    with base_vieja.begin() as conn:
        antes = mig._cifras_de_control(conn)
        print("\n===== cifras de control ANTES =====")
        for nombre, valor in antes.items():
            print(f"   {nombre:44} = {valor}")

        mig.rellenar(conn)

        despues = mig._cifras_de_control(conn)
        print("\n===== cifras de control DESPUÉS =====")
        for nombre, valor in despues.items():
            print(f"   {nombre:44} = {valor}")
        relleno = mig._cifras_del_relleno(conn)
        print("\n===== lo que quedó nombrado =====")
        for nombre, valor in relleno.items():
            print(f"   {nombre:44} = {valor}")

        # Los dos chequeos de la migración, corridos de verdad.
        mig._exigir_que_no_se_movio(antes, despues)
        mig._exigir_que_el_relleno_quedo_completo(antes, relleno)

        # Y las cifras concretas, escritas a mano, para que se vea qué se midió.
        assert antes["ajustes: cantidad de filas"] == "5"
        assert antes["ajustes: suma de kilos"] == "52.21"
        assert antes["ajustes: cuántos van a borona"] == "3"
        assert antes["ajustes: kilos que van a borona"] == "39.08"
        assert antes["ajustes: cuántos son merma"] == "2"
        assert antes["ajustes: kilos de merma"] == "13.13"
        assert antes["compras: cantidad de filas"] == "6"
        assert antes["compras: suma de borona_kilos"] == "33.74"
        assert antes["compras: cuántas trajeron kilos gratis"] == "4"
        assert antes["compras: suma de valor_total (en pesos)"] == "30028345.30"
        assert despues == antes, "el relleno movió una cifra"
        assert relleno["ajustes con origen 'queso'"] == "5"
        assert relleno["ajustes con destino 'borona'"] == "3"
        assert relleno["ajustes sin destino (merma)"] == "2"
        assert relleno["compras marcadas con destinatario 'borona'"] == "4"
        assert relleno["kilos gratis con destinatario"] == "33.74"


def test_el_relleno_dice_lo_que_esas_filas_siempre_significaron(base_vieja):
    """Fila por fila: de dónde salen los kilos y a quién le entran.

    La merma queda SIN destino, y ese nulo significa algo preciso: esos kilos no le
    entraron a nadie, se perdieron. Y las compras que NO trajeron nada gratis se
    quedan sin destinatario, que también es la verdad.
    """
    mig = _cargar_migracion()
    with base_vieja.begin() as conn:
        mig.rellenar(conn)
        filas = conn.execute(
            select(
                ConversionBorona.destino,
                ConversionBorona.kilos,
                ConversionBorona.producto_origen,
                ConversionBorona.producto_destino,
            ).order_by(ConversionBorona.fecha)
        ).all()
        print("\n===== los ajustes rellenados =====")
        for destino, kilos, origen, hacia in filas:
            print(f"   {destino:8} {kilos:>8}  {origen} -> {hacia}")
        assert all(f[2] == "queso" for f in filas)
        assert all(f[3] == "borona" for f in filas if f[0] == "borona")
        assert all(f[3] is None for f in filas if f[0] == "merma")

        compras = conn.execute(
            select(
                CompraQueso.productor,
                CompraQueso.borona_kilos,
                CompraQueso.subproducto_tipo,
            ).order_by(CompraQueso.productor)
        ).all()
        print("\n===== las compras rellenadas =====")
        for productor, gratis, hacia in compras:
            print(f"   {productor:12} gratis={gratis:>8}  -> {hacia}")
        for _, gratis, hacia in compras:
            assert hacia == ("borona" if gratis and gratis > 0 else None)


def test_el_post_vuelo_revienta_si_una_cifra_se_mueve(base_vieja):
    """Se le sabotea una cantidad a propósito: la migración tiene que negarse."""
    mig = _cargar_migracion()
    with base_vieja.begin() as conn:
        antes = mig._cifras_de_control(conn)
        mig.rellenar(conn)
        # El sabotaje: alguien "aprovecha" la migración para tocar unos kilos.
        conn.execute(text("UPDATE conversiones_borona SET kilos = kilos + 1"))
        with pytest.raises(RuntimeError) as caida:
            mig._exigir_que_no_se_movio(antes, mig._cifras_de_control(conn))
        mensaje = str(caida.value)
        print("\n===== el mensaje del post-vuelo =====\n" + mensaje)
        assert "MIGRACIÓN ABORTADA" in mensaje
        assert "suma de kilos" in mensaje
        assert "52.21" in mensaje and "57.21" in mensaje, (
            "el mensaje tiene que decir cuánto valía y cuánto vale"
        )
        assert "alembic deshace toda la transacción" in mensaje


def test_el_post_vuelo_revienta_si_el_relleno_queda_incompleto(base_vieja):
    """Y el otro chequeo: una fila que se quedó sin decir de qué producto habla."""
    mig = _cargar_migracion()
    with base_vieja.begin() as conn:
        antes = mig._cifras_de_control(conn)
        mig.rellenar(conn)
        # El sabotaje: dos ajustes quedan diciendo que salieron de OTRO producto, que
        # es exactamente lo que pasaría si el relleno no los hubiera alcanzado (la
        # columna es NOT NULL, así que "sin rellenar" no puede ser un nulo: es un
        # valor que no es el que esas filas siempre significaron).
        conn.execute(text(
            "UPDATE conversiones_borona SET producto_origen = 'otro_producto' "
            "WHERE destino = 'merma'"
        ))
        with pytest.raises(RuntimeError) as caida:
            mig._exigir_que_el_relleno_quedo_completo(
                antes, mig._cifras_del_relleno(conn)
            )
        mensaje = str(caida.value)
        print("\n===== el mensaje del chequeo del relleno =====\n" + mensaje)
        assert "MIGRACIÓN ABORTADA" in mensaje
        assert "ajustes con origen 'queso'" in mensaje
        assert "quedaron 3 y tenían que ser 5" in mensaje


def test_una_base_vacia_pasa_sin_ruido(base_vieja):
    """El despliegue de una instalación nueva: cero filas, cero problemas."""
    mig = _cargar_migracion()
    with base_vieja.begin() as conn:
        conn.execute(text("DELETE FROM conversiones_borona"))
        conn.execute(text("DELETE FROM compras_queso"))
        antes = mig._cifras_de_control(conn)
        mig.rellenar(conn)
        mig._exigir_que_no_se_movio(antes, mig._cifras_de_control(conn))
        mig._exigir_que_el_relleno_quedo_completo(antes, mig._cifras_del_relleno(conn))
        print("\n   base vacía:", antes)
