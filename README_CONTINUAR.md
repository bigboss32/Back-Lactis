# Continuar: las quincenas que ya existen y el rótulo "pagada · quedó debiendo"

> Nota de traspaso escrita el 27/09/2026 y puesta al día el 12/10/2026: las vueltas 5 (revisión final) y 6 (precio para varios días, filtros y grilla escribible) están en producción desde el 07/10/2026; el 12/10/2026 se subió, a pedido del usuario, lo que vino después: los candados de concurrencia del backend (07/10), que un anticipo no se descuente dos veces (12/10) y Desde/Hasta en los listados del frontend (09/10). El detalle de cada commit está en `git log`.
> Está igual en **Back-Lactis** y en **Front-Lactis** porque el cambio toca los dos.

## En una mirada

- **La revisión final (vuelta 5, 30/09–04/10/2026) ya está en `main` y en producción.** Encontró 15 defectos confirmados y 10 menores, uno de ellos **crítico** (C0, abajo). Todos quedaron arreglados con prueba, salvo una decisión del dueño (L8).
- **La vuelta 6 (04/10/2026) ya está en `main` y en producción** (subida el 07/10/2026 a pedido del usuario: back `c584694`, front `2cb59f4`; primero el backend, y solo cuando Render ya mostraba `/precios` en su `openapi.json`, el frontend; Cloudflare sirve archivos idénticos byte a byte a los del build local). Ver la sección "Vuelta 6" abajo.
- **Las tres consultas de solo lectura en Render siguen pendientes** (sección "Lo que falta", punto 1), y falta que el usuario pruebe la grilla con el lápiz S Pen en la tablet. A quien tenga Lactis abierto hay que pedirle Ctrl+F5.

## Dónde está el código

| | Back-Lactis | Front-Lactis |
|---|---|---|
| Rama con el trabajo | `wip/quincenas-existentes` | `wip/quincenas-existentes` |
| `origin/main` (lo que está en producción) | `c584694` | `2cb59f4` |

- **Hasta la vuelta 5 todo está en `main`, y por lo tanto en producción** (se subió el 04/10/2026, primero el backend y después el frontend; Render despliega el backend desde `main` y Cloudflare el frontend). Eso incluye el rótulo "pagada · quedó debiendo", la vuelta 4 y la revisión final, y con ellas el arreglo de C0 (borrarle el pago a una quincena cuya deuda ya se cobró en la siguiente se aceptaba y dejaba $700.000 de leche contra $600.000 de plata).
- **La vuelta 6 también está en `main`** (ver arriba). Si esta nota aparece con un commit más en la rama `wip` que en `main`, ese commit es solo esta actualización del texto: se dejó sin desplegar a propósito para no reiniciar producción por un cambio de documentación.
- **No hay migraciones nuevas** en ninguna de las dos: `alembic/versions` no se tocó, así que desplegar no cambia el esquema.

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

## Vuelta 6: precio para varios días, filtros y grilla escribible (en producción desde el 07/10/2026)

Salió de lo que pidió el usuario después del despliegue de la vuelta 5. Cada pieza se probó con la suite y **en un navegador real** contra un backend local con datos de prueba.

### Backend (solo `liquidaciones`)

- **`POST /api/v1/liquidaciones/{id}/precios`** (permiso `liquidaciones:editar`) con `{precio_litro, detalle_ids}`; `detalle_ids` vacío o ausente = todos los días. Es **todo o nada**: si algún día quedaría con el valor negativo, no cambia ninguno y el 422 nombra los días (hasta 5 y "y N más").
  - Mismas reglas del lápiz de un día (`_razon_para_no_cambiar_el_precio`: solo borradores, nada con deuda cobrada, etc.), `_bloquear` (FOR UPDATE), UN recálculo y una auditoría por día cambiado más una de la liquidación.
  - Reescribe `precio_litro`, `valor_bruto` y `valor_neto` de cada `RecepcionLeche` y recalcula con `_recalcular_desde_recepciones`. Los días que ya tienen ese precio no se tocan.
  - Un precio que **redondea a $0,00** (0,004) se rechaza en las dos puertas, la de un día y la masiva ("al menos un centavo"); `gt=0` solo miraba el número tal como llegó.
  - Si falta la recepción de un día, el 422 la nombra y el consejo respeta el permiso: a un Administrador se le dice que anule y regenere, a quien solo edita (Compras) que se lo pida a un Administrador.
