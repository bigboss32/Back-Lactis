# Continuar: las quincenas que ya existen y el rótulo "pagada · quedó debiendo"

> Nota de traspaso escrita el 27/09/2026 y puesta al día el 04/10/2026, después de la revisión final (vuelta 5).
> Está igual en **Back-Lactis** y en **Front-Lactis** porque el cambio toca los dos.

## En una mirada

- **La revisión final ya se hizo** (vuelta 5, 30/09–04/10/2026). Encontró 15 defectos confirmados y 10 menores. Uno es **crítico y ya está en producción** (viene de `main`): ver C0 abajo. Todos quedaron arreglados con prueba, salvo una decisión del dueño (L8).
- **Nada de la vuelta 5 tiene commit.** Está en el árbol de trabajo de la rama `wip/quincenas-existentes` de los dos repos, encima de los commits `wip(...)`.
- **Antes de desplegar hay que correr tres consultas de solo lectura en Render** (sección "Lo que falta", punto 1), y el orden del despliegue es **primero el backend, después el frontend** (punto 3).

## Dónde está el código

| | Back-Lactis | Front-Lactis |
|---|---|---|
| Rama con el trabajo | `wip/quincenas-existentes` | `wip/quincenas-existentes` |
| `origin/main` (lo que está en producción) | `0274078` | `f853705` |

- **El rótulo ya está en `main`, y por lo tanto en producción.** `0274078` y `f853705` los subió otra sesión el 27/09/2026, junto con el respaldo antes de formatear el equipo. Render despliega el backend desde `main` y Cloudflare el frontend.
- **Lo demás no está desplegado.** La vuelta 4 son los commits `wip(...)` de la rama; la vuelta 5 está sin commit en el árbol de trabajo.
- **Mientras la rama no se despliegue, producción tiene tres problemas que la rama arregla:**
  1. La tarjeta "Le quedaron debiendo a la quesera" pierde deudas cuando hay más de 200 quincenas pagadas.
  2. "Corregir esta quincena" sobre una pagada de julio con la deuda borrada puede mandar a pagarle a alguien que debe.
  3. **(C0, crítico)** Borrarle el pago a una quincena cuya deuda ya se cobró en la siguiente se acepta: la quincena vuelve a quedar "por pagar" y la siguiente sigue descontando la deuda. Medido: $700.000 de leche contra $600.000 de plata, y ninguna pantalla lo muestra.
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
  - **La pantalla hace la misma pregunta que el guardia del servidor**, importada de un solo lugar y no copiada. Cuando el servidor manda el texto del porqué (`candado_aviso`, `avisos_deuda_cobrada`, etc.), la pantalla lo muestra tal cual.
  - **Ningún mensaje afirma algo falso**, como "ya se pagó" cuando no salió un peso o "elimine primero ese pago" cuando no hay pago. **Ningún consejo nombra un botón que el servidor rechace**, ni para esa fila ni para los permisos de quien mira.

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

## Qué hay en la rama (vuelta 4, commits `wip(...)`)

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
  - la deuda de la quincena anterior se comió el neto. **Corrección de la vuelta 5:** a esta fila no se llega con el botón de antes (el guardia de Pagar nació con `saldo_anterior`); solo se llega corrigiendo una pagada y borrándole después el pago, lo que la deja en v2.
- **El PDF de una fila con deuda borrada cierra igual que la pantalla.** Imprime "Pagado" como la suma de los pagos y agrega el renglón "Deuda borrada por la migración + $X".
- **POST y PUT `/recepciones` devuelven `liquidaciones_devueltas_a_borrador: [{id, tipo}]`.** Es el hecho, contado por `recuadrar`, que usa la pantalla para su aviso. No se guarda en la base.
  - Para esto `crud_router` recibe un `write_schema` opcional. Si no se pasa, usa el de lectura.

### Frontend

