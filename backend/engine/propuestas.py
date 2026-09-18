"""Gobernanza de la capa IA: SAP propone -> IA refina -> el Analista aprueba, modifica o rechaza con justificación.

Solo lo aprobado (o modificado) sale hacia SAP, y todo queda en la auditoría: quién, qué y por qué.
Las propuestas hechas sobre una simulación (plan no oficial) se pueden ver pero no decidir.
"""
from __future__ import annotations

import json
import sqlite3

from . import ciclo as cy
from . import ia, mrp

TIPOS_MODIFICABLES = {"orden_reposicion"}


def _construir(resultado: dict) -> list[dict]:
    """Traduce el resultado del MRP+IA en propuestas concretas."""
    mats = {m["material_id"]: m for m in resultado["materiales"]}
    out = []
    for m in resultado["materiales"]:
        o = m["ia"]["orden"]
        if not o:
            continue
        out.append({"tipo": "orden_reposicion", "material_id": m["material_id"], "prioridad": m["prioridad"], "detalle": {
            "clave": f"orden:{m['material_id']}",
            "material": {"codigo": m["codigo"], "nombre": m["nombre"], "unidad": m["unidad"], "costo_unitario": m["costo_unitario"]},
            "sap": {"orden": m["sap"]["orden"], "quiebre_dia": m["sap"]["quiebre_dia"], "lead_time": m["sap"]["lead_time"], "ss": m["sap"]["ss"]},
            "ia": {"orden": o, "quiebre_dia": m["ia"]["quiebre_dia"], "lead_time": m["ia"]["lead_time"], "ss": m["ia"]["ss"],
                   "ss_estatico": m["ia"]["ss_estatico"], "motivos": m["ia"]["motivos"], "excepcion_cuota": m["ia"]["excepcion_cuota"],
                   "consolidacion": m["ia"]["consolidacion"], "proxima_oc": m["ia"]["proxima_oc"]},
            "valor": o["cantidad"] * m["costo_unitario"]}})
    for a in resultado["anomalias"]:
        if a["tipo"] not in ("oc_duplicada", "oc_obsoleta"):
            continue
        m = mats.get(a["material_id"])
        oculta = bool(m and m["sap"]["quiebre_dia"] is None and m["ia"]["quiebre_dia"] is not None)
        out.append({"tipo": "cancelar_oc", "material_id": a["material_id"], "prioridad": "critico" if oculta else "normal", "detalle": {
            "clave": f"cancelar:{a['entidad']}", "oc": a["entidad"], "oc_id": a["oc_id"], "motivo": a["tipo"], "cantidad": a["cantidad"],
            "descripcion": a["detalle"], "efecto": a["impacto"] + (" — y esconde un quiebre real" if oculta else ""),
            "material": {"codigo": m["codigo"], "nombre": m["nombre"], "unidad": m["unidad"]} if m else None}})
    for l in resultado["lead_times"]:
        if not l["actualizar"]:
            continue
        out.append({"tipo": "actualizar_lead_time", "material_id": l["material_id"],
                    "prioridad": "normal" if abs(l["sugerido"] - l["nominal"]) >= 5 else "puede_esperar", "detalle": {
                        "clave": f"lt:{l['material_id']}:{l['proveedor_id']}", **{k: l[k] for k in (
                            "material", "codigo", "proveedor", "proveedor_id", "nominal", "real_medio", "atraso_medio", "atraso_p80", "n", "sugerido")}}})
    return out


def generar_propuestas(con: sqlite3.Connection, ciclo_id: int, rol: str, modo: str = "oficial") -> dict:
    ciclo = cy._ciclo(con, ciclo_id)
    cy._requerir(con, ciclo, "generar_propuestas", rol)
    resultado = ia.correr(con, modo)
    meta = resultado["meta"]
    if meta["ciclo_id"] != ciclo_id or meta["plan_version_id"] is None:
        raise cy.EstadoError("El ciclo no tiene una versión del plan sobre la cual proponer")
    vid = meta["plan_version_id"]
    con.execute("DELETE FROM propuesta_ia WHERE ciclo_id=? AND plan_version_id=? AND estado='pendiente'", (ciclo_id, vid))
    decididas = {json.loads(r["detalle"])["clave"] for r in con.execute(
        "SELECT detalle FROM propuesta_ia WHERE ciclo_id=? AND plan_version_id=? AND estado!='pendiente'", (ciclo_id, vid))}
    nuevas = [p for p in _construir(resultado) if p["detalle"]["clave"] not in decididas]
    for p in nuevas:
        con.execute("""INSERT INTO propuesta_ia(ciclo_id, plan_version_id, tipo, material_id, prioridad, detalle, estado, creado_en)
                       VALUES (?,?,?,?,?,?, 'pendiente', ?)""",
                    (ciclo_id, vid, p["tipo"], p["material_id"], p["prioridad"], json.dumps(p["detalle"], ensure_ascii=False), cy._ahora()))
    cy.auditar(con, rol, "generar_propuestas", f"plan_version:{vid}",
               {"modo": modo, "es_oficial": meta["es_oficial"], "nuevas": len(nuevas), "ya_decididas": len(decididas)})
    con.commit()
    return {"meta": meta, "nuevas": len(nuevas), "ya_decididas": len(decididas)}


