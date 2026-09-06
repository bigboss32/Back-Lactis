"""AUDITORIA DE LOS FORMATEADORES DEL PAPEL: el medio centavo y la coma.

Es la regla escrita del proyecto: el medio SUBE (0,005 -> 0,01), no el redondeo
del banquero de Python; los miles van con PUNTO y los decimales con COMA.
Se mide directamente sobre las funciones que escriben TODAS las cifras de los
cinco PDF.
"""
from decimal import Decimal

from app.utils.export import barras, kilogramos, litros, pesos


def test_zzaudit_formato_el_medio_centavo_sube_y_no_es_el_del_banquero():
    casos = [
        ("2.505", "$2,51"),        # el del banquero daria $2,50
        ("1800.005", "$1.800,01"),  # el del banquero daria $1.800,00
        ("0.005", "$0,01"),
        ("2.515", "$2,52"),
        ("0.999", "$1"),            # se redondea PRIMERO y despues se decide si hay centavos
        ("-2.505", "-$2,51"),
    ]
    for crudo, esperado in casos:
        obtenido = pesos(Decimal(crudo))
        print(f"  pesos({crudo}) = {obtenido}   esperado {esperado}")
        assert obtenido == esperado, f"pesos({crudo}) dio {obtenido}, no {esperado}"


def test_zzaudit_formato_colombiano_en_las_cuatro_unidades():
    assert pesos(Decimal("18525000")) == "$18.525.000"
    assert pesos(Decimal("19500.50")) == "$19.500,50"
    assert kilogramos(Decimal("1234.5")) == "1.234,5 kg"
    assert kilogramos(Decimal("100")) == "100 kg"
    assert litros(Decimal("227.50")) == "227,5 L"
    assert litros(Decimal("1234.75")) == "1.234,75 L"
    assert barras(Decimal("1")) == "1 barra"
    assert barras(Decimal("8")) == "8 barras"
    print("  todas las unidades salen con punto de miles y coma decimal")


def test_zzaudit_formato_cifra_absurda_no_tumba_el_papel():
    """Una cifra imposible no puede dejar el PDF caido con un 500."""
    for crudo in ("1E+50", "-1E+50", "0"):
        salida = pesos(Decimal(crudo))
        print(f"  pesos({crudo}) = {salida[:60]}")
        assert isinstance(salida, str) and salida