- **Tarjetas del listado** desde `resumen()`, más una tarjeta "Deuda borrada por reparar" y una marca en cada fila afectada.
- **Detalle de la quincena** (`liquidacion-detail.dialog.ts`): texto del candado coherente con el rótulo; renglón "Pagado" en las pagadas viejas sin registros de pago, para que el desglose sume; la ayuda del stepper sigue a los botones.
- **Filas con deuda borrada** (`deudaBorradaPorReparar`): aviso rojo con la cifra; se ocultan Corregir, Pagar, Abonar, Anular, WhatsApp y Compartir PDF.
- **Recepción diaria y Anticipos** muestran el `candado_aviso` del servidor.
- **Aviso al guardar un día** (`aviso-del-guardado.ts`): sale de `liquidaciones_devueltas_a_borrador`.

## Vuelta 5: la revisión final y sus arreglos (sin commit)

**Cómo se hizo.** Siete revisores con lentes distintos (los cuatro pendientes de la vuelta 4, plata y SQL, paridad pantalla↔servidor, regresiones). Cada hallazgo se aceptó solo si una prueba de verdad lo reproducía, y un segundo agente intentaba refutarlo. Después hubo tres rondas de arreglos, cada una verificada volviendo a correr las reproducciones originales contra el código nuevo. Los hallazgos completos, con sus pruebas, quedaron en la sesión que hizo el trabajo; lo que importa está aquí.

### Plata

- **C0 (crítico, venía de `main`).** `eliminar_pago` rebota si la deuda de esa quincena ya se cobró en otra (`_razon_para_no_borrar_un_pago`). El 422 sale después del `FOR UPDATE` y antes de tocar los soportes. Borrarle el pago a la quincena que **cobró** la deuda sigue permitido (es el paso previo a anularla). La pantalla esconde la basura del pago y dice por qué.
- **Las parejas que `main` ya pudo descuadrar** (`_deuda_cobrada_que_no_cuadra`): Pagar, Marcar pagada y Abonar rebotan en las dos puntas con un texto que dice que los dos comprobantes ya no cuadran. Una pareja sana nunca entra ahí. El hecho le llega a todo el que ve la fila.
- **C13.** El tablero y el balance traen `quincenas_por_reparar` y la pantalla lo dice al lado de "por pagar", para que no se lea como "no se le debe nada a nadie". Una sola definición: `LiquidacionRepository` (`deuda_por_reparar`), compartida con el resumen.
- **C4 / C11.** El PDF de una fila con deuda borrada lleva la marca "PENDIENTE DE REPARAR · VER EL AVISO" y, como primera nota, la posición de hoy (`posicion_de_hoy`, la misma frase del 422). Los renglones y el cierre no cambiaron (regla de oro). La banda de la v2 ya no manda a entregar ese papel.
- **R7.** La vista previa de Corregir ya no dice "se le pagó de más $250.000" cuando de la caja salieron $200.000.

### Mensajes y candados (criterios 1, 2 y 3)

- **C1 / L0.** "Anule primero esa liquidación" sale solo cuando la otra se puede anular y anularla destraba esa acción (`por_que_no_se_anula`, `consejo_deuda_cobrada`). Si la otra tiene pagos, lo dice con la cifra y advierte que los soportes no se recuperan. Si no hay salida dentro del sistema, manda al ajuste en la quincena siguiente. Recalcular pregunta primero el estado.
- **C6 / C7 / R1 / R2 / R3.** El consejo del día (Recepción), del anticipo y del PUT de observaciones sale de la misma pregunta que el botón (`por_que_no_se_corrige`, `_usar_corregir`) y de los permisos de quien mira: Corregir primero cuando lo acepta, después el ajuste, y borrar pagos de último, contándolos ("esos 2 pagos") y avisando que se van los soportes. A Supervisor, Compras, Contador y Consulta: "pídale a un Administrador de la empresa…".
- **C3.** Con adelantos propios ya no dice "sin que saliera un peso": nombra los anticipos y la deuda que cubrieron el valor.
- **C2 / C10.** "Abono" solo cuando salió plata por pagos (`con_abonos`, `RecepcionRead.liquidacion_con_abono`). La 'pagada' cerrada sin pago muestra la frase del servidor (`cerrada_sin_pago`) en vez de "El pago quedó registrado".
- **C5, C8, C9, C12, R4, R5, R10.** Lápiz del precio oculto donde el servidor lo rebota; textos que nombran Anular, Pagar o "elimine el abono" solo para quien tiene ese permiso; Recalcular en la aprobada cubierta por sus anticipos ya no dice "todavía no se le ha pagado nada"; el adelanto que soltó una quincena con deuda borrada no manda a Corregir; el 422 de Pagar ya no habla de "un aviso que no es cierto"; Anular en la pagada de antes de los abonos no manda a borrar pagos que no existen.
- **C14 / R9.** Si `GET /liquidaciones/resumen` falla, la lista dice "No se pudieron cargar los totales" con Reintentar, sin pintar tarjetas en $0 y sin afirmar la causa.
- **L1, L2, L4, R6/L7, R12.** "de el flete"; la cola del transportador con el rótulo; "Se le pagó de más" igual que el PDF; los candados de la fila con deuda borrada y de "Marcar pagada" muestran el texto del servidor; el aviso rojo no repite la cifra.