- Pruebas: `tests/test_liquidacion_precio_varios_dias.py` (36). Se les hicieron 6 mutaciones y todas murieron.

### Frontend

- **Liquidaciones, listado.** Se quitaron Entidad, Desde, Hasta y los chips "Rápido". Ahora: el **selector de quincena** (`shared/selector-quincena.ts`, el mismo de "Generar quincena": el mes con flechas y los botones 1.ª y 2.ª) y **Estado y Tipo en botones** (`shared/filtro-por-opciones.ts`). *(Desde y Hasta se quitaron aquí, y se pidieron de vuelta el 09/10/2026: ver "Desde y Hasta de vuelta en los listados". Lo de "solo una quincena o todo" y el descarte de fechas guardadas que no son quincena ya no vale.)* La lista y las tarjetas llevan un turno por consulta (la respuesta vieja no pisa la nueva).
- **Recepción, listado.** Igual: selector de quincena y la **Ruta en botones**; siguen el buscador de proveedor y el desplegable de Proveedor. Arranca en la quincena de hoy **entera** (antes "del 1 hasta hoy"). *(Ya no se descartan las fechas sueltas guardadas: ver "Desde y Hasta de vuelta".)* Mismos turnos por consulta.
- **Detalle de la liquidación, rediseñado** (`liquidacion-detail.dialog.*`): el resumen va primero y en dos columnas (el desglose a la izquierda; a la derecha, del valor total al saldo; una columna cuando la tarjeta mide menos de 600 px), la tabla más compacta, el pie con botones de texto y el paso a paso del estado en una sola línea. El diálogo mide hasta 760 px (`panelClass: 'dialogo-detalle'`: Material 3 ignora `width` y lo limita a 560).
  - **Mismo precio para varios días:** el campo "Precio por litro de todos los días" con su botón ("Poner $ 2.000 a los 15 días": el botón dice **cómo se leyó el precio**, y si queda a más de 3 veces o menos de la tercera parte del de hoy se pide confirmación) y el **cuadrito de cada precio, que se arrastra** hacia abajo o arriba para copiarlo a las filas por las que pasa. El borde de la tabla la hace correr sola, pero **espera 250 ms, arranca despacio y solo si el puntero ya se movió**; la barra se queda pegada arriba mientras se arrastra para que se vea "Suelte para copiar $X a N días". Solo botón principal y primer puntero. No hay cuadrito en los días de $0.
  - **Una sola escritura a la vez** (`ocupado()`): mientras se guarda un precio no se puede aprobar, recalcular, anular, pagar ni abrir otro lápiz. Antes Aprobar podía salir con el PUT del precio todavía en vuelo.
  - **Arreglo de plata que ya existía:** al abrir el lápiz de un precio con decimales (1750,5) y salir sin tocar nada, se leía "1750.5" como 17.505. `precioComoSeEscribe` lo escribe con coma; se arregló también en Corregir quincena.
- **Recepción, grilla de quincena: cada celda es un campo de texto** (`recepcion-grilla.tab.*`, `litros-escritos.ts`). Se pidió para escribir **con el lápiz digital de la tablet Samsung**: la escritura a mano a texto de Chrome en Android 13+ solo funciona sobre un `<input>` de verdad, y eso es lo que son ahora (un botón o un cuadro no la reciben). **No se pudo probar con el S Pen**: está probado con teclado y con texto insertado como lo entrega un reconocedor, pero conviene que el usuario lo pruebe en la tablet.
  - Se escribe encima y se guarda con Enter (baja a la celda de abajo), al pasar a otra celda o al tocar fuera; Esc deshace; flechas arriba y abajo se mueven; Alt+Enter o F2 abren el día. Lo escrito y sin guardar se ve en ámbar.
  - **Solo se guardan los litros** (`PUT` parcial con `cantidad_litros`; para un día nuevo, `POST` con fecha, proveedor y litros; el precio sale del proveedor). Lo que no se entiende ("abc"), el cero, el vacío o lo que no cambió **no se manda**; dejar una celda con registro en blanco **no lo borra** (se dice cómo quitarlo). Se leen a la colombiana: `52,5` y `52.5` son 52 litros y medio, `1.500` es mil quinientos, `1,500` se rechaza por ambiguo.
  - **Paridad con el diálogo del día:** antes de escribir se vuelve a pedir el día entero y se le pregunta al servidor (`campos_bloqueados`, `candado_aviso` tal cual). Si el día está en una liquidación **aprobada** se pide confirmación antes, con las mismas palabras de la advertencia del diálogo; después se dice qué le pasó a las liquidaciones (`avisoDelGuardado`). Después de cada guardado se vuelve a pedir la grilla: los totales son los del servidor.
  - Los días con **cifras en firme (candado) siguen siendo el botón** de siempre. El ícono ⤢ de la esquina de la celda (solo con el mouse encima o enfocada) abre el día completo: transportador, precio, observaciones y eliminar. Un clic en él justo después de escribir espera a que termine el guardado.
