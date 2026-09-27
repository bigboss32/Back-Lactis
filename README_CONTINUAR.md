# Continuar: las quincenas que ya existen y el rótulo "pagada · quedó debiendo"

> Nota de traspaso escrita el 27/09/2026 para que otra persona o IA siga el trabajo.
> Está igual en **Back-Lactis** y en **Front-Lactis** porque el cambio toca los dos.

## Dónde está el código

| | Back-Lactis | Front-Lactis |
|---|---|---|
| Rama con el trabajo | `wip/quincenas-existentes` | `wip/quincenas-existentes` |
| `origin/main` (lo que está en producción) | `0274078` | `f853705` |

- **El rótulo ya está en `main`, y por lo tanto en producción.** `0274078` y `f853705` los subió otra sesión el 27/09/2026, junto con el respaldo antes de formatear el equipo. Render despliega el backend desde `main` y Cloudflare el frontend.
- **Lo demás no está desplegado.** El trabajo de este documento son los commits `wip(...)` de la rama.
- **Mientras la rama no se despliegue, producción tiene dos problemas que la rama arregla:**
  1. La tarjeta "Le quedaron debiendo a la quesera" pierde deudas cuando hay más de 200 quincenas pagadas.
  2. "Corregir esta quincena" sobre una pagada de julio con la deuda borrada puede mandar a pagarle a alguien que debe.
- **No hay migraciones nuevas**: `alembic/versions` no se tocó, así que desplegar no cambia el esquema.

## Contexto que hay que saber antes de tocar nada

- **Lactis está en producción**, con un cliente real y plata real. El dueño no es técnico y **verifica cada cifra a mano con calculadora**.
- **Regla de oro:** todo desglose suma exacto la cifra grande.
  - `neto = valor_total − anticipos − saldo_anterior`
  - `saldo = neto − pagado`
- Reglas de trabajo con el usuario:
  - No hacer commit ni push sin que lo pida.
  - Nunca subir llaves reales de Wompi o R2 (el `.env` está en el gitignore).
  - No tocar `D:\maroa\quesera-erp\mobile`.
  - No tocar contenedores de Docker ajenos.
  - Qué no puede llevar cada PDF:
    - El estado de cuenta del cliente no lleva gastos, costos, márgenes ni nombres de productores.
    - El de productor no lleva precios de venta, márgenes, gastos ni nombres de clientes.
- Tres criterios que se aplicaron en todo esto:
  - **Un botón que el servidor siempre rechaza es un defecto.**
  - **La pantalla hace la misma pregunta que el guardia del servidor**, importada de un solo lugar y no copiada. Cuando el servidor manda el texto del porqué (`candado_aviso`), la pantalla lo muestra tal cual.
  - **Ningún mensaje afirma algo falso**, como "ya se pagó" cuando no salió un peso o "elimine primero ese pago" cuando no hay pago.

## Qué pidió el dueño

1. **El rótulo.** "Cuando quede debiendo, que el estado quede pagada o pagada debiendo, no solo aprobada." Caso: la quincena vale $500.000 y el tercero ya había pedido $700.000.
   - Se hizo como un **rótulo** (`Liquidacion.estado_visible`), no como un estado nuevo en la base. La columna `estado` la leen unas 20 partes del sistema; cambiarla trabaría días de Recepción diaria que se dejan corregibles a propósito.
   - Commits `0274078` (back) y `f853705` (front). El de front también trae el historial completo de correcciones.
2. **"¿Y qué pasa con las que ya cumplen esa regla?"**, es decir, las quincenas que ya estaban en la base.

### La respuesta que se le dio

**Casi todas cambian solas y no hay que tocar datos**, porque el rótulo se calcula al leer, a partir del `saldo` guardado. Las que salen como "pagada · quedó debiendo":
- aprobadas con deuda, pendiente o ya cobrada en la quincena siguiente;
- pagadas con el botón Pagar de antes (01–04/08/2026) cuando el tercero debía;
- corregidas que quedaron pagadas de más.

**La excepción es un problema viejo de datos, anterior a este trabajo.**
- La migración `a5e7c1b4d9f2` (pagos parciales, 01/08/2026, ya corrida en producción) hizo `pagado = valor_total − anticipos` y `saldo = 0` en todas las `pagada`.
- En las pagadas de julio donde el adelanto pasaba del valor, eso **borró lo que el tercero quedaba debiendo**. Esa deuda no se ve y nunca viajó a la quincena siguiente.
- La marca de esas filas es `Σpagos − pagado > 0`. Hoy se llama `Liquidacion.deuda_borrada_por_la_migracion`.

## Qué hay en la rama

### Backend