### Contrato nuevo (todos opcionales en el front, para convivir con el backend viejo)

- `LiquidacionRead`: `aviso_deuda_borrada`, `avisos_deuda_borrada` (por acción), `avisos_deuda_cobrada` (por acción: anular, corregir, recalcular, precio, eliminar_pago, pagar, registrar_pago), `aviso_sin_un_peso_por_la_deuda`, `con_abonos`, `cerrada_sin_pago`.
- `RecepcionRead.liquidacion_con_abono`; `DashboardResponse` y `BalanceResponse`: `quincenas_por_reparar`.

### Rendimiento y concurrencia

- `GET /anticipos` ya no hace una consulta por quincena cobrada (L9). El listado de liquidaciones pasó de 57 a 8 consultas por página (R11).
- Pagar, anular, el PUT de observaciones y la vista previa de Corregir deciden bajo `_bloquear` (L6, R8), así un abono concurrente no hace rebotar una quincena normal con el aviso de la migración.

## Cómo verificar

Montar el entorno (el equipo se formateó; esto se hizo el 30/09/2026):

```bash
# Backend, desde Back-Lactis: Python 3.12, el mismo de producción
py install 3.12
py -V:3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt pypdf pytest-xdist   # pypdf lo piden las pruebas y no está en requirements

# Frontend, desde Front-Lactis: Node 22, el mismo de Cloudflare
npm ci
```

Correr:

```bash
# Backend. DIFERENCIAL_SALIDA evita que una prueba reescriba diferencial_por_litro.json
DIFERENCIAL_SALIDA=/tmp/dif.json .venv/Scripts/python.exe -m pytest tests -q -n 16

# Frontend
npx ng build
npx ng test --watch=false --browsers=ChromeHeadless
```

Resultado el 04/10/2026, con los archivos de auditoría de la rama ya borrados:
- **Backend, suite completa y sin excluir nada:** **2.968 pasan, 0 fallan**, 22 skipped y 16 xfailed (los mismos de siempre). La advertencia de `DecompressionBombWarning` es de pruebas de soportes y es intencional. Al empezar la vuelta 5 eran 2.828: las nuevas son las de cada arreglo, con las cifras del dueño.
- **Frontend:** compila limpio (solo los dos avisos de presupuesto de estilos que ya existían). **589 specs, 0 fallan.**

Si `ng test` o `ng build` fallan con un `SyntaxError` *dentro de* `node_modules`, reinstalar con `npm ci`. El 04/10/2026 `node_modules/typescript/lib/typescript.js` apareció con un bit cambiado (`returo siogleOrMany` por `return singleOrMany`) sin que nadie lo hubiera escrito: el disco reporta buena salud, así que la sospecha es la RAM del equipo. `git fsck` y una búsqueda de palabras con un bit cambiado en todo lo editado no encontraron nada en los repos.

## Lo que falta, en orden

