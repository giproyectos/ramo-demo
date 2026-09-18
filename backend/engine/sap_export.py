"""Salida hacia SAP: archivos planos (CSV con ';', estilo LSMW) solo con lo aprobado.

- Órdenes provisionales por planta, desde el plan oficial. Hoy la carga automática está rota y cada planta las carga
  a mano por LSMW: este archivo es exactamente lo que cada planta necesita.
- Compras (reposiciones aprobadas o modificadas), cancelaciones de OC y lead times a resincronizar en datos maestros.

No se conecta a SAP: genera los archivos, guarda su contenido exacto con una huella y deja todo en la auditoría.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import date, timedelta

from . import ciclo as cy

SEP = ";"


def _celda(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v).replace(SEP, ",").replace("\r", " ").replace("\n", " ")


def _csv(cabecera: list[str], filas: list[list]) -> str:
    return "\n".join([SEP.join(cabecera)] + [SEP.join(_celda(c) for c in f) for f in filas]) + "\n"


def _guardar(con, ciclo_id: int, vid: int, lote: int, tipo: str, archivo: str, contenido: str, filas: int, rol: str) -> int:
    cur = con.execute(
        """INSERT INTO exportacion_sap(ciclo_id, plan_version_id, lote, tipo, archivo, filas, sha256, contenido, rol, creado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ciclo_id, vid, lote, tipo, archivo, filas, hashlib.sha256(contenido.encode("utf-8")).hexdigest(), contenido, rol, cy._ahora()))
    return cur.lastrowid


def _siguiente_lote(con, ciclo_id: int) -> int:
    return con.execute("SELECT COALESCE(MAX(lote), 0) + 1 FROM exportacion_sap WHERE ciclo_id=?", (ciclo_id,)).fetchone()[0]


def _plan_oficial(con, ciclo_id: int) -> sqlite3.Row:
    v = con.execute("SELECT * FROM plan_version WHERE ciclo_id=? AND origen='final' ORDER BY numero DESC LIMIT 1", (ciclo_id,)).fetchone()
    if v is None:
        raise cy.EstadoError("Solo se exporta un plan oficial aprobado")
    return v


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", s)


# ───────────────────────── Órdenes provisionales ─────────────────────────
def exportar_ordenes(con: sqlite3.Connection, ciclo_id: int, rol: str) -> dict:
    ciclo = cy._ciclo(con, ciclo_id)
    cy._requerir(con, ciclo, "exportar_ordenes", rol)
    ver = _plan_oficial(con, ciclo_id)
    if con.execute("SELECT 1 FROM exportacion_sap WHERE plan_version_id=? AND tipo='ordenes_provisionales'", (ver["id"],)).fetchone():
        raise cy.EstadoError("Las órdenes provisionales de este plan ya se exportaron")
    inicio = date.fromisoformat(con.execute("SELECT fecha_inicio FROM semana WHERE id=?", (ciclo["semana_id"],)).fetchone()[0])
    fin = inicio + timedelta(days=6)
    horas = {r["linea_id"]: r["horas_extra"] for r in json.loads(ver["detalle"])["crp"]["lineas"]}
    filas = con.execute("""SELECT pl.nombre AS planta, l.codigo AS linea, l.nombre AS linea_nombre, l.unidad, l.id AS linea_id,
                                  p.sku, p.nombre, pv.cantidad
                           FROM plan_version_producto pv JOIN producto p ON p.id=pv.producto_id
                           JOIN linea l ON l.id=p.linea_id JOIN planta pl ON pl.id=l.planta_id
                           WHERE pv.plan_version_id=? AND pv.cantidad > 0 ORDER BY pl.id, l.id, p.id""", (ver["id"],)).fetchall()
    lote = _siguiente_lote(con, ciclo_id)
    cab = ["CENTRO", "LINEA", "SKU", "DESCRIPCION", "CANTIDAD", "UNIDAD", "FECHA_INICIO", "FECHA_FIN", "TIPO_ORDEN", "VERSION_PLAN", "HORAS_EXTRA_LINEA"]
    por_planta: dict[str, list] = {}
    for r in filas:
        por_planta.setdefault(r["planta"], []).append([r["planta"], r["linea"], r["sku"], r["nombre"], float(r["cantidad"]), r["unidad"],
                                                        inicio.isoformat(), fin.isoformat(), "PROVISIONAL", ver["numero"],
                                                        round(horas.get(r["linea_id"], 0.0), 1)])
    archivos = []
    for planta, f in por_planta.items():
        nombre = f"ORDENES_PROV_{_slug(planta)}_{inicio.isoformat()}.csv"
        aid = _guardar(con, ciclo_id, ver["id"], lote, "ordenes_provisionales", nombre, _csv(cab, f), len(f), rol)
        archivos.append({"id": aid, "archivo": nombre, "filas": len(f), "tipo": "ordenes_provisionales"})
    cy.auditar(con, rol, "exportar_ordenes", f"plan_version:{ver['id']}",
               {"lote": lote, "archivos": [a["archivo"] for a in archivos], "filas": sum(a["filas"] for a in archivos)})
    con.commit()
    return {"lote": lote, "archivos": archivos}


