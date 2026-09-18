# Ramo — Demo MPS/CRP → MRP + capa IA

Demo funcional (no mockup) del ciclo semanal de planeación: **CRP ↔ MPS → MRP con capa IA**,
sobre un dataset **100 % sintético** generado con semilla fija.

- Alcance: desde MPS en adelante. Demanda/SOP y DRP entran como datos de entrada.
- Stack: Python (FastAPI) + SQLite + React/Vite.
- Roles (sin nombres propios): Planeador de Capacidad, Planeador de Inventarios/MPS,
  Analista de Planeación, Gerencia, Plantas.

## Estado por fases
| Fase | Contenido | Estado |
|---|---|---|
| 0 | Repo y estructura | listo |
| 1 | Dataset sintético + esquema SQLite | listo (17 tests) |
| 2 | Motor CRP + MPS + bucle | listo (37+1 tests) |
| 3 | API + vistas núcleo | listo (44 tests + recorrido en navegador) |
| 4 | MRP + capa IA | listo (71 tests + recorrido en navegador) |
| 5 | Exportación SAP, auditoría, guion | pendiente |

> Todos los datos son sintéticos; ninguna cifra corresponde a información real del cliente.

## Motor (Fase 2)
- `backend/engine/crp.py`: capacidad por línea, tripulación compartida, horas extra y what-if (funciones puras).
- `backend/engine/mps.py`: ajustes de distribución y consolidación.
- `backend/engine/ciclo.py`: ciclo semanal (capacidad → compartido → decisiones → oficial), roles, versiones y auditoría.
- Regla de tripulación: las líneas del grupo ceden su holgura a las que se pasan; el excedente restante se convierte en horas extra al ritmo de la línea con más holgura.

## API y front (Fase 3)
- `backend/app.py` (FastAPI): el rol va en la cabecera `X-Rol`; permisos y estados los aplica el motor
  (403 sin permiso, 409 estado incorrecto, 400 validación). `/api/simular` es el what-if de solo lectura.
- `frontend/` (React + Vite): selector de rol, Capacidad (CRP + simulador), Consolidación (MPS), Ciclo y plan, Auditoría.
- Sin login: el selector de rol basta para el demo; el servidor igual rechaza acciones no permitidas.

## MRP + capa IA (Fase 4)
- `backend/engine/mrp.py`: explosión del plan a materiales (incluye premezclas), proyección diaria a 28 días y propuesta de
  reposición tipo SAP (lead time fijo, stock de seguridad estático, todas las OC abiertas contadas).
- `backend/engine/ia.py`: refinamiento estadístico y explicable, sin API externa: lead times dinámicos, stock de seguridad
  dinámico (variabilidad de demanda y del proveedor), anomalías (OC/SolPed duplicadas u obsoletas, movimientos repetidos),
  priorización crítico/normal/puede esperar, cuota reguladora entre proveedores y consolidación de pedidos.
- `backend/engine/propuestas.py`: SAP propone → IA refina → el Analista aprueba, modifica o rechaza con justificación.
  Las que rompen la cuota exigen justificación; las calculadas sobre una simulación no se pueden decidir.
- Modos: **plan oficial** (versión aprobada por Gerencia) o **simulación** (última versión o necesidad sin CRP).
- La vista «4 · MRP + IA» permite conmutar la unidad (base, toneladas, costo).

### Levantar el demo
```powershell
./run.ps1            # crea venv, genera datos, compila el front y sirve todo en http://localhost:8000
```
Desarrollo con recarga: `uvicorn backend.app:app --reload` y, en `frontend/`, `npm run dev` (http://localhost:5173).
Botón «Reiniciar demo» (o `POST /api/demo/reiniciar`) regenera los datos y borra el ciclo.

## Uso rápido (Fase 1)
```bash
python -m venv .venv
.venv/Scripts/python -m pip install pytest
python -m backend.synth.generate          # crea ramo.db (semilla 42)
.venv/Scripts/python -m pytest backend/tests -q
```
El dataset es reproducible: misma semilla, mismos datos. `ramo.db` no se versiona.
La semana semilla (semana 1 del horizonte, lunes 2026-09-21) reproduce el escenario de referencia:
Barra Rellena 137 % → 107 % conjunto con Mini Ponqué → 230.000 u de excedente ≈ 8,5 h extra.