1. **Correr tres consultas de solo lectura en Render, antes de desplegar.**
   - **Filas con deuda borrada por la migración:**
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
   - **Quincenas que cobraron una deuda que ya no existe** (lo que pudo dejar C0 en producción):
     ```sql
     SELECT d.id, d.tipo, p.nombre AS proveedor, d.transportador_id, d.estado, d.version,
            d.periodo_inicio, d.periodo_fin, d.saldo_anterior,
            COALESCE(o.deuda_hoy, 0) AS deuda_de_los_origenes_hoy,
            d.saldo_anterior - COALESCE(o.deuda_hoy, 0) AS descuadre
     FROM liquidaciones d
     LEFT JOIN proveedores p ON p.id = d.proveedor_id
     LEFT JOIN (SELECT deuda_trasladada_a_id AS destino,
                       SUM(CASE WHEN saldo < 0 THEN -saldo ELSE 0 END) AS deuda_hoy
                FROM liquidaciones
                WHERE deleted_at IS NULL AND deuda_trasladada_a_id IS NOT NULL
                GROUP BY deuda_trasladada_a_id) o ON o.destino = d.id
     WHERE d.deleted_at IS NULL AND d.estado <> 'anulada'
       AND d.saldo_anterior > 0
       AND d.saldo_anterior <> COALESCE(o.deuda_hoy, 0)
     ORDER BY d.periodo_inicio;
     ```
   - **Quincenas marcadas como "deuda ya cobrada" que ya no deben nada** (la otra punta de C0):
     ```sql
     SELECT o.id, o.tipo, p.nombre AS proveedor, o.estado, o.version,
            o.periodo_inicio, o.periodo_fin, o.valor_total, o.anticipos, o.pagado, o.saldo,
            o.deuda_trasladada_a_id
     FROM liquidaciones o
     LEFT JOIN proveedores p ON p.id = o.proveedor_id
     WHERE o.deleted_at IS NULL AND o.deuda_trasladada_a_id IS NOT NULL AND o.saldo >= 0
     ORDER BY o.periodo_inicio;
     ```
   - Las dos últimas se probaron contra el caso de Henri armado con el `eliminar_pago` de `main`: lo detectan ($100.000 de descuadre) y con datos sanos no traen nada.
   - Si las tres salen vacías, no hay nada que reparar. Si salen filas, el dueño confirma una por una qué pasó con esa plata y se prepara una reparación de datos con pre-vuelo y post-vuelo, ensayada con `herramientas/ensayo_de_despliegue.py` contra un Postgres de verdad. **No se escribió ninguna reparación.** Mientras existan, esas filas siguen sumando en "por pagar" aunque Pagar las rebote.
2. **Decisiones que le tocan al usuario o al dueño:**
   - **Quincena en $0 porque la deuda anterior se comió el valor.** Se queda "Aprobada" para siempre, a propósito. ¿Otro rótulo, por ejemplo "Aprobada · nada que entregar"? Ligado a esto, **L8**: la tarjeta "Aprobadas por pagar" las cuenta (y a las de deuda borrada), aunque la cifra en plata ya las excluye.
   - **El ejemplo del dueño.** Dijo "vale 500 pero pidió prestado 200", y así no queda debiendo. Se tomó su captura como la regla (el adelanto pasa del valor). Falta que lo confirme.
   - **Textos nuevos que el dueño debería leer:** el de C3 ("…quedó cerrada como pagada sin saldo por entregar, porque los anticipos que se le aplicaron ($80.000) y lo que el tercero quedó debiendo de la quincena pasada ($120.000) cubrieron exacto su valor ($200.000)"); el de C9 ("…todavía no tiene pagos registrados, y al anularla sus anticipos ($300.000) vuelven a quedar pendientes"); los 422 de "los dos comprobantes ya no cuadran"; el consejo del candado del anticipo; el 422 de Pagar en la quincena en $0; el aviso de la vista previa "Le queda debiendo $X: ya se le habían entregado $P…".
   - **El PDF:** el de la fila migrada sin tocar sigue diciendo "Estado: PAGADA" en el encabezado (`estado_visible` no mira la deuda borrada), con la marca "PENDIENTE DE REPARAR" encima. Y las tablas quedaron un poco más apretadas en todos los comprobantes de liquidación, para que el caso denso siga cabiendo en una hoja: conviene que el dueño lo vea impreso.
   - **Cambios fuera de lo pedido, para revisar:** Anular se oculta en `version > 1`; Recalcular se oculta en un borrador cuya deuda ya se cobró; aparece "Se recalculó la liquidación de este día" en borradores; una anulada con deuda borrada sigue ofreciendo compartir; el 422 de un día ofrece Corregir (solo para el precio) en toda quincena de leche que Corregir acepta.