- Otros: presupuesto de estilos por componente de 8 kB a 12 kB en `angular.json` (el diálogo de detalle ya mide más); `shared/quincena.ts` gana `quincenaDeLaFecha`; `cifras-de-la-quincena.ts` gana `precioComoSeEscribe`.

### Cómo se revisó

- Dos revisiones independientes de solo lectura, con lentes distintos y un escéptico por hallazgo que intentaba refutarlo, y dos rondas de **pruebas de mutación** sobre copias aisladas (se rompe el código a propósito y se mide si alguna prueba lo nota). La del diálogo de detalle confirmó 16 hallazgos; la de la grilla, el listado y los arreglos, 36 (muchos repetidos entre lentes). **Todos se arreglaron con prueba**, entre ellos los que tocan plata: con el filtro de transportador o con un proveedor retirado las celdas vacías ya no son campos (el servidor rebota ahí); un "5" a medias ya no se guarda cuando un diálogo o otra ventana se lleva el foco; tras un guardado fallido o de resultado incierto la grilla se vuelve a pedir; F2 / Alt+Enter guardan antes de abrir el día; `52 5` ya no se lee 525 ni `0.500` quinientos; el listado de Recepción ya no deja la lista vieja bajo un filtro nuevo cuando la consulta falla. Los sobrevivientes de la mutación se convirtieron en pruebas. Quedan como descartados, a propósito, los hallazgos que pedían avisar de lo que ya no se puede hacer en el listado (día suelto, mes entero).
- **Deudas conocidas de esta vuelta:**
  - **Orden de los candados leche → recepción frente a recepción → leche** (ver arriba): hoy un 409 reintentable; el arreglo de fondo está sin hacer. Pasa con los mismos actores desde antes de esta vuelta (precio masivo, Anular, Corregir).
  - ~~Un anticipo se puede descontar dos veces~~ **(arreglado el 12/10/2026, ver "Un anticipo no se descuenta dos veces")**; quedan de la misma clase la nómina de empleados y editar/borrar un anticipo suelto (sin candado, anteriores).
  - Las fechas escritas a mano en los calendarios de toda la app se leen como mes/día/año (ya salió la tarea aparte para arreglarlo).
  - Las celdas de la grilla miden unos 40 px en la tablet: se mantuvo la cuadrícula compacta para que entre la quincena. Si el lápiz se siente apretado, es lo primero que hay que subir.

## Candados de concurrencia en precio, Recalcular, Aprobar y Recuadrar (07/10/2026, subido el 12/10/2026)

**Qué faltaba.** Pagar, Abonar, Anular, Corregir y el precio masivo ya leían la liquidación con `_bloquear` (FOR UPDATE + `populate_existing`). El **precio de un día**, **Recalcular**, **Aprobar** y **Recuadrar** no: leían el estado y los totales sin candado y escribían encima de otro usuario.

**Medido en un Postgres de verdad** (`herramientas/carrera_de_candados.py`: detiene a una petición después de que leyó, deja pasar a la otra y revisa las igualdades que el dueño verifica a mano), con el código de antes:
- Aprobar con un anticipo por descontar mientras otro cambia el precio de un día: `saldo = 355.000` donde debía ser `365.000` (con el precio de todos los días, `400.000`): **la regla de oro rota en el comprobante que enseguida se paga**, y el usuario ve aprobada una cifra distinta de la que quedó guardada.
- Aprobar mientras otro **anula**: quedaba "aprobada" con **0 de sus 3 días** apartados.
- Recalcular una quincena desactualizada mientras otro la aprueba: el papel aprobado cambiaba de cifra después de aprobado.
- Con el código nuevo, en las cinco carreras la segunda petición **espera el candado**, relee y rebota con su 422 de siempre (o sigue, según quién gane), y todo cuadra.