# ───────────────────────── Compras, cancelaciones y lead times ─────────────────────────
def pendientes_de_exportar(con: sqlite3.Connection, ciclo_id: int) -> list[sqlite3.Row]:
    return con.execute("""SELECT p.* FROM propuesta_ia p JOIN plan_version v ON v.id=p.plan_version_id
                          WHERE p.ciclo_id=? AND p.estado IN ('aprobada','modificada') AND p.exportado_id IS NULL AND v.origen='final'
                          ORDER BY p.id""", (ciclo_id,)).fetchall()


def exportar_compras(con: sqlite3.Connection, ciclo_id: int, rol: str) -> dict:
    ciclo = cy._ciclo(con, ciclo_id)
    cy._requerir(con, ciclo, "exportar_compras", rol)
    pend = pendientes_de_exportar(con, ciclo_id)
    if not pend:
        raise cy.EstadoError("No hay propuestas aprobadas o modificadas pendientes de exportar")
    vid = pend[0]["plan_version_id"]
    lote = _siguiente_lote(con, ciclo_id)
    grupos: dict[str, list] = {"compras": [], "cancelaciones_oc": [], "lead_times": []}
    ids: dict[str, list[int]] = {k: [] for k in grupos}
    for p in pend:
        d = json.loads(p["detalle"])
        fin = json.loads(p["detalle_final"]) if p["detalle_final"] else {}
        just = p["justificacion"] or ""
        if p["tipo"] == "orden_reposicion":
            m = d["material"]
            exc = bool(d["ia"].get("excepcion_cuota"))
            un_solo = len(fin["reparto"]) == 1
            cuota_rota = exc if p["estado"] == "aprobada" else un_solo and (exc or len(d["ia"]["orden"]["reparto"]) > 1)
            for r in fin["reparto"]:
                grupos["compras"].append([r["proveedor"], m["codigo"], m["nombre"], float(r["cantidad"]), m["unidad"], fin["fecha_pedido"],
                                          fin["fecha_llegada"], (d["ia"]["consolidacion"] or {}).get("clave", ""), p["prioridad"],
                                          "MODIFICADA" if p["estado"] == "modificada" else "IA_APROBADA", "SI" if cuota_rota else "NO", just])
            ids["compras"].append(p["id"])
        elif p["tipo"] == "cancelar_oc":
            m = d["material"] or {}
            grupos["cancelaciones_oc"].append([d["oc"], m.get("codigo", ""), m.get("nombre", ""), float(d["cantidad"]), d["motivo"], just])
            ids["cancelaciones_oc"].append(p["id"])
        else:
            grupos["lead_times"].append([d["codigo"], d["material"], d["proveedor"], d["nominal"], d["sugerido"], d["n"], round(d["atraso_medio"], 1), just])
            ids["lead_times"].append(p["id"])
    plantillas = {
        "compras": ("COMPRAS_REPOSICION", ["PROVEEDOR", "COD_MATERIAL", "DESCRIPCION", "CANTIDAD", "UNIDAD", "FECHA_PEDIDO", "FECHA_ENTREGA",
                                            "PEDIDO_CONSOLIDADO", "PRIORIDAD", "ORIGEN", "CUOTA_ROTA", "JUSTIFICACION"]),
        "cancelaciones_oc": ("CANCELACION_OC", ["NUMERO_OC", "COD_MATERIAL", "DESCRIPCION", "CANTIDAD", "MOTIVO", "JUSTIFICACION"]),
        "lead_times": ("LEADTIME_MAESTRO", ["COD_MATERIAL", "DESCRIPCION", "PROVEEDOR", "LT_ACTUAL_DIAS", "LT_NUEVO_DIAS", "ENTREGAS_ANALIZADAS",
                                             "ATRASO_MEDIO_DIAS", "JUSTIFICACION"]),
    }
    hoy = cy._ahora()[:10]
    archivos = []
    for tipo, filas in grupos.items():
        if not filas:
            continue
        base, cab = plantillas[tipo]
        nombre = f"{base}_{hoy}_L{lote}.csv"
        aid = _guardar(con, ciclo_id, vid, lote, tipo, nombre, _csv(cab, filas), len(filas), rol)
        for pid in ids[tipo]:
            con.execute("UPDATE propuesta_ia SET exportado_id=? WHERE id=?", (aid, pid))
        archivos.append({"id": aid, "archivo": nombre, "filas": len(filas), "tipo": tipo})
    cy.auditar(con, rol, "exportar_compras", f"ciclo:{ciclo_id}",
               {"lote": lote, "archivos": [a["archivo"] for a in archivos], "propuestas": sum(len(v) for v in ids.values())})
    con.commit()
    return {"lote": lote, "archivos": archivos}