- **B1 · `GET /api/v1/liquidaciones/resumen?tipo=&desde=&hasta=`.** Arma las tarjetas del listado en una sola consulta SQL, sin el tope de 200 filas.
  - Ese tope hacía que la tarjeta "Le quedaron debiendo" perdiera deudas.
  - El estado se decide una sola vez en `_criterios_del_estado` y `_criterios_de_tipo_y_periodo`, compartidos con `listar_filtrado`.
  - Trae también `por_reparar` y `deuda_borrada`, las filas con deuda borrada.
- **Una sola regla de "por pagar".** `LiquidacionRepository.saldo_por_pagar()` alimenta las tarjetas, el tablero (`reportes`) y el balance (`contabilidad`). Deja por fuera las filas con deuda borrada, porque no se pueden pagar.
- **Un solo candado.** `cifras_congeladas(liq)` = `ya_salio_papel_o_plata(liq) or liq.deuda_ya_cobrada`.
  - Lo usan los anticipos y Recepción diaria; `_traba_el_dia` la importa.
  - `AnticipoRead.candado_aviso`, `RecepcionRead.candado_aviso` y `CeldaGrilla.candado_aviso` traen el porqué con el mismo texto que da el 422.
  - En los anticipos el texto sale de `_por_que_no_se_mueve`; en Recepción, de `_por_que_esta_trabada`.
- **Guardia para las filas con deuda borrada.** `_exigir_sin_deuda_borrada` rechaza corregir, la vista previa, pagar, abonar, anular, mover anticipos y el PUT de observaciones.
  - El mensaje `_aviso_deuda_borrada` dice la posición de hoy: cuánto debe todavía el tercero, o cuánto falta de verdad por entregarle.
  - `eliminar_pago` ya no recorta a cero en esas filas, para que la cifra borrada no cambie.
- **Quincenas pagadas sin que saliera un peso.** `pagada_sin_que_saliera_un_peso()` y `por_que_no_salio_un_peso()` evitan que el día o el anticipo digan "ya se pagó". Cubren dos casos:
  - la quincena quedó debiendo y se marcó pagada con el botón de antes;
  - la deuda de la quincena anterior se comió el neto.
- **El PDF de una fila con deuda borrada cierra igual que la pantalla.** Imprime "Pagado" como la suma de los pagos y agrega el renglón "Deuda borrada por la migración + $X".
- **POST y PUT `/recepciones` devuelven `liquidaciones_devueltas_a_borrador: [{id, tipo}]`.** Es el hecho, contado por `recuadrar`, que usa la pantalla para su aviso. No se guarda en la base.
  - Para esto `crud_router` recibe un `write_schema` opcional. Si no se pasa, usa el de lectura.
- **Pruebas nuevas:** `tests/test_liquidacion_*.py` y `tests/test_recepcion_*.py`, sin commit hasta esta rama. Cada archivo tiene el caso con las cifras del dueño.

### Frontend

- **Tarjetas del listado** desde `resumen()`, más una tarjeta "Deuda borrada por reparar" y una marca en cada fila afectada.
- **Detalle de la quincena** (`liquidacion-detail.dialog.ts`):
  - Texto del candado coherente con el rótulo.
  - Renglón "Pagado" en las pagadas viejas, que no tienen registros de pago, para que el desglose sume.
  - "Marcar pagada" se oculta cuando la deuda vieja se comió el neto (`laDeudaViejaSeLlevoElNeto`, con las tres condiciones de `_no_sale_un_peso_por_la_deuda`).
  - La ayuda del stepper sigue a los botones (`ayudaEnLugarDeLaDelEstado`).
  - El texto del candado de Recalcular no afirma pagos que no existen.
- **Filas con deuda borrada** (`deudaBorradaPorReparar`):
  - Aviso rojo con la cifra, y el desglose cierra con el renglón de la deuda borrada.
  - Se ocultan Corregir, Pagar, Abonar, Anular, WhatsApp y Compartir PDF.
- **Recepción diaria y Anticipos** muestran el `candado_aviso` del servidor.
- **Aviso al guardar un día** (`aviso-del-guardado.ts`): sale de `liquidaciones_devueltas_a_borrador`. Ya no dice "volvió a borrador" cuando no volvió.

## Cómo verificar

```bash
# Backend (Windows, desde Back-Lactis). DIFERENCIAL_SALIDA evita que una prueba reescriba diferencial_por_litro.json
DIFERENCIAL_SALIDA=/tmp/dif.json .venv/Scripts/python.exe -m pytest tests -q -k "liquidac or recepc or anticip or reporte or contab or tablero or dashboard"

# Frontend (desde Front-Lactis)
npx ng build
npx ng test --watch=false --browsers=ChromeHeadless
```

Resultado al escribir esto:
- **Backend:** la corrida de arriba da **657 pasan, 0 fallan**, 1 skipped y 5 xfailed, en 25 minutos. La advertencia de `DecompressionBombWarning` es de una prueba de soportes y es intencional.
  - Se corrió sin los archivos de auditoría: `--ignore-glob="tests/test_zz_*"`.
  - Los 14 archivos nuevos y el del rótulo pasan solos: 68 casos.
  - La suite completa del backend (más de 2.900 pruebas) no se corrió después de la última vuelta.