**Qué se hizo** (`liquidaciones/service.py`): `_bloquear` justo después de `get_or_fail` en `actualizar_precio_detalle`, `recalcular`, `aprobar` y `recuadrar`. En `recuadrar` importa porque Recepción lee sin candado el comprobante de flete que cobra un viaje ya cobrado (`_volver_al_viaje_ya_cobrado`); los demás llamadores ya lo traen bloqueado, y pedirlo otra vez en la misma transacción no cuesta nada (ya se hizo flush, releer no pierde nada).

**Un abrazo mortal ahora es un 409 y no un 500** (`app/core/exceptions.py`): Postgres tumba con 40P01 a una de dos peticiones que se esperan entre sí, y con 40001 a un fallo de serialización. Ambos revierten la transacción entera, así que no queda nada a medias; salían como "Error interno del servidor". Ahora: 409, código `concurrency_conflict` y un texto que dice que no se perdió nada y que vuelva a intentar. Cualquier otro error de la base de datos sigue siendo el 500 de siempre.

**Por qué esto último hace falta (riesgo que el cambio ensancha, dicho sin adornos).** El precio de un día ahora toma el candado de la liquidación **antes** de escribir la recepción (liquidación → recepción), igual que ya hacían el precio masivo, Anular y Corregir. Pero hay escritores que van al revés (recepción → liquidación): `RecepcionService._bajar_las_fotos` (anotar o editar un día de **día fijo** reparte el flete entre las recepciones de todos los proveedores del grupo y después actualiza sus liquidaciones de leche) y el botón Recalcular de un comprobante de **flete** con la tarifa cambiada. Dos de esas operaciones sobre el mismo día pueden abrazarse. Ventana muy estrecha, y el resultado es un 409 con la transacción revertida, no plata descuadrada. Antes del cambio el precio de un día no esperaba a nadie, y en vez de abrazarse podía pisar en silencio lo que la otra operación estaba escribiendo (justo lo que midió la herramienta). **El arreglo de fondo** (tomar siempre primero las liquidaciones de leche de todos los días que se van a escribir, en orden por id, en `_bajar_las_fotos`, en `rehacer_las_fotos_sueltas` y en el Recalcular del transportador) **no se hizo**: toca la plata del flete en un servicio de más de dos mil líneas y merece su propia vuelta y su propia prueba contra Postgres.

**Cómo se prueba.** SQLite descarta el FOR UPDATE, así que la suite fija: que el candado se pida, en qué orden (candado → guardia → recálculo / candado → anticipos → estado), y que la petición **relea** lo que otro cambió por debajo (la fila "vieja" en memoria + un UPDATE por debajo). Sin el candado esas pruebas fallan; se verificó quitándolo. El FOR UPDATE en sí lo prueba `herramientas/carrera_de_candados.py` (levanta su **propio** contenedor `lactis-carrera-candado`, solo en `127.0.0.1:15433`, y lo borra al terminar; no toca ningún otro):

```bash
.venv/Scripts/python.exe herramientas/carrera_de_candados.py --antes <commit sin el candado>
```

Con el árbol de trabajo igual a `HEAD` hay que pasarle `--antes` (si no, las dos corridas usan el mismo código y lo avisa). Tests: `tests/test_liquidacion_candado_precio_recalcular_aprobar.py`, `tests/test_liquidacion_candado_sobrevivientes.py` (los mutantes que sobrevivieron a la primera ronda: también fijan los candados que ya existían en Abonar, Anular y Pagar) y `tests/test_concurrencia_409.py`.