# ───────────────────────── Consulta ─────────────────────────
def listar(con: sqlite3.Connection, ciclo_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT id, lote, tipo, archivo, filas, sha256, rol, creado_en FROM exportacion_sap WHERE ciclo_id=? ORDER BY id DESC", (ciclo_id,))]


def obtener(con: sqlite3.Connection, export_id: int) -> dict:
    r = con.execute("SELECT * FROM exportacion_sap WHERE id=?", (export_id,)).fetchone()
    if r is None:
        raise cy.CicloError("Archivo inexistente")
    return dict(r)


def zip_del_lote(con: sqlite3.Connection, ciclo_id: int, lote: int) -> bytes:
    filas = con.execute("SELECT archivo, contenido FROM exportacion_sap WHERE ciclo_id=? AND lote=?", (ciclo_id, lote)).fetchall()
    if not filas:
        raise cy.CicloError("Lote inexistente")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in filas:
            z.writestr(f["archivo"], f["contenido"].encode("utf-8-sig"))
    return buf.getvalue()


def estado_salida(con: sqlite3.Connection, ciclo_id: int) -> dict:
    """Qué se puede exportar hoy."""
    oficial = con.execute("SELECT id FROM plan_version WHERE ciclo_id=? AND origen='final'", (ciclo_id,)).fetchone()
    ordenes_hechas = bool(oficial and con.execute(
        "SELECT 1 FROM exportacion_sap WHERE plan_version_id=? AND tipo='ordenes_provisionales'", (oficial["id"],)).fetchone())
    pend = pendientes_de_exportar(con, ciclo_id)
    return {"plan_oficial": bool(oficial), "ordenes_exportadas": ordenes_hechas,
            "propuestas_pendientes_de_exportar": {t: sum(1 for p in pend if p["tipo"] == t)
                                                  for t in ("orden_reposicion", "cancelar_oc", "actualizar_lead_time")}}
