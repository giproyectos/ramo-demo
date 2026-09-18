# Guion de demo — Planeación MPS/CRP → MRP con capa IA

Duración sugerida: 20 minutos. Todos los datos son **sintéticos**; la estructura del cálculo replica el proceso levantado
con los equipos de planeación. Usa el botón **Reiniciar demo** antes de empezar para partir de cero.

## Mensaje central
El cuello de botella de la planeación no es el MRP: es el **MPS/CRP**, que hoy corre en Excel, no existe en SAP y es una
**negociación de ida y vuelta** entre capacidad y consolidación de negocio. Este demo la vuelve un flujo trazable, y a
partir del plan aprobado ejecuta un MRP corregido con datos reales, donde **la IA propone y las personas deciden**.

## Antes de empezar
- Abrir http://localhost:8000 (se levanta con `./run.ps1`).
- Mostrar el banner «Datos 100 % sintéticos» y el selector **Vista como** (cada rol ve y puede hacer cosas distintas;
  el servidor rechaza lo que no le corresponde).

## Acto 1 — Capacidad (rol: Planeador de Capacidad) · 5 min
1. Pestaña **1 · Capacidad**. Pulsa **Iniciar ciclo de la semana**.
2. **Paso 1**: la necesidad llega en tres flujos (regular por reposición, canal directo y exportación, estos dos
   *make-to-order*). Señala el aviso amarillo: el archivo estándar trae datos sucios (canal directo duplicado, exportación
   en cero); el motor usa el origen del pedido.
3. **Paso 2**: capacidad = ritmo × horas disponibles. **Barra Rellena al 137 %**; el resto está entre 78 % y 96 %.
   Menciona la parada de Snack Horneado avisada con pocos días y el ritmo nuevo de Mini Ponqué por automatización.
4. **Pasos 3 y 4** (tarjeta de tripulación compartida): Barra y Mini Ponqué comparten personal. Mini Ponqué cede 290.000 u
   de su holgura; el conjunto queda al **107 %**, con 230.000 u de excedente ≈ **8,5 h extra** (turno extendido).
   *SAP no puede modelar esto porque no sabe qué líneas comparten personal.*
5. **Simulador**: cambia el ritmo de Mini Ponqué a 15.000 y mira cómo se desploma el plan (≈ 144 % conjunto, 71 h, «refuerzo
   externo»). Pulsa **Restablecer**. Marca «Ignorar tripulación compartida» para ver cómo lo vería SAP.
6. **Ejecutar CRP y guardar versión** → v1. **Compartir plan (miércoles)**.

## Acto 2 — Consolidación (rol: Planeador de Inventarios / MPS) · 4 min
1. Cambia el rol. Pestaña **2 · Consolidación**. Explica que el plan de capacidad **todavía no es el MPS final**.
2. Propón un ajuste: *Ponqué Individual Vainilla 40 g*, **−120.000**, motivo «Riesgo de rotación», con justificación.
   Muestra que un ajuste solo propuesto **no cambia ninguna cifra**.
3. Decide el ajuste (aprobar con justificación) y pulsa **Consolidar y devolver al CRP**.
4. Resultado: Ponqué Individual 3.400.000 → **3.280.000 (96 % → 93 %)**; Barra sigue al 137 %. *El ciclo se cierra volviendo
   al CRP: no es un pipeline lineal.*

## Acto 3 — Aprobación (rol: Gerencia) · 2 min
1. Pestaña **3 · Ciclo y plan**. Muestra la línea de tiempo mié / jue / vie y el conmutador de unidades
   (unidades, cajas, toneladas, costo).
2. Intenta aprobar: el botón pide **aceptar las 8,5 h extra** y una justificación. Aprueba.
3. Versiones: v1 CRP → v2 MPS → v3 plan oficial; compara v1 y v2 (solo cambia Ponqué Individual).

## Acto 4 — MRP con capa IA (rol: Analista de Planeación) · 6 min
1. Pestaña **4 · MRP + IA**. KPIs: SAP proyecta 13 quiebres; la IA, 15. **Dos quiebres que SAP no ve.**
2. **Tablero de riesgo**: azúcar refinada en rojo, 4 días de cobertura. Pulsa la fila.
3. **Explosión**: de qué productos sale el consumo (incluye premezclas internas) y el gráfico de inventario a 28 días.
4. **Propuestas SAP frente a IA** (pulsa *Generar propuestas*). En azúcar:
   - lead time real de Proveedor 07 = **15 días** frente a 6 en el maestro (25 entregas);
   - su OC prometida para el día 6 llegaría el día 14; el quiebre es el día 4;
   - la IA propone comprar todo a Proveedor 02 y **romper la cuota 60/40**: por eso exige justificación y queda trazado.
5. Aprueba azúcar con justificación; modifica otra orden (cantidad); rechaza una con justificación.
6. Muestra **Anomalías** (OC duplicadas y obsoletas que SAP cuenta como recibos, SolPed duplicadas o fuera de rango…) y la
   tabla de **lead times dinámicos** (lo que habría que resincronizar en datos maestros).
7. Conmuta la unidad a **toneladas** y **costo**.

## Acto 5 — Salida a SAP · 2 min
1. Rol Planeador de Capacidad → pestaña **5 · Salida a SAP** → **Generar archivos por planta**: un CSV de órdenes
   provisionales por planta. *Esto ataca la carga automática que se rompió con la migración de SAP y hoy es manual.*
2. Rol Analista → **Generar archivos de compras**: solo lo aprobado o modificado; lo rechazado no sale. Abre un archivo
   con **Ver** y muestra la huella SHA-256.
3. Pestaña **Auditoría**: quién, qué y por qué; filtra por rol; aparecen también los intentos denegados. Descarga el CSV.

## Preguntas que el demo deja abiertas para validar con el cliente
- **Regla de tripulación compartida**: el demo suma las capacidades del grupo y convierte el excedente en horas al ritmo de
  la línea con más holgura. Validar con quien opera el CRP.
- **Cambios de formato entre SKUs** y secuencia de producción: no se modelan.
- **Nivel de servicio (90 %) y días de cobertura por ciclo (14)** de la IA: son parámetros de negocio.
- **Formato real del archivo LSMW** por planta y por tipo de orden: el del demo es genérico.
- **Recarga de lead times al maestro de SAP**: se genera la lista; el mecanismo de carga está por definir.
- **Regla de escasez** (reparto proporcional al forecast) y **cuota reguladora**: requieren alineación comercial.

## Qué NO es este demo
No se conecta a SAP ni a las fuentes reales, no incluye Demanda/SOP ni DRP (los construye otro proveedor y aquí entran como
dato ya calculado), no tiene autenticación real (el rol se elige en pantalla) y sus cifras no corresponden a la operación.