**Revisión.** Cuatro lentes independientes con un escéptico por hallazgo (10 confirmados: el candado de `recuadrar`, los comentarios que decían que `recalcular` y `recuadrar` llegaban siempre bloqueados, el riesgo de abrazo y su 409, y cinco de la herramienta, todos arreglados) y tres rondas de mutación sobre copias aisladas. La tercera (09/10/2026: 24 y 20 mutantes, 20 y 13 muertos) dejó 9 sobrevivientes, todos con prueba que los mata (`tests/test_liquidacion_candado_sobrevivientes_ronda2.py`, `tests/test_liquidacion_candado_recuadrar_sobrevivientes.py`, `tests/test_concurrencia_409_sobrevivientes.py`): el `lazyload` de las dos puntas de la deuda en `_bloquear` (hace que `populate_existing` suelte la punta que la petición ya tenía y la relea; es código anterior a esta vuelta y no tenía ninguna prueba), usar lo que **devuelve** el candado en `recalcular`, `aprobar` y `recuadrar`, no tomar el candado antes de comprobar la empresa (`get_or_fail`), el guardia de "pagada" de `recuadrar`, el `getattr` del manejador de concurrencia, que el texto del 409 diga la verdad ("no se perdió ni se movió nada") y que otro error de la base siga registrándose como error.

## Un anticipo no se descuenta dos veces (12/10/2026)

**El defecto (existía desde antes y estaba anotado como deuda).** Quien COBRA anticipos —Generar una quincena, o Aprobar/Recalcular un borrador que barre los que se registraron después— lee los anticipos sueltos (`pendientes_de`: `liquidacion_id` en nulo), los marca con el id de la quincena y los resta del total. Sin candado, dos peticiones a la vez leían el MISMO anticipo como suelto y lo restaban las dos; la marca queda apuntando a la última que escribió, y de la otra no queda rastro. **Medido en un Postgres de verdad** con `herramientas/carrera_de_candados.py` (dos escenarios nuevos, uno por cada sentido): Aprobar julio mientras otra persona Genera agosto, con un anticipo de $500.000 sin descontar → julio con $500.000 de anticipos y agosto con otros $500.000: **$1.000.000 restados por $500.000 entregados**, y a un productor se le habría pagado de menos.

**El arreglo.**
- `AnticipoRepository._consulta_de_sueltos(...)` / `pendientes_de(..., para_marcar=True)` y `pendientes_transportador(..., para_marcar=True)`: `FOR UPDATE` a secas (la segunda petición ESPERA; al soltarse Postgres vuelve a evaluar `liquidacion_id IS NULL` y el anticipo ya no sale, igual que `deudas_sin_cobrar`), `ORDER BY fecha, id` (orden fijo de bloqueo), `lazyload` de `proveedor`/`transportador`/`empleado` (son `lazy="joined"` y Postgres rechaza un `FOR UPDATE` con LEFT JOIN: 0A000) y `populate_existing`. **Por omisión NO bloquea** (`para_marcar=False`): la pre-liquidación, los avisos y la previsualización de la corrección solo MIRAN y no frenan a nadie.
- Piden el candado los que MARCAN: `_generar_proveedores`, `_generar_transportadores`, `_aplicar_anticipos_pendientes` (Aprobar y Recalcular de un borrador, las dos ramas) y **Corregir una quincena pagada cuando va a incluir algún adelanto** (`_simular_correccion(bloquear_los_sueltos=...)`; la previsualización no).
- **El orden de los candados** (lo encontró la revisión y se midió): Aprobar, Recalcular, Recuadrar, Corregir y editar un anticipo toman primero la liquidación y después los anticipos; Generar tomaba los anticipos y después la liquidación de donde sale la deuda. Con una quincena anterior que ya DEBE y un anticipo suelto, las dos se esperaban entre sí (abrazo mortal 40P01: una salía con el 409). Ahora `_candado_de_las_deudas_primero` toma la deuda antes que los anticipos en Generar, y el escenario "Aprobar una quincena que ya DEBE mientras otro GENERA la siguiente" pasa sin abrazo.

**Cómo se prueba.** `tests/test_anticipos_candado_al_marcar.py` (27 pruebas): la consulta vista en el dialecto de Postgres (`FOR UPDATE` a secas —ni `NOWAIT` ni `SKIP LOCKED`—, orden fijo, **sin JOIN**, con un control que demuestra que sin los `lazyload` sí habría LEFT JOIN, `populate_existing`), quién pide el candado y quién no (cada sitio, las dos ramas, proveedor y transportador, y lo que le LLEGA a la consulta y no solo lo que pide el servicio), el orden deuda → anticipos al generar, la foto vieja que se relee, y que no se barra lo de otra empresa ni lo borrado. SQLite descarta el `FOR UPDATE`: la espera de verdad la prueba la herramienta (`--solo-ahora` corre solo el árbol de trabajo; con HEAD sin el arreglo los dos escenarios de anticipos dan MAL con el $1.000.000).

