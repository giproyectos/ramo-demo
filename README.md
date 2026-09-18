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
| 2 | Motor CRP + MPS + bucle | pendiente |
| 3 | API + vistas núcleo | pendiente |
| 4 | MRP + capa IA | pendiente |
| 5 | Exportación SAP, auditoría, guion | pendiente |

> Todos los datos son sintéticos; ninguna cifra corresponde a información real del cliente.

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
