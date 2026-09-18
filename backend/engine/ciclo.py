"""Ciclo semanal CRP <-> MPS: estados, roles, versiones del plan y auditoría.

Ritmo semanal: capacidad (lun-mar) -> compartido (mié) -> decisiones (jue) -> oficial (vie).
El plan no es lineal: entre `decisiones` se puede volver a correr el CRP y reconsolidar todas las
veces que haga falta; cada corrida deja una versión comparable.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import crp as crp_mod
from . import datos, mps
from .crp import Escenario

# Roles (sin nombres propios)
CAPACIDAD, INVENTARIOS, ANALISTA, GERENCIA = (
    "planeador_capacidad", "planeador_inventarios", "analista_planeacion", "gerencia")
ROLES = {
    CAPACIDAD: "Planeador de Capacidad",
    INVENTARIOS: "Planeador de Inventarios / MPS",
    ANALISTA: "Analista de Planeación",
    GERENCIA: "Gerencia",
}
PERMISOS = {
    "iniciar_ciclo": {CAPACIDAD},
    "ejecutar_crp": {CAPACIDAD},
    "compartir_plan": {CAPACIDAD},
    "proponer_ajuste": {INVENTARIOS},
    "decidir_ajuste": {INVENTARIOS, GERENCIA},
    "consolidar": {INVENTARIOS},
    "abrir_decisiones": {INVENTARIOS, GERENCIA},
    "aprobar_plan": {GERENCIA},
    "generar_propuestas": {ANALISTA},
    "decidir_propuesta": {ANALISTA},
    "exportar_ordenes": {CAPACIDAD},
    "exportar_compras": {ANALISTA},
}
ESTADOS_PERMITIDOS = {
    "ejecutar_crp": {"capacidad", "compartido", "decisiones"},
    "compartir_plan": {"capacidad"},
    "proponer_ajuste": {"compartido", "decisiones"},
    "decidir_ajuste": {"decisiones"},
    "consolidar": {"decisiones"},
    "abrir_decisiones": {"compartido"},
    "aprobar_plan": {"decisiones"},
}


class CicloError(Exception):
    pass


class PermisoError(CicloError):
    pass


class EstadoError(CicloError):
    pass


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def auditar(con: sqlite3.Connection, rol: str, accion: str, entidad: str, detalle: dict | str | None = None) -> None:
    if isinstance(detalle, dict):
        detalle = json.dumps(detalle, ensure_ascii=False)
    con.execute("INSERT INTO auditoria(ts, rol, accion, entidad, detalle) VALUES (?,?,?,?,?)",
                (_ahora(), rol, accion, entidad, detalle))


def _ciclo(con: sqlite3.Connection, ciclo_id: int) -> sqlite3.Row:
    c = con.execute("SELECT * FROM ciclo WHERE id=?", (ciclo_id,)).fetchone()
    if c is None:
        raise CicloError(f"No existe el ciclo {ciclo_id}")
    return c


def _requerir(con: sqlite3.Connection, ciclo: sqlite3.Row | None, accion: str, rol: str) -> None:
    if rol not in PERMISOS[accion]:
        auditar(con, rol, f"{accion}:denegado", f"ciclo:{ciclo['id'] if ciclo else '-'}", "rol sin permiso")
        con.commit()
        nombres = " / ".join(ROLES[r] for r in sorted(PERMISOS[accion]))
        raise PermisoError(f"El rol {ROLES.get(rol, rol)} no puede ejecutar '{accion}' (permitido: {nombres})")
    if ciclo is not None and accion in ESTADOS_PERMITIDOS and ciclo["estado"] not in ESTADOS_PERMITIDOS[accion]:
        raise EstadoError(f"'{accion}' no está permitido con el ciclo en estado '{ciclo['estado']}'")


# ───────────────────────── Cálculo ─────────────────────────
def _ajustes_aprobados(con: sqlite3.Connection, ciclo_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT id, producto_id, delta FROM ajuste_distribucion WHERE ciclo_id=? AND estado='aprobado' ORDER BY id",
        (ciclo_id,))]


def calcular(con: sqlite3.Connection, semana_id: int, escenario: Escenario | None = None,
             ajustes: list[dict] | None = None) -> dict:
    """Cálculo puro (no escribe): necesidad -> ajustes -> capacidad -> CRP."""
    lineas, grupos = datos.cargar_lineas(con, semana_id)
    lineas = crp_mod.aplicar_escenario(lineas, escenario)
    productos = mps.aplicar_ajustes(datos.cargar_necesidad_productos(con, semana_id), ajustes or [])
    resultado = crp_mod.calcular_crp(lineas, mps.necesidad_por_linea(productos), grupos)
    return {"crp": resultado, "productos": productos}


def simular(con: sqlite3.Connection, semana_id: int, escenario: Escenario | None = None,
            ajustes: list[dict] | None = None) -> dict:
    """What-if de solo lectura, sin ciclo ni versiones."""
    return calcular(con, semana_id, escenario, ajustes)


def _guardar_version(con, ciclo_id, origen, rol, nota, escenario, calculo, ajustes_ids) -> dict:
    numero = con.execute("SELECT COALESCE(MAX(numero),0)+1 FROM plan_version WHERE ciclo_id=?", (ciclo_id,)).fetchone()[0]
    detalle = {"escenario": escenario.to_dict(), "ajustes_aplicados": ajustes_ids, "crp": calculo["crp"]}
    cur = con.execute(
        "INSERT INTO plan_version(ciclo_id, numero, origen, rol, nota, detalle, creado_en) VALUES (?,?,?,?,?,?,?)",
        (ciclo_id, numero, origen, rol, nota, json.dumps(detalle, ensure_ascii=False), _ahora()))
    vid = cur.lastrowid
    for r in calculo["crp"]["lineas"]:
        con.execute("INSERT INTO plan_version_linea VALUES (?,?,?,?,?,?)",
                    (vid, r["linea_id"], r["necesidad"], r["capacidad"], r["saturacion"], r["horas_extra"]))
    for p in calculo["productos"]:
        con.execute("INSERT INTO plan_version_producto VALUES (?,?,?)", (vid, p["producto_id"], p["total_final"]))
    return {"id": vid, "numero": numero, "origen": origen}


def _ultima_version(con, ciclo_id) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM plan_version WHERE ciclo_id=? ORDER BY numero DESC LIMIT 1", (ciclo_id,)).fetchone()


def _escenario_vigente(con, ciclo_id, escenario: Escenario | None) -> Escenario:
    if escenario is not None:
        return escenario
    v = _ultima_version(con, ciclo_id)
    return Escenario.from_dict(json.loads(v["detalle"])["escenario"]) if v else Escenario()


# ───────────────────────── Operaciones del ciclo ─────────────────────────
def iniciar_ciclo(con: sqlite3.Connection, semana_id: int, rol: str = CAPACIDAD) -> int:
    _requerir(con, None, "iniciar_ciclo", rol)
    if con.execute("SELECT 1 FROM ciclo WHERE semana_id=?", (semana_id,)).fetchone():
        raise EstadoError("Ya existe un ciclo para esa semana")
    cur = con.execute("INSERT INTO ciclo(semana_id, estado, creado_en) VALUES (?,?,?)",
                      (semana_id, "capacidad", _ahora()))
    auditar(con, rol, "iniciar_ciclo", f"ciclo:{cur.lastrowid}", {"semana_id": semana_id})
    con.commit()
    return cur.lastrowid


def ejecutar_crp(con: sqlite3.Connection, ciclo_id: int, rol: str, escenario: Escenario | None = None,
                 nota: str | None = None) -> dict:
    """Corre el CRP (aplicando los ajustes ya aprobados) y guarda una nueva versión."""
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "ejecutar_crp", rol)
    esc = _escenario_vigente(con, ciclo_id, escenario)
    ajustes = _ajustes_aprobados(con, ciclo_id)
    calculo = calcular(con, ciclo["semana_id"], esc, ajustes)
    v = _guardar_version(con, ciclo_id, "crp", rol, nota, esc, calculo, [a["id"] for a in ajustes])
    auditar(con, rol, "ejecutar_crp", f"plan_version:{v['id']}",
            {"numero": v["numero"], "horas_extra_total": round(calculo["crp"]["horas_extra_total"], 2),
             "escenario": esc.to_dict()})
    con.commit()
    return {**v, **calculo}


def compartir_plan(con: sqlite3.Connection, ciclo_id: int, rol: str) -> None:
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "compartir_plan", rol)
    if _ultima_version(con, ciclo_id) is None:
        raise EstadoError("Primero hay que ejecutar el CRP")
    con.execute("UPDATE ciclo SET estado='compartido' WHERE id=?", (ciclo_id,))
    auditar(con, rol, "compartir_plan", f"ciclo:{ciclo_id}", "Plan de capacidad compartido con inventarios/MPS")
    con.commit()


def abrir_decisiones(con: sqlite3.Connection, ciclo_id: int, rol: str) -> None:
    """Jueves de decisiones. También se abre solo al proponer el primer ajuste; sirve cuando no hay ajustes."""
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "abrir_decisiones", rol)
    con.execute("UPDATE ciclo SET estado='decisiones' WHERE id=?", (ciclo_id,))
    auditar(con, rol, "abrir_decisiones", f"ciclo:{ciclo_id}", "Reunión de decisiones abierta")
    con.commit()


def proponer_ajuste(con: sqlite3.Connection, ciclo_id: int, rol: str, producto_id: int, delta: float,
                    motivo: str, justificacion: str) -> int:
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "proponer_ajuste", rol)
    if motivo not in mps.MOTIVOS:
        raise CicloError(f"Motivo inválido: {motivo}")
    if not justificacion or not justificacion.strip():
        raise CicloError("El ajuste requiere justificación")
    if delta == 0:
        raise CicloError("El ajuste no puede ser cero")
    actual = {p["producto_id"]: p for p in datos.cargar_necesidad_productos(con, ciclo["semana_id"])}
    if producto_id not in actual:
        raise CicloError("Producto inexistente")
    pendiente = con.execute("SELECT COALESCE(SUM(delta),0) FROM ajuste_distribucion "
                            "WHERE ciclo_id=? AND producto_id=? AND estado!='rechazado'", (ciclo_id, producto_id)).fetchone()[0]
    if actual[producto_id]["total"] + pendiente + delta < 0:
        raise CicloError("El ajuste dejaría la necesidad del producto en negativo")
    cur = con.execute(
        "INSERT INTO ajuste_distribucion(ciclo_id, producto_id, delta, motivo, justificacion, rol, estado, creado_en)"
        " VALUES (?,?,?,?,?,?, 'propuesto', ?)", (ciclo_id, producto_id, delta, motivo, justificacion.strip(), rol, _ahora()))
    if ciclo["estado"] == "compartido":
        con.execute("UPDATE ciclo SET estado='decisiones' WHERE id=?", (ciclo_id,))
    auditar(con, rol, "proponer_ajuste", f"ajuste:{cur.lastrowid}",
            {"producto_id": producto_id, "delta": delta, "motivo": motivo, "justificacion": justificacion.strip()})
    con.commit()
    return cur.lastrowid


def decidir_ajuste(con: sqlite3.Connection, ciclo_id: int, rol: str, ajuste_id: int, aprobar: bool,
                   justificacion: str) -> None:
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "decidir_ajuste", rol)
    a = con.execute("SELECT * FROM ajuste_distribucion WHERE id=? AND ciclo_id=?", (ajuste_id, ciclo_id)).fetchone()
    if a is None:
        raise CicloError("Ajuste inexistente en este ciclo")
    if a["estado"] != "propuesto":
        raise EstadoError(f"El ajuste ya fue {a['estado']}")
    if not justificacion or not justificacion.strip():
        raise CicloError("La decisión requiere justificación")
    estado = "aprobado" if aprobar else "rechazado"
    con.execute("UPDATE ajuste_distribucion SET estado=?, decidido_rol=?, decidido_en=?, decision_justificacion=? WHERE id=?",
                (estado, rol, _ahora(), justificacion.strip(), ajuste_id))
    auditar(con, rol, f"ajuste_{estado}", f"ajuste:{ajuste_id}", justificacion.strip())
    con.commit()


def consolidar(con: sqlite3.Connection, ciclo_id: int, rol: str, nota: str | None = None) -> dict:
    """Aplica los ajustes aprobados y devuelve la necesidad final al CRP (segunda validación)."""
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "consolidar", rol)
    if _ultima_version(con, ciclo_id) is None:
        raise EstadoError("Primero el Planeador de Capacidad debe ejecutar el CRP")
    esc = _escenario_vigente(con, ciclo_id, None)
    ajustes = _ajustes_aprobados(con, ciclo_id)
    base = calcular(con, ciclo["semana_id"], esc, [])
    final = calcular(con, ciclo["semana_id"], esc, ajustes)
    v = _guardar_version(con, ciclo_id, "mps", rol, nota, esc, final, [a["id"] for a in ajustes])
    resumen = mps.resumen_consolidacion(base["crp"], final["crp"], final["productos"])
    auditar(con, rol, "consolidar", f"plan_version:{v['id']}",
            {"numero": v["numero"], "ajustes": [a["id"] for a in ajustes],
             "horas_extra_total": round(final["crp"]["horas_extra_total"], 2)})
    con.commit()
    return {**v, **final, "resumen_consolidacion": resumen}


def aprobar_plan(con: sqlite3.Connection, ciclo_id: int, rol: str, justificacion: str,
                 acepta_horas_extra: bool = False) -> dict:
    ciclo = _ciclo(con, ciclo_id)
    _requerir(con, ciclo, "aprobar_plan", rol)
    ultima = _ultima_version(con, ciclo_id)
    if ultima is None:
        raise EstadoError("No hay versión del plan para aprobar")
    if con.execute("SELECT 1 FROM ajuste_distribucion WHERE ciclo_id=? AND estado='propuesto'", (ciclo_id,)).fetchone():
        raise EstadoError("Hay ajustes propuestos sin decidir")
    detalle = json.loads(ultima["detalle"])
    aprobados = sorted(a["id"] for a in _ajustes_aprobados(con, ciclo_id))
    if sorted(detalle["ajustes_aplicados"]) != aprobados:
        raise EstadoError("La última versión no incluye todos los ajustes aprobados: hay que reconsolidar")
    horas = detalle["crp"]["horas_extra_total"]
    if horas > 1e-9 and not acepta_horas_extra:
        raise EstadoError(f"El plan requiere {horas:.1f} horas extra sin validar; hay que aceptarlas explícitamente")
    if not justificacion or not justificacion.strip():
        raise CicloError("La aprobación requiere justificación")
    esc = Escenario.from_dict(detalle["escenario"])
    calculo = calcular(con, ciclo["semana_id"], esc, _ajustes_aprobados(con, ciclo_id))
    v = _guardar_version(con, ciclo_id, "final", rol, justificacion.strip(), esc, calculo, aprobados)
    con.execute("UPDATE ciclo SET estado='oficial' WHERE id=?", (ciclo_id,))
    auditar(con, rol, "aprobar_plan", f"plan_version:{v['id']}",
            {"numero": v["numero"], "horas_extra_aceptadas": round(horas, 2), "justificacion": justificacion.strip()})
    con.commit()
    return {**v, **calculo}


# ───────────────────────── Consultas ─────────────────────────
def obtener_version(con: sqlite3.Connection, ciclo_id: int, numero: int) -> dict:
    v = con.execute("SELECT * FROM plan_version WHERE ciclo_id=? AND numero=?", (ciclo_id, numero)).fetchone()
    if v is None:
        raise CicloError("Versión inexistente")
    d = dict(v)
    d["detalle"] = json.loads(d["detalle"])
    d["productos"] = [dict(r) for r in con.execute(
        "SELECT producto_id, cantidad FROM plan_version_producto WHERE plan_version_id=? ORDER BY producto_id", (v["id"],))]
    return d


def comparar_versiones(con: sqlite3.Connection, ciclo_id: int, a: int, b: int) -> list[dict]:
    va, vb = obtener_version(con, ciclo_id, a), obtener_version(con, ciclo_id, b)
    la = {r["linea_id"]: r for r in va["detalle"]["crp"]["lineas"]}
    out = []
    for r in vb["detalle"]["crp"]["lineas"]:
        x = la[r["linea_id"]]
        out.append({"linea_id": r["linea_id"], "nombre": r["nombre"],
                    "necesidad": (x["necesidad"], r["necesidad"]),
                    "saturacion": (x["saturacion"], r["saturacion"]),
                    "horas_extra": (x["horas_extra"], r["horas_extra"]),
                    "cambio": abs(x["necesidad"] - r["necesidad"]) > 1e-9 or abs(x["horas_extra"] - r["horas_extra"]) > 1e-9})
    return out


def estado_ciclo(con: sqlite3.Connection, ciclo_id: int) -> dict:
    c = _ciclo(con, ciclo_id)
    return {
        "ciclo": dict(c),
        "versiones": [dict(r) for r in con.execute(
            "SELECT id, numero, origen, rol, nota, creado_en FROM plan_version WHERE ciclo_id=? ORDER BY numero", (ciclo_id,))],
        "ajustes": [dict(r) for r in con.execute("SELECT * FROM ajuste_distribucion WHERE ciclo_id=? ORDER BY id", (ciclo_id,))],
        "auditoria": [dict(r) for r in con.execute("SELECT * FROM auditoria ORDER BY id")],
    }
