# Ramo — Demo MPS/CRP → MRP con capa IA

Demo **funcional** (no un mockup) del ciclo semanal de planeación desde el MPS: **CRP ↔ MPS → aprobación → MRP con IA →
salida a SAP**, sobre un dataset **100 % sintético** generado con semilla fija. Cada cifra en pantalla sale de un cálculo real
sobre una base de datos, con estado persistente, escenarios ejecutables y trazabilidad.

- **Alcance:** desde MPS en adelante. Demanda/SOP y DRP entran como datos ya calculados.
- **Roles (sin nombres propios):** Planeador de Capacidad, Planeador de Inventarios / MPS, Analista de Planeación, Gerencia.
- **Stack:** Python (FastAPI) + SQLite + React/Vite. Motor de cálculo en Python puro, sin dependencias de IA externas.
- **Guion para presentarlo:** [docs/GUION_DEMO.md](docs/GUION_DEMO.md).

## Levantar el demo
```powershell
./run.ps1            # crea el entorno, genera los datos, compila el front y sirve todo en http://localhost:8000
```
Desarrollo con recarga: `uvicorn backend.app:app --reload` y, en `frontend/`, `npm run dev` (http://localhost:5173).
El botón **Reiniciar demo** (o `POST /api/demo/reiniciar`) regenera los datos y borra el ciclo.

## Tests
```powershell
.venv\Scripts\python -m pytest backend/tests -q      # 80 tests
```
Reproducen las cifras de referencia: Barra Rellena 137 % → 107 % conjunto → 230.000 u de excedente ≈ 8,5 h extra;
Ponqué Individual 3.400.000 → 3.280.000 tras el ajuste; azúcar con 4 días de cobertura y lead time real de 15 días.

## Arquitectura
```
backend/
  synth/     generador reproducible del dataset (semilla 42) y esquema SQLite
  engine/    crp · mps · ciclo (roles, versiones, auditoría) · mrp · ia · propuestas · sap_export
  app.py     API FastAPI (el rol viaja en la cabecera X-Rol; permisos y estados los aplica el motor)
  tests/     pytest
frontend/    React + Vite: Capacidad · Consolidación · Ciclo y plan · MRP + IA · Salida a SAP · Auditoría
docs/        guion de demo
```

### Flujo
1. **Capacidad (CRP):** necesidad por línea en tres flujos (regular, canal directo y exportación) → capacidad = ritmo × horas
   disponibles → saturación. Las líneas que comparten tripulación ceden holgura entre sí; el excedente se traduce en horas extra.
2. **Consolidación (MPS):** ajustes de distribución con motivo y justificación; solo los aprobados entran, y la necesidad final
   vuelve al CRP (ciclo de ida y vuelta, con versiones comparables).
3. **Plan oficial:** Gerencia aprueba aceptando explícitamente las horas extra.
4. **MRP con IA:** SAP explota el plan con lead time fijo, stock de seguridad estático y todas las OC abiertas. La IA lo corrige
   con lead times dinámicos, variabilidad de demanda y de proveedor, detección de anomalías, priorización, cuota reguladora y
   consolidación de pedidos. El Analista aprueba, modifica o rechaza con justificación.
5. **Salida a SAP:** CSV de órdenes provisionales por planta y de compras, cancelaciones de OC y lead times, solo con lo aprobado,
   con huella SHA-256 y auditoría.

## Límites
No se conecta a SAP; no incluye Demanda ni DRP; sin autenticación real (selector de rol); cambios de formato entre SKUs sin
modelar; el formato LSMW es genérico. Detalle y preguntas abiertas en el guion.

## Estado por fases
| Fase | Contenido | Estado |
|---|---|---|
| 0 | Repo y estructura | listo |
| 1 | Dataset sintético + esquema SQLite | listo |
| 2 | Motor CRP + MPS + bucle | listo |
| 3 | API + vistas núcleo | listo |
| 4 | MRP + capa IA | listo |
| 5 | Salida a SAP, auditoría, guion | listo |

> Todos los datos son sintéticos; ninguna cifra corresponde a información real del cliente.
