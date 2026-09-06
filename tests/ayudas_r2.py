"""El doble de Cloudflare R2 que usan TODAS las pruebas de soportes de pago.

ESTAS PRUEBAS NO SALEN A INTERNET, ni siquiera cuando la máquina tiene llaves
configuradas. Mismo patrón que `WompiFalso` en tests/test_suscripcion_pse.py: los
servicios instancian el cliente DENTRO de cada método, así que basta con
cambiarle el nombre en el módulo que lo usa.

Vive acá y no dentro de un archivo de pruebas porque lo usan dos: los adjuntos de
reventa (compras y ventas) y los soportes de los pagos de liquidación. Son el
mismo bucket, el mismo cliente y las mismas reglas; dos dobles distintos serían
dos contratos que se van separando sin que nadie se entere.
"""
from app.core.exceptions import BusinessError


class R2Falso:
    """Doble del cliente de R2: guarda los objetos en un diccionario.

    Registra TODO lo que se le pidió (qué se subió, qué se firmó y por cuántos
    segundos, qué se borró) porque varias pruebas verifican justamente eso: que
    la duración del enlace sea corta, que no se firme nada de otra empresa, y que
    borrar un soporte borre también el archivo.
    """

    objetos: dict[str, tuple[bytes, str]] = {}
    firmas: list[tuple[str, int]] = []
    borrados: list[str] = []
    revienta_al_borrar = False
    revienta_al_subir_en = -1  # índice de la subida que debe fallar (-1 = ninguna)

    @classmethod
    def reset(cls):
        cls.objetos = {}
        cls.firmas = []
        cls.borrados = []
        cls.revienta_al_borrar = False
        cls.revienta_al_subir_en = -1

    def subir(self, *, clave, contenido, content_type):
        if R2Falso.revienta_al_subir_en == len(R2Falso.objetos):
            # El cliente de verdad convierte cualquier fallo de boto3 en un
            # BusinessError legible (nunca deja salir un 500): el doble tiene
            # que respetar ese contrato o la prueba estaría probando otra cosa.
            raise BusinessError(
                "No fue posible guardar la imagen. Verifique la conexión e intente de nuevo",
                code="r2_error",
            )
        R2Falso.objetos[clave] = (contenido, content_type)

    def enlace_firmado(self, *, clave, segundos, nombre_descarga=None):
        R2Falso.firmas.append((clave, segundos))
        # Con la misma pinta de una URL firmada de verdad (SigV4).
        return (
            f"https://ejemplo.r2.cloudflarestorage.com/lactis/{clave}"
            f"?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires={segundos}"
            f"&X-Amz-Signature=00deadbeef"
        )

    def borrar(self, clave):
        if R2Falso.revienta_al_borrar:
            raise BusinessError(
                "No fue posible borrar la imagen del almacenamiento. Intente de nuevo",
                code="r2_error",
            )
        R2Falso.borrados.append(clave)
        R2Falso.objetos.pop(clave, None)


def enchufar(monkeypatch, modulo) -> type[R2Falso]:
    """Deja el doble puesto en los DOS sitios desde los que se llega a R2.

    El servicio instancia `R2Client` dentro de cada método, así que basta con
    cambiarle el nombre en el módulo que lo usa — eso es lo de siempre. LO QUE
    HAY QUE PARCHEAR ADEMÁS es `app.core.storage`, porque el borrado que espera
    al commit (`borrar_del_bucket_al_confirmar`) vive allá: es la misma regla
    para los soportes de un pago, para los adjuntos de reventa y para el reinicio
    de una empresa, y por eso está escrita una sola vez. Si solo se parcheara el
    servicio, ese borrado saldría a internet de verdad — o, con llaves sin
    configurar, no borraría nada y la prueba pasaría por la razón equivocada.
    """
    import app.core.storage as almacenamiento

    R2Falso.reset()
    monkeypatch.setattr(modulo, "R2Client", R2Falso)
    monkeypatch.setattr(modulo, "r2_configurado", lambda: True)
    monkeypatch.setattr(almacenamiento, "R2Client", R2Falso)
    monkeypatch.setattr(almacenamiento, "r2_configurado", lambda: True)
    return R2Falso