def _fila(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["detalle"] = json.loads(d["detalle"])
    d["detalle_final"] = json.loads(d["detalle_final"]) if d["detalle_final"] else None
    return d


def listar_propuestas(con: sqlite3.Connection, ciclo_id: int) -> list[dict]:
    filas = con.execute("""SELECT p.*, v.origen AS plan_origen, v.numero AS plan_numero FROM propuesta_ia p
                           JOIN plan_version v ON v.id=p.plan_version_id WHERE p.ciclo_id=?
                           ORDER BY CASE p.prioridad WHEN 'critico' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, p.id""", (ciclo_id,))
    out = []
    for r in filas:
        d = _fila(r)
        d["es_simulacion"] = d["plan_origen"] != "final"
        out.append(d)
    return out


def decidir_propuesta(con: sqlite3.Connection, ciclo_id: int, rol: str, propuesta_id: int, decision: str,
                      justificacion: str = "", cambios: dict | None = None) -> dict:
    ciclo = cy._ciclo(con, ciclo_id)
    cy._requerir(con, ciclo, "decidir_propuesta", rol)
    if decision not in ("aprobar", "modificar", "rechazar"):
        raise cy.CicloError("Decisión inválida: aprobar, modificar o rechazar")
    fila = con.execute("""SELECT p.*, v.origen AS plan_origen FROM propuesta_ia p JOIN plan_version v ON v.id=p.plan_version_id
                          WHERE p.id=? AND p.ciclo_id=?""", (propuesta_id, ciclo_id)).fetchone()
    if fila is None:
        raise cy.CicloError("Propuesta inexistente en este ciclo")
    if fila["estado"] != "pendiente":
        raise cy.EstadoError(f"La propuesta ya fue {fila['estado']}")
    if fila["plan_origen"] != "final":
        raise cy.EstadoError("Esta propuesta se calculó sobre una simulación; solo se deciden las que parten del plan oficial")
    det = json.loads(fila["detalle"])
    just = (justificacion or "").strip()
    excepcion = fila["tipo"] == "orden_reposicion" and det["ia"].get("excepcion_cuota")
    if decision in ("rechazar", "modificar") and not just:
        raise cy.CicloError("Rechazar o modificar requiere justificación")
    if decision == "aprobar" and excepcion and not just:
        raise cy.CicloError("Esta propuesta rompe la cuota reguladora: aprobarla requiere justificación")

    final, estado, cuota_rota = None, "rechazada", False
    if decision == "aprobar":
        estado = "aprobada"
        final = det["ia"]["orden"] if fila["tipo"] == "orden_reposicion" else {k: v for k, v in det.items() if k != "clave"}
        cuota_rota = bool(excepcion)
    elif decision == "modificar":
        if fila["tipo"] not in TIPOS_MODIFICABLES:
            raise cy.CicloError("Solo se pueden modificar las órdenes de reposición")
        cambios = cambios or {}
        orden = dict(det["ia"]["orden"])
        provs = [dict(r) for r in con.execute(
            """SELECT mp.proveedor_id, p.nombre, mp.cuota_pct, mp.lead_time_nominal_dias AS lt, mp.moq, mp.multiplo
               FROM material_proveedor mp JOIN proveedor p ON p.id=mp.proveedor_id WHERE mp.material_id=?
               ORDER BY mp.cuota_pct DESC, mp.proveedor_id""", (fila["material_id"],))]
        qty = float(cambios.get("cantidad", orden["cantidad"]))
        if qty <= 0:
            raise cy.CicloError("La cantidad debe ser mayor que cero")
        p0 = provs[0]
        if qty < p0["moq"]:
            raise cy.CicloError(f"La cantidad es menor que el mínimo de compra ({p0['moq']:,.0f})")
        if qty % p0["multiplo"] > 1e-6:
            raise cy.CicloError(f"La cantidad debe ser múltiplo de {p0['multiplo']:,.0f}")
        if cambios.get("proveedor_id"):
            elegido = next((p for p in provs if p["proveedor_id"] == int(cambios["proveedor_id"])), None)
            if elegido is None:
                raise cy.CicloError("Ese proveedor no está homologado para el material")
            orden["reparto"] = [{"proveedor_id": elegido["proveedor_id"], "proveedor": elegido["nombre"], "cuota_pct": elegido["cuota_pct"], "cantidad": qty}]
            cuota_rota = len(provs) > 1
        else:
            orden["reparto"] = mrp.repartir_por_cuota(qty, provs)
        orden["cantidad"] = qty
        final, estado = orden, "modificada"
    con.execute("""UPDATE propuesta_ia SET estado=?, justificacion=?, detalle_final=?, decidido_rol=?, decidido_en=? WHERE id=?""",
                (estado, just or None, json.dumps(final, ensure_ascii=False) if final is not None else None, rol, cy._ahora(), propuesta_id))
    cy.auditar(con, rol, f"propuesta_{estado}", f"propuesta:{propuesta_id}",
               {"tipo": fila["tipo"], "material_id": fila["material_id"], "justificacion": just, "cuota_rota": cuota_rota,
                "cantidad_final": (final or {}).get("cantidad") if isinstance(final, dict) else None})
    con.commit()
    return {"estado": estado, "cuota_rota": cuota_rota}