**Revisión.** Un agente de mutación sobre una copia aislada (34 mutantes, 26 muertos; los 7 sobrevivientes —la rama del TRANSPORTADOR casi sin prueba, `NOWAIT`/`SKIP LOCKED`, el filtro de empresa— quedaron convertidos en pruebas) y dos lentes de solo lectura con escépticos (orden de candados y semántica del dinero): 1 hallazgo confirmado, el orden de Generar, arreglado y medido; 4 refutados por ser deuda anterior. Después, una ronda corta propia sobre el orden de candados y la corrección (9 mutantes: 8 muertos; el que sobrevive es la rama del TRANSPORTADOR de `_anticipos_sueltos_del_periodo`, que no se alcanza porque Corregir una pagada solo existe para leche).

**Lo que NO se tocó (misma clase, deuda anterior; el candado nuevo no lo ensancha):**
- **Nómina de empleados** (`pendientes_empleado`, `PagoEmpleadoService.crear`): lee los adelantos sueltos sin candado y los marca con un UPDATE por llave primaria; dos pagos a la vez al mismo empleado pueden descontar dos veces el mismo adelanto. El arreglo es el mismo (`para_marcar`).
- **Editar o borrar un anticipo suelto** decide con un `liquidacion_id` leído sin candado: si Generar o Aprobar lo marca en medio, puede quedar un anticipo "fantasma" restado sin recuadre. El candado nuevo lo estrecha, no lo cierra.
- **Dos corridas de Generar** sobre periodos que se traslapan o sobre terceros en distinto orden (la barrida por tercero sale de un diccionario armado con una consulta sin `ORDER BY`) pueden abrazarse por las deudas, como ya pasaba antes: 409 que se reintenta.

## Desde y Hasta de vuelta en los listados (09/10/2026, subido el 12/10/2026)

**Lo que pidió el usuario:** "los botones de los filtros están bien, pero también hace falta Desde y Hasta". La vuelta 6 los había quitado (solo quincena o todo); ahora están **además** de los botones de quincena, Estado, Tipo y Ruta, en **Liquidaciones, listado** y en **Recepción, listado**. No hay cambios en el backend.

- **`shared/rango-de-fechas.ts`** (`app-rango-de-fechas`): dos casillas con calendario y una X en cada una (con una sola punta el rango queda abierto: "desde el 5 en adelante"). Comparten los mismos `FormControl` `desde` y `hasta` que el selector de quincena: un botón de quincena llena las casillas, y unas casillas que resultan ser justo una quincena la marcan. Con un rango propio el mes dice "las fechas de al lado son un rango propio" y no "sin filtro".
- **Una sola consulta por cambio de fechas:** las dos fechas esperan **300 ms juntas** (`merge` + `debounceTime`). Escritas a mano llegan a pedazos ("1/10/2" ya es una fecha para el navegador, la del año 2002) y el botón de quincena fija una y luego la otra; sin la espera eran dos consultas, la primera con una fecha vieja. Tipo, Estado y Ruta siguen siendo clic y consulta al instante.
- **Ya no se descartan las fechas guardadas de la sesión que no son una quincena**: se conservan y se ven en las casillas, así que no hay un filtro escondido (era la razón del descarte).
- **Un rango que no sirve NO se consulta** (`problemaDelRango`, en `rango-de-fechas.ts`), y la pantalla lo dice con palabras:
  - **Al revés** ("Desde" posterior a "Hasta"): aviso rojo junto a las casillas y un único mensaje en la tabla.
  - **Texto que no se entiende como fecha** (`31/02/2026`, `hola`): Material deja el control en `null` con el error `matDatepickerParse` y el texto sigue a la vista; **también cuentan como ilegibles una `Date` inválida y un año fuera de 1900–2100**: tecleando `5/10/2026`, al llegar a `5/10/202` el adaptador ya lo da por una fecha buena del año 202, y la consulta salía como `desde=202-10-05`, el servidor la rechazaba con un 422 y la pantalla decía que "la consulta no alcanzó a llegar" en pleno tecleo; sin este caso la pantalla consultaba con ese extremo **abierto** y pintaba la lista y las tarjetas de plata de **todo el historial** bajo una casilla que decía otra cosa. Ahora: no consulta, dice cuál casilla ("No se entiende la fecha «Desde»…"), deja la X visible para borrar el texto con un toque, y en la tabla un único "No se entiende una de las fechas".
  - En los dos casos se vacían las filas, el total y el resumen de antes, se apaga el "cargando" (una consulta vieja en camino ya no cuenta, gracias al turno) y se borra el aviso de "tarjetas que no llegaron" en Liquidaciones.