3. **Desplegar, solo con el visto bueno del usuario y después de hacer commit:**
   1. **Primero el backend** (Render). Con el back nuevo y el front viejo no se arriesga plata: los guardias están en el servidor.
   2. Comprobar que `GET /api/v1/liquidaciones/resumen` responde 200.
   3. **Después el frontend** (Cloudflare). Al revés, el front nuevo contra el back viejo deja la lista sin tarjetas hasta que llegue el backend.
   4. Pedirle a quien tenga Lactis abierto que recargue (Ctrl+F5): no hay service worker, y una pestaña vieja conserva los textos de antes.
4. **Deudas conocidas que no se tocaron:**
   - En una pareja descuadrada (punto 1), la quincena que cobró, mientras está en borrador o pagada, todavía explica el descuento como "se le cobran acá, una sola vez"; y a un rol sin `administrar` la quincena que dejó la deuda le muestra una frase propia de la pantalla (cierta) en vez del texto del servidor. Solo existe si producción tiene esas filas.
   - El rótulo del cierre ("se le pagó de más" / "le queda debiendo") se decide en la pantalla con `causaDeLaDeuda`, una copia de la regla del backend. No es falso, pero lo ideal es que `LiquidacionRead` traiga el rótulo.
   - `tiene_pagos` sigue siendo `pagado > 0`.
   - `generar` todavía serializa `deudas_cobradas` y `deuda_trasladada_a` con una consulta por fila nueva.
   - El aviso del día en `GET /recepciones` lee `pagado` y `pagos` sin candado (es solo pantalla y la ventana es de milisegundos).
   - En varias pantallas que vienen de `main`, el error de carga dice "la consulta no alcanzó a llegar" también ante un 500.
   - La rama 'pagada' del candado de Recalcular no da consejo, mientras el candado del anticipo de esa misma quincena ofrece Corregir.
   - `_puede` está repetido en `recepcion/service.py` y en `liquidaciones/service.py`; `terceros_le_quedan_debiendo` podría usar `LiquidacionRepository.deuda_sin_cobrar()`.
   - Medir con `EXPLAIN ANALYZE` en Postgres la subconsulta de `quincenas_por_reparar` (subconsulta correlacionada sobre `pagos_liquidacion`).
   - Tareas aparte que ya estaban: desempatar por `id` el orden del listado de liquidaciones; que el tablero y la tarjeta "Aprobadas por pagar" usen la misma regla.

## Archivos de auditoría

- **Los de la rama ya se borraron** (sin commit): 15 `tests/test_zz_*.py` y 8 `src/app/**/zz-*.spec.ts`. Eran reproducciones que afirmaban los defectos viejos y fallaban; la lógica que probaban quedó en las pruebas nuevas. Si hiciera falta verlos, siguen en los commits `chore(backup)`.
- **Cinco `test_zz_*` vienen de `main` y se quedan:** `test_zz_auditor_contaminacion`, `test_zz_auditoria_privacidad_productor`, `test_zz_auditoria_privacidad_productor2`, `test_zz_humo_anticipos_correccion` y `test_zz_humo_borrar_adelanto` (32 casos, entre ellos los de privacidad del PDF del productor). Pasan. Ojo: el patrón `--ignore-glob="tests/test_zz_*"` que se usó en las vueltas anteriores también los dejaba por fuera; ya no hace falta.
