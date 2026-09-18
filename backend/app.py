"""API del demo. El rol llega en la cabecera X-Rol; los permisos y estados los aplica el motor."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.engine import ciclo as cy
from backend.engine import datos, ia, mps, mrp, propuestas, sap_export
from backend.engine.crp import Escenario
from backend.synth.generate import generar

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"

app = FastAPI(title="Ramo — Demo MPS/CRP", version="0.3")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def db_path() -> Path:
    return Path(os.environ.get("RAMO_DB", datos.DB_POR_DEFECTO))


def get_con():
    con = datos.conectar(db_path())
    try:
        yield con
    finally:
        con.close()


def get_rol(x_rol: str = Header(default="")) -> str:
    if x_rol not in cy.ROLES:
        raise HTTPException(400, f"Cabecera X-Rol inválida. Opciones: {', '.join(cy.ROLES)}")
    return x_rol


@app.exception_handler(cy.PermisoError)
async def _permiso(_, e):
    return JSONResponse({"detail": str(e), "tipo": "permiso"}, status_code=403)


@app.exception_handler(cy.EstadoError)
async def _estado(_, e):
    return JSONResponse({"detail": str(e), "tipo": "estado"}, status_code=409)


@app.exception_handler(cy.CicloError)
async def _ciclo(_, e):
    return JSONResponse({"detail": str(e), "tipo": "validacion"}, status_code=400)


@app.exception_handler(mrp.MrpError)
async def _mrp(_, e):
    return JSONResponse({"detail": str(e), "tipo": "estado"}, status_code=409)


@app.exception_handler(ValueError)
async def _valor(_, e):
    return JSONResponse({"detail": str(e), "tipo": "validacion"}, status_code=400)


# ───────────────────────── Modelos ─────────────────────────
class EscenarioIn(BaseModel):
    ritmos: dict[int, float] = Field(default_factory=dict)
    horas_brutas: dict[int, float] = Field(default_factory=dict)
    horas_evento_extra: dict[int, float] = Field(default_factory=dict)
    sin_tripulacion_compartida: bool = False

    def a_motor(self) -> Escenario:
        return Escenario(self.ritmos, self.horas_brutas, self.horas_evento_extra, self.sin_tripulacion_compartida)


class SimularIn(BaseModel):
    semana_id: int | None = None
    escenario: EscenarioIn = Field(default_factory=EscenarioIn)
    ciclo_id: int | None = None          # si viene, se simula además con los ajustes ya aprobados de ese ciclo


class CrpIn(BaseModel):
    escenario: EscenarioIn | None = None
    nota: str | None = None


class AjusteIn(BaseModel):
    producto_id: int
    delta: float
    motivo: str
    justificacion: str


class DecisionIn(BaseModel):
    aprobar: bool
    justificacion: str


class AprobarIn(BaseModel):
    justificacion: str
    acepta_horas_extra: bool = False


class NotaIn(BaseModel):
    nota: str | None = None


class GenerarIn(BaseModel):
    modo: str = "oficial"


class DecisionPropuestaIn(BaseModel):
    decision: str
    justificacion: str = ""
    cantidad: float | None = None
    proveedor_id: int | None = None


# ───────────────────────── Meta ─────────────────────────
@app.get("/api/meta")
def meta(con=Depends(get_con)):
    sem = datos.semana_semilla(con)
    fila = con.execute("SELECT * FROM semana WHERE id=?", (sem,)).fetchone()
    return {
        "roles": [{"id": k, "nombre": v} for k, v in cy.ROLES.items()],
        "permisos": {a: sorted(r) for a, r in cy.PERMISOS.items()},
        "motivos": list(mps.MOTIVOS),
        "semana": dict(fila),
        "datos_sinteticos": True,
    }


@app.post("/api/demo/reiniciar")
def reiniciar(rol: str = Depends(get_rol)):
    """Regenera el dataset sintético desde la semilla (deja la demo en el punto de partida)."""
    generar(db_path(), seed=42)
    return {"ok": True}


# ───────────────────────── Necesidad y lectura ─────────────────────────
@app.get("/api/necesidad")
def necesidad(semana_id: int | None = None, con=Depends(get_con)):
    semana_id = semana_id or datos.semana_semilla(con)
    return {"productos": datos.cargar_necesidad_productos(con, semana_id),
            "diagnostico_fuentes": datos.diagnostico_fuentes(con, semana_id)}


@app.get("/api/lineas")
def lineas(semana_id: int | None = None, con=Depends(get_con)):
    semana_id = semana_id or datos.semana_semilla(con)
    ls, grupos = datos.cargar_lineas(con, semana_id)
    return {"lineas": [{"id": l.id, "codigo": l.codigo, "nombre": l.nombre, "unidad": l.unidad, "ritmo": l.ritmo,
                        "horas_brutas": l.horas_brutas, "horas_evento": l.horas_evento, "grupo_id": l.grupo_id}
                       for l in ls], "grupos": grupos}


@app.post("/api/simular")
def simular(body: SimularIn, con=Depends(get_con)):
    semana_id = body.semana_id or datos.semana_semilla(con)
    ajustes = cy._ajustes_aprobados(con, body.ciclo_id) if body.ciclo_id else []
    return cy.simular(con, semana_id, body.escenario.a_motor(), ajustes)


# ───────────────────────── Ciclo ─────────────────────────
def _ultimo(con, ciclo_id: int) -> dict | None:
    fila = cy._ultima_version(con, ciclo_id)
    return cy.obtener_version(con, ciclo_id, fila["numero"]) if fila else None


@app.get("/api/ciclo/actual")
def ciclo_actual(con=Depends(get_con)):
    sem = datos.semana_semilla(con)
    fila = con.execute("SELECT id FROM ciclo WHERE semana_id=?", (sem,)).fetchone()
    return {"ciclo_id": fila["id"] if fila else None, "semana_id": sem}


@app.post("/api/ciclo")
def iniciar(con=Depends(get_con), rol: str = Depends(get_rol)):
    return {"ciclo_id": cy.iniciar_ciclo(con, datos.semana_semilla(con), rol)}


@app.get("/api/ciclo/{ciclo_id}")
def estado(ciclo_id: int, con=Depends(get_con)):
    e = cy.estado_ciclo(con, ciclo_id)
    e["ultima_version"] = _ultimo(con, ciclo_id)
    ap = {a["id"] for a in cy._ajustes_aprobados(con, ciclo_id)}
    ult = e["ultima_version"]
    e["desactualizada"] = bool(ult) and set(ult["detalle"]["ajustes_aplicados"]) != ap
    e["roles"] = cy.ROLES
    return e


@app.post("/api/ciclo/{ciclo_id}/crp")
def ejecutar_crp(ciclo_id: int, body: CrpIn, con=Depends(get_con), rol: str = Depends(get_rol)):
    esc = body.escenario.a_motor() if body.escenario else None
    return cy.ejecutar_crp(con, ciclo_id, rol, esc, body.nota)


@app.post("/api/ciclo/{ciclo_id}/compartir")
def compartir(ciclo_id: int, con=Depends(get_con), rol: str = Depends(get_rol)):
    cy.compartir_plan(con, ciclo_id, rol)
    return {"ok": True}


@app.post("/api/ciclo/{ciclo_id}/abrir-decisiones")
def abrir(ciclo_id: int, con=Depends(get_con), rol: str = Depends(get_rol)):
    cy.abrir_decisiones(con, ciclo_id, rol)
    return {"ok": True}


@app.post("/api/ciclo/{ciclo_id}/ajustes")
def proponer(ciclo_id: int, body: AjusteIn, con=Depends(get_con), rol: str = Depends(get_rol)):
    return {"ajuste_id": cy.proponer_ajuste(con, ciclo_id, rol, body.producto_id, body.delta, body.motivo, body.justificacion)}


@app.post("/api/ciclo/{ciclo_id}/ajustes/{ajuste_id}/decision")
def decidir(ciclo_id: int, ajuste_id: int, body: DecisionIn, con=Depends(get_con), rol: str = Depends(get_rol)):
    cy.decidir_ajuste(con, ciclo_id, rol, ajuste_id, body.aprobar, body.justificacion)
    return {"ok": True}


@app.post("/api/ciclo/{ciclo_id}/consolidar")
def consolidar(ciclo_id: int, body: NotaIn | None = None, con=Depends(get_con), rol: str = Depends(get_rol)):
    return cy.consolidar(con, ciclo_id, rol, body.nota if body else None)


@app.post("/api/ciclo/{ciclo_id}/aprobar")
def aprobar(ciclo_id: int, body: AprobarIn, con=Depends(get_con), rol: str = Depends(get_rol)):
    return cy.aprobar_plan(con, ciclo_id, rol, body.justificacion, body.acepta_horas_extra)


@app.get("/api/ciclo/{ciclo_id}/versiones/{numero}")
def version(ciclo_id: int, numero: int, con=Depends(get_con)):
    return cy.obtener_version(con, ciclo_id, numero)


@app.get("/api/ciclo/{ciclo_id}/comparar")
def comparar(ciclo_id: int, a: int = Query(...), b: int = Query(...), con=Depends(get_con)):
    return cy.comparar_versiones(con, ciclo_id, a, b)


@app.get("/api/auditoria")
def auditoria(con=Depends(get_con)):
    return [dict(r) for r in con.execute("SELECT * FROM auditoria ORDER BY id DESC")]


@app.get("/api/auditoria.csv")
def auditoria_csv(con=Depends(get_con)):
    filas = [dict(r) for r in con.execute("SELECT * FROM auditoria ORDER BY id")]
    cuerpo = sap_export._csv(["ID", "FECHA", "ROL", "ACCION", "ENTIDAD", "DETALLE"],
                             [[f["id"], f["ts"], f["rol"], f["accion"], f["entidad"], f["detalle"]] for f in filas])
    return Response(cuerpo.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="auditoria.csv"'})


# ───────────────────────── MRP y capa IA ─────────────────────────
@app.get("/api/mrp")
def mrp_run(modo: str = "oficial", con=Depends(get_con)):
    return ia.correr(con, modo)


@app.post("/api/ciclo/{ciclo_id}/ia/generar")
def ia_generar(ciclo_id: int, body: GenerarIn, con=Depends(get_con), rol: str = Depends(get_rol)):
    return propuestas.generar_propuestas(con, ciclo_id, rol, body.modo)


@app.get("/api/ciclo/{ciclo_id}/ia/propuestas")
def ia_propuestas(ciclo_id: int, con=Depends(get_con)):
    return propuestas.listar_propuestas(con, ciclo_id)


@app.post("/api/ciclo/{ciclo_id}/ia/propuestas/{propuesta_id}/decision")
def ia_decidir(ciclo_id: int, propuesta_id: int, body: DecisionPropuestaIn, con=Depends(get_con), rol: str = Depends(get_rol)):
    cambios = {k: v for k, v in (("cantidad", body.cantidad), ("proveedor_id", body.proveedor_id)) if v is not None}
    return propuestas.decidir_propuesta(con, ciclo_id, rol, propuesta_id, body.decision, body.justificacion, cambios)


# ───────────────────────── Salida a SAP ─────────────────────────
@app.get("/api/ciclo/{ciclo_id}/salida")
def salida(ciclo_id: int, con=Depends(get_con)):
    return {**sap_export.estado_salida(con, ciclo_id), "archivos": sap_export.listar(con, ciclo_id)}


@app.post("/api/ciclo/{ciclo_id}/exportar/ordenes")
def exportar_ordenes(ciclo_id: int, con=Depends(get_con), rol: str = Depends(get_rol)):
    return sap_export.exportar_ordenes(con, ciclo_id, rol)


@app.post("/api/ciclo/{ciclo_id}/exportar/compras")
def exportar_compras(ciclo_id: int, con=Depends(get_con), rol: str = Depends(get_rol)):
    return sap_export.exportar_compras(con, ciclo_id, rol)


@app.get("/api/exportaciones/{export_id}/vista")
def vista_archivo(export_id: int, con=Depends(get_con)):
    a = sap_export.obtener(con, export_id)
    return {"archivo": a["archivo"], "tipo": a["tipo"], "filas": a["filas"], "sha256": a["sha256"],
            "lineas": a["contenido"].splitlines()[:12]}


@app.get("/api/exportaciones/{export_id}/descargar")
def descargar_archivo(export_id: int, con=Depends(get_con)):
    a = sap_export.obtener(con, export_id)
    return Response(a["contenido"].encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{a["archivo"]}"'})


@app.get("/api/ciclo/{ciclo_id}/exportaciones/lote/{lote}.zip")
def descargar_lote(ciclo_id: int, lote: int, con=Depends(get_con)):
    return Response(sap_export.zip_del_lote(con, ciclo_id, lote), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="lote_{lote}.zip"'})


# ───────────────────────── Front compilado (opcional) ─────────────────────────
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{ruta:path}")
    def spa(ruta: str):
        f = DIST / ruta
        return FileResponse(f if ruta and f.is_file() else DIST / "index.html")