- **Recepción:** el resumen del período necesita las dos fechas; con una sola punta la lista se pide abierta de ese lado y las tarjetas se esconden.
- **Celular:** una casilla por fila a 560 px o menos (de a dos, en 360 px el texto de la fecha salía cortado).
- **Pendiente, y no es de esta vuelta:** lo escrito **a mano** se lee mes/día/año (el adaptador de fechas del navegador), así que `05/09/2026` sale 9 de mayo mientras la casilla muestra día/mes/año. Otra sesión del mismo usuario (tarea "Leer las fechas escritas a mano como día/mes/año") tiene un `fecha-dia-primero.ts` para eso; **no se integró** porque vive en otro árbol de trabajo y no se sabe en qué estado está. Elegir del calendario no tiene el problema.
- **Con fechas que no sirven no se pintan la tabla vacía ni el paginador "0 de 0"** (igual que cuando falla la consulta): sin consulta no hay conteo. El tooltip del mes dice "una de las fechas de al lado no se entiende" y no "sin filtro". Los botones del calendario llevan `aria-label` en español ("Abrir el calendario de «Desde»"; el resto de las etiquetas internas del calendario de Material siguen en inglés en toda la app, sin `MatDatepickerIntl` propio).
- **El aviso no empuja a la casilla:** en pantallas anchas (≥1440 px) las casillas van a la derecha de la quincena; el aviso, con su texto largo, hacía crecer el bloque de 484 a 864 px, ya no cabía en el renglón y la casilla que se estaba tecleando saltaba de renglón. Medido en un navegador real y arreglado con `width: 0; min-width: 100%` en `.aviso-al-reves`.
- **Revisión, dos rondas.** (1) Cuatro lentes de solo lectura con un escéptico por hallazgo y una ronda de mutación: texto ilegible, 360 px, pruebas flojas. (2) Tres lentes nuevas (comportamiento real del datepicker leyendo las fuentes de Material, máquina de estados y consumidores, UX y accesibilidad) con escépticos, y cuatro agentes de mutación en copias aisladas: 22 mutantes en cada frontend (17 y 12 muertos); sobrevivieron 14, ninguno equivalente, y cada uno tiene ahora su prueba (el orden de `errorCarga`/turnos frente a la salida temprana del rango malo, el respiro de 300 ms que con 150 ms también pasaba, `fechasIlegibles` que con `!== null` mostraba dos mensajes, la X solo donde hay algo que quitar, `role="alert"` en el aviso de «Hasta», etc.). 8 hallazgos confirmados y arreglados (años de 3 o 5 dígitos, tabla y paginador, tooltip, `aria-label`, salto del aviso; el de las fechas escritas mes/día queda como la deuda de abajo) y 6 refutados.

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

Resultado el 12/10/2026 (vuelta 6 + candados + anticipos + Desde/Hasta), sin excluir nada:
- **Backend, suite completa:** **3.072 pasan, 0 fallan**, 22 skipped y 16 xfailed (los mismos de siempre). La advertencia de `DecompressionBombWarning` es de pruebas de soportes y es intencional. Eran 3.004 al subir la vuelta 6 y 2.968 al terminar la vuelta 5.
- **Frontend:** compila limpio y **sin avisos** (el presupuesto de estilos por componente quedó en 12 kB). **949 specs, 0 fallan** (eran 863 al subir la vuelta 6 y 589 al terminar la vuelta 5; los nuevos son los de Desde/Hasta).

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
3. **Cómo se despliega** (la vuelta 6 ya se desplegó así el 07/10/2026; sirve para la próxima, siempre con el visto bueno del usuario y después de hacer commit):
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