- **Frontend:** compila limpio. Pasan todos los specs que no son de auditoría. Los 13 que fallan son de los archivos `zz-*` (ver la última sección).

## Lo que falta, en orden

1. **Revisión final de la última vuelta.** Las correcciones de la vuelta 4 están en la rama, pero el revisor se cortó. Hay que revisar el diff con atención a cuatro cosas:
   - que los mensajes de Recepción digan la verdad en una quincena corregida sin pagos;
   - `pagada_sin_que_saliera_un_peso` en el caso "la deuda vieja se comió el neto";
   - el PDF de una fila con deuda borrada;
   - el candado de Recalcular.
2. **Saber si en producción hay filas con deuda borrada.** El usuario tiene que correr esta consulta en Render (solo lee):
   ```sql
   SELECT l.id, l.tipo, p.nombre AS proveedor, l.transportador_id, l.estado, l.version,
          l.periodo_inicio, l.periodo_fin, l.valor_total, l.anticipos, l.pagado, l.saldo,
          COALESCE(pg.suma, 0) - l.pagado AS deuda_borrada
   FROM liquidaciones l
   LEFT JOIN proveedores p ON p.id = l.proveedor_id
   LEFT JOIN (SELECT liquidacion_id, SUM(valor) AS suma
              FROM pagos_liquidacion GROUP BY liquidacion_id) pg ON pg.liquidacion_id = l.id
   WHERE l.deleted_at IS NULL AND l.estado <> 'anulada'
     AND COALESCE(pg.suma, 0) - l.pagado > 0
   ORDER BY l.periodo_inicio;
   ```
   - Si sale vacía, no hay nada que reparar.
   - Si salen filas, el dueño confirma una por una si esa plata se cobró por fuera del sistema. Para las que no, se prepara una migración de datos con pre-vuelo y post-vuelo:
     - caso normal (`estado='pagada'`, `version=1`, sin pagos): `saldo = pagado`, `pagado = 0`;
     - para las que ya se corrigieron o se pagaron, hay que pensarla aparte.
   - Hay que ensayarla con `herramientas/ensayo_de_despliegue.py` contra un Postgres de verdad. **No se escribió ninguna reparación.**
3. **Decisiones que le tocan al usuario o al dueño:**
   - **Quincena en $0 porque la deuda anterior se comió el valor.** Se queda "Aprobada" para siempre, a propósito (`_no_sale_un_peso_por_la_deuda`: no se marca pagada lo que nadie pagó). ¿Otro rótulo, por ejemplo "Aprobada · nada que entregar"?
   - **El ejemplo del dueño.** Dijo "vale 500 pero pidió prestado 200", y así no queda debiendo. Se tomó su captura como la regla (el adelanto pasa del valor). Falta que lo confirme.
   - **Cambios que los agentes hicieron fuera de lo pedido, para revisar:**
     - Anular se oculta en `version > 1`.
     - Recalcular se oculta en un borrador cuya deuda ya se cobró.
     - Aparece el aviso "Se recalculó la liquidación de este día" en borradores.
     - Una anulada con deuda borrada sigue ofreciendo compartir.
     - El 422 de un día corregido dice "si lo que está mal es el precio, use Corregir; si es otra cifra, regístrelo en la quincena siguiente".
4. **Desplegar.** Hay que subir el back y el front juntos: el backend ya rechaza acciones que el frontend viejo todavía ofrece. Solo con el visto bueno del usuario.
5. **Deudas conocidas que no se tocaron:**
   - `tiene_pagos` es `pagado > 0`. Miente en filas con deuda borrada que tienen pagos reales; hoy no rompe nada porque esas filas están trabadas por otra razón.
   - El mensaje del anticipo manda todas las cifras a "Corregir", cuando Corregir no cambia litros.
   - Quedaron dos tareas aparte:
     - desempatar por `id` el orden del listado de liquidaciones, porque Postgres puede repetir o saltar filas entre páginas;
     - que el tablero y la tarjeta "Aprobadas por pagar" usen la misma regla (hoy el tablero suma los borradores).

## Archivos de auditoría: están en la rama y hay que borrarlos antes del merge

Estos archivos entraron a la rama en los commits `chore(backup)`, para no perderlos al formatear el equipo:
- `tests/test_zz_*.py`
- `src/app/**/zz-*.spec.ts`

Son reproducciones que dejaron las auditorías, y muchas **afirman el defecto viejo**, así que fallan ahora que está arreglado. Por eso:
- Las suites solo quedan limpias sin ellos. En el backend se corren con `--ignore-glob="tests/test_zz_*"`; en el frontend fallan 13 specs `zz-*` y ninguno más.
- **Hay que borrarlos antes de llevar la rama a `main`.**

La lógica que probaban quedó en las pruebas nuevas.
