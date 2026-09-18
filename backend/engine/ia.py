"""Capa IA sobre el MRP: refina la propuesta SAP con datos reales. Todo estadístico y explicable (sin API externa).

1. Lead times dinámicos: lead time nominal + atraso histórico real por proveedor y material.
2. Stock de seguridad dinámico: variabilidad de la demanda (histórico pronóstico vs real) y del proveedor.
3. Anomalías: OC duplicadas u obsoletas, SolPed duplicadas o fuera de rango, movimientos contados dos veces.
4. Priorización: crítico (< 5 días o pedido ya vencido) / normal / puede esperar.
5. Cuota reguladora entre proveedores y consolidación de pedidos por proveedor y ventana.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import date, timedelta

from . import mrp

Z = 1.28                       # ~90 % de nivel de servicio
CV_MTO = 0.10                  # variabilidad supuesta para productos sin histórico (make-to-order)
DIAS_OC_OBSOLETA = 120
DIAS_OP_OBSOLETA = 90
FACTOR_SOLPED_FUERA_DE_RANGO = 6.0
UMBRAL_CRITICO_DIAS = 5
UMBRAL_LT_ACTUALIZAR = 3
MIN_ENTREGAS_LT = 8
VENTANA_REVISION_DIAS = 7


# ───────────────────────── Estadística de proveedores y demanda ─────────────────────────
def estadisticas_proveedor(con: sqlite3.Connection) -> dict[tuple[int, int], dict]:
    filas: dict[tuple[int, int], list] = {}
    for r in con.execute("""SELECT material_id, proveedor_id,
                                   julianday(fecha_recepcion) - julianday(fecha_prometida) AS atraso,
                                   julianday(fecha_recepcion) - julianday(fecha_oc) AS lt_real,
                                   cantidad_recibida * 1.0 / cantidad_pedida AS fill
                            FROM historial_entrega"""):
        filas.setdefault((r["material_id"], r["proveedor_id"]), []).append((r["atraso"], r["lt_real"], r["fill"]))
    out = {}
    for k, v in filas.items():
        atrasos = sorted(x[0] for x in v)
        out[k] = {"n": len(v), "atraso_medio": statistics.fmean(atrasos),
                  "atraso_sd": statistics.pstdev(atrasos) if len(v) > 1 else 0.0,
                  "atraso_p80": atrasos[min(len(atrasos) - 1, int(0.8 * len(atrasos)))],
                  "lt_real_medio": statistics.fmean(x[1] for x in v), "fill_medio": statistics.fmean(x[2] for x in v)}
    return out


def variabilidad_productos(con: sqlite3.Connection) -> dict[int, float]:
    err: dict[int, list] = {}
    for r in con.execute("SELECT producto_id, pronostico, real FROM historial_necesidad WHERE pronostico > 0"):
        err.setdefault(r["producto_id"], []).append((r["real"] - r["pronostico"]) / r["pronostico"])
    return {pid: statistics.pstdev(e) for pid, e in err.items() if len(e) > 3}


# ───────────────────────── Anomalías ─────────────────────────
def detectar_anomalias(con: sqlite3.Connection, ctx: dict, expl1: dict, stats: dict) -> tuple[list[dict], dict[int, str]]:
    """Devuelve la lista de anomalías y {oc_id: motivo} de las OC que hay que excluir del cálculo."""
    h = ctx["hoy"]
    mat = ctx["mat"]
    prov_nombre = {p["proveedor_id"]: p["nombre"] for lista in ctx["prov"].values() for p in lista}
    semanal = {mid: d["total"] for mid, d in expl1.items()}
    anomalias: list[dict] = []
    excluidas: dict[int, str] = {}

    def nom(mid):
        return f"{mat[mid]['codigo']} {mat[mid]['nombre']}"

    ocs = ctx["ocs"]
    # OC duplicadas: mismo material, proveedor y cantidad emitidas con ≤ 3 días de diferencia
    vistas: dict[tuple, dict] = {}
    for o in sorted(ocs, key=lambda x: (x["fecha_emision"], x["id"])):
        k = (o["material_id"], o["proveedor_id"], o["cantidad"])
        previa = vistas.get(k)
        if previa and abs((date.fromisoformat(o["fecha_emision"]) - date.fromisoformat(previa["fecha_emision"])).days) <= 3:
            excluidas[o["id"]] = "duplicada"
            anomalias.append({"tipo": "oc_duplicada", "severidad": "alta", "entidad": o["numero"], "material_id": o["material_id"],
                              "detalle": f"{o['numero']} duplica a {previa['numero']} ({nom(o['material_id'])}, {o['cantidad']:,.0f} {mat[o['material_id']]['unidad']}, "
                                         f"{prov_nombre.get(o['proveedor_id'], '')})",
                              "impacto": f"SAP cuenta {o['cantidad']:,.0f} {mat[o['material_id']]['unidad']} de más como recibo",
                              "accion": "Cancelar la OC duplicada", "cantidad": o["cantidad"], "oc_id": o["id"]})
        else:
            vistas.setdefault(k, o)
    # OC obsoletas y vencidas
    for o in ocs:
        d = date.fromisoformat(o["fecha_entrega_prometida"])
        atraso_dias = (h - d).days
        if o["id"] in excluidas or atraso_dias <= 0:
            continue
        pv = stats.get((o["material_id"], o["proveedor_id"]), {})
        if atraso_dias > DIAS_OC_OBSOLETA:
            excluidas[o["id"]] = "obsoleta"
            anomalias.append({"tipo": "oc_obsoleta", "severidad": "alta", "entidad": o["numero"], "material_id": o["material_id"],
                              "detalle": f"{o['numero']} sigue abierta {atraso_dias} días después de su fecha prometida ({nom(o['material_id'])})",
                              "impacto": f"SAP cuenta {o['cantidad']:,.0f} {mat[o['material_id']]['unidad']} que nunca llegarán",
                              "accion": "Cancelar o cerrar la OC", "cantidad": o["cantidad"], "oc_id": o["id"]})
        else:
            incumplido = pv.get("atraso_medio", 0) > 5
            anomalias.append({"tipo": "oc_vencida", "severidad": "alta" if incumplido else "media", "entidad": o["numero"],
                              "material_id": o["material_id"],
                              "detalle": f"{o['numero']} vencida hace {atraso_dias} días ({nom(o['material_id'])}, {prov_nombre.get(o['proveedor_id'], '')}"
                                         f"{', atraso histórico medio ' + format(pv['atraso_medio'], '.0f') + ' d' if pv else ''})",
                              "impacto": "El recibo se espera en los próximos días según el atraso histórico del proveedor",
                              "accion": "Expeditar con el proveedor o buscar alternativa", "cantidad": o["cantidad"], "oc_id": o["id"]})
    activas = [o for o in ocs if o["id"] not in excluidas]
    # SolPed duplicadas de una OC abierta y SolPed fuera de rango
    for s in ctx["solpeds"]:
        if any(o["material_id"] == s["material_id"] and o["cantidad"] == s["cantidad"] for o in activas):
            anomalias.append({"tipo": "solped_duplica_oc", "severidad": "media", "entidad": s["numero"], "material_id": s["material_id"],
                              "detalle": f"{s['numero']} pide lo mismo que una OC abierta ({nom(s['material_id'])}, {s['cantidad']:,.0f})",
                              "impacto": "Riesgo de comprar dos veces", "accion": "Rechazar la SolPed", "cantidad": s["cantidad"]})
        sem = semanal.get(s["material_id"], 0.0)
        if sem > 0 and s["cantidad"] > FACTOR_SOLPED_FUERA_DE_RANGO * sem:
            anomalias.append({"tipo": "solped_fuera_de_rango", "severidad": "media", "entidad": s["numero"], "material_id": s["material_id"],
                              "detalle": f"{s['numero']} pide {s['cantidad']:,.0f} de {nom(s['material_id'])}: {s['cantidad'] / sem:.0f} veces el consumo semanal",
                              "impacto": "Posible error de digitación o unidad", "accion": "Validar con el solicitante", "cantidad": s["cantidad"]})
    # Movimientos de triangulación contados dos veces
    for r in con.execute("SELECT documento, COUNT(*) n, MAX(cantidad) q FROM movimiento_triangulacion GROUP BY documento HAVING n > 1"):
        anomalias.append({"tipo": "triangulacion_duplicada", "severidad": "baja", "entidad": r["documento"], "material_id": None,
                          "detalle": f"El movimiento {r['documento']} aparece {r['n']} veces en la triangulación ({r['q']:,.0f} u)",
                          "impacto": "Sobrestima lo despachado / por despachar", "accion": "Netear el movimiento repetido", "cantidad": r["q"]})
    # Órdenes de producción provisionales obsoletas
    for r in con.execute("""SELECT o.id, p.nombre, o.cantidad, o.fecha_inicio FROM orden_produccion o JOIN producto p ON p.id=o.producto_id
                            WHERE o.tipo='provisional' AND o.estado='abierta'"""):
        antig = (h - date.fromisoformat(r["fecha_inicio"])).days
        if antig > DIAS_OP_OBSOLETA:
            anomalias.append({"tipo": "op_obsoleta", "severidad": "media", "entidad": f"OP-{r['id']}", "material_id": None,
                              "detalle": f"Orden provisional de {r['nombre']} abierta hace {antig} días ({r['cantidad']:,.0f})",
                              "impacto": "Descuenta necesidad que ya no existe (necesidad fantasma)", "accion": "Cerrar la orden", "cantidad": r["cantidad"]})
    orden = {"alta": 0, "media": 1, "baja": 2}
    anomalias.sort(key=lambda a: (orden[a["severidad"]], a["tipo"], a["entidad"]))
    return anomalias, excluidas


# ───────────────────────── Refinamiento por material ─────────────────────────
def _semana_iso(f: date) -> str:
    y, w, _ = f.isocalendar()
    return f"{y}-S{w:02d}"


def _lt_efectivo(p: dict, st: dict | None) -> int:
    return p["lt"] + (max(0, math.ceil(st["atraso_medio"])) if st else 0)


def refinar(con: sqlite3.Connection, ctx: dict, consumo: dict, expl1: dict, sap: dict, stats: dict,
            cv_prod: dict[int, float], excluidas: dict[int, str]) -> dict[int, dict]:
    h = ctx["hoy"]
    out: dict[int, dict] = {}
    for mid, m in ctx["mat"].items():
        if m["tipo"] == "premezcla":
            continue
        provs = ctx["prov"].get(mid, [])
        c = consumo[mid]
        prom = sum(c) / len(c)
        pr = provs[0] if provs else None
        # Recibos: sin OC sucias y con la fecha corrida por el atraso histórico de cada proveedor
        rec: dict[int, float] = {}
        prox = None
        for o in ctx["ocs"]:
            if o["material_id"] != mid or o["id"] in excluidas:
                continue
            st = stats.get((mid, o["proveedor_id"]))
            atraso = round(st["atraso_medio"]) if st else 0
            d = max(0, (date.fromisoformat(o["fecha_entrega_prometida"]) - h).days + max(0, atraso))
            if d < mrp.HORIZONTE_DIAS:
                rec[d] = rec.get(d, 0.0) + o["cantidad"]
            if prox is None or d < prox["dia_llegada"]:
                prox = {"numero": o["numero"], "proveedor_id": o["proveedor_id"], "prometida": o["fecha_entrega_prometida"],
                        "dia_llegada": d, "atraso_historico": atraso, "cantidad": o["cantidad"]}
        fin, quiebre = mrp.proyectar(ctx["inv"].get(mid, 0.0), c, rec)
        # Stock de seguridad dinámico: demanda + variabilidad del proveedor
        # suma directa (demanda correlacionada entre SKUs): criterio conservador
        sigma_sem = sum(q * cv_prod.get(pid, CV_MTO) for pid, q in expl1.get(mid, {"por_producto": {}})["por_producto"].items())
        sigma_dia = sigma_sem / math.sqrt(7)
        st_pr = stats.get((mid, pr["proveedor_id"])) if pr else None
        lt_ia = _lt_efectivo(pr, st_pr) if pr else None
        sd_lt = st_pr["atraso_sd"] if st_pr else 0.0
        ss_din = Z * math.sqrt((lt_ia + VENTANA_REVISION_DIAS) * sigma_dia ** 2 + prom ** 2 * sd_lt ** 2) if pr else 0.0
        ss_est = sap[mid]["ss"]
        orden = mrp.proponer_orden(fin, c, ss_din, lt_ia, pr["moq"], pr["multiplo"], h) if pr and prom > 0 else None
        excepcion = None
        if orden:
            reparto = mrp.repartir_por_cuota(orden["cantidad"], provs)
            if len(provs) > 1 and quiebre is not None:
                llegada = {p["proveedor_id"]: (date.fromisoformat(orden["fecha_pedido"]) - h).days + _lt_efectivo(p, stats.get((mid, p["proveedor_id"])))
                           for p in provs}
                principal_dia = llegada[pr["proveedor_id"]]
                alt = min((p for p in provs[1:]), key=lambda p: llegada[p["proveedor_id"]])
                if principal_dia > quiebre and llegada[alt["proveedor_id"]] <= principal_dia - 2:
                    excepcion = {"motivo": (f"{pr['nombre']} llegaría el día {principal_dia} (lead time real {lt_ia} d) y el quiebre es el día {quiebre:.1f}; "
                                            f"{alt['nombre']} llega el día {llegada[alt['proveedor_id']]}"),
                                 "cuota_original": [{"proveedor": p["nombre"], "cuota_pct": p["cuota_pct"]} for p in provs],
                                 "proveedor_preferido": alt["nombre"], "proveedor_preferido_id": alt["proveedor_id"]}
                    reparto = [{"proveedor_id": alt["proveedor_id"], "proveedor": alt["nombre"], "cuota_pct": alt["cuota_pct"],
                                "cantidad": orden["cantidad"]}]
                    lt_alt = _lt_efectivo(alt, stats.get((mid, alt["proveedor_id"])))
                    orden["fecha_llegada"] = (date.fromisoformat(orden["fecha_pedido"]) + timedelta(days=lt_alt)).isoformat()
                    orden["lead_time"] = lt_alt
            orden["reparto"] = reparto
        # Prioridad
        prioridad = None
        if orden:
            dias_a_pedir = (date.fromisoformat(orden["fecha_pedido"]) - h).days
            if (quiebre is not None and quiebre < UMBRAL_CRITICO_DIAS) or orden["retrasado"]:
                prioridad = "critico"
            elif dias_a_pedir <= 7:
                prioridad = "normal"
            else:
                prioridad = "puede_esperar"
        cob = ctx["inv"].get(mid, 0.0) / (sum(c[:7]) / 7) if sum(c[:7]) > 0 else None
        zona = "verde"
        if pr and cob is not None:
            if (quiebre is not None and quiebre < (lt_ia or 0)) or cob < (lt_ia or 0):
                zona = "rojo"
            elif cob < (lt_ia or 0) + mrp.COBERTURA_CICLO_DIAS / 2:
                zona = "amarillo"
        motivos = []
        if pr and st_pr and lt_ia != pr["lt"]:
            motivos.append(f"Lead time real de {pr['nombre']}: {lt_ia} d (atraso medio {st_pr['atraso_medio']:.1f} d en {st_pr['n']} entregas) frente a {pr['lt']} d en el maestro")
        if orden and abs(ss_din - ss_est) > 0.05 * max(ss_est, 1):
            motivos.append(f"Stock de seguridad dinámico {ss_din:,.0f} {m['unidad']} frente a {ss_est:,.0f} {m['unidad']} estático "
                           f"(variabilidad de demanda {sigma_sem / max(sum(c[:7]), 1):.0%}, atraso del proveedor ±{sd_lt:.1f} d)")
        n_exc = [o for o in ctx["ocs"] if o["material_id"] == mid and o["id"] in excluidas]
        if n_exc:
            motivos.append(f"Se excluyeron {len(n_exc)} OC sucias ({sum(o['cantidad'] for o in n_exc):,.0f} {m['unidad']}) que SAP cuenta como recibos")
        if quiebre is not None and prox:
            motivos.append(f"Quiebre proyectado en {quiebre:.1f} d; la próxima OC ({prox['numero']}) llegaría el día {prox['dia_llegada']} "
                           f"(prometida {prox['prometida']}{f', atraso histórico {prox['atraso_historico']} d' if prox['atraso_historico'] > 0 else ''})")
        elif quiebre is not None:
            motivos.append(f"Quiebre proyectado en {quiebre:.1f} d sin OC abierta que lo cubra")
        if excepcion:
            motivos.append("Se propone romper la cuota reguladora por riesgo de lead time (queda trazado): " + excepcion["motivo"])
        out[mid] = {"quiebre_dia": quiebre, "serie": fin, "ss": ss_din, "ss_estatico": ss_est, "lead_time": lt_ia,
                    "lead_time_nominal": pr["lt"] if pr else None, "orden": orden, "prioridad": prioridad, "zona": zona,
                    "cobertura_dias": cob, "proxima_oc": prox, "excepcion_cuota": excepcion, "motivos": motivos,
                    "cv_demanda": sigma_sem / max(sum(c[:7]), 1), "recibos_excluidos": len(n_exc)}
    return out


def consolidar_ordenes(refinado: dict[int, dict], ctx: dict) -> list[dict]:
    """Agrupa las reposiciones propuestas por proveedor y semana de llegada."""
    grupos: dict[tuple, list] = {}
    for mid, r in refinado.items():
        o = r["orden"]
        if not o:
            continue
        for linea in o["reparto"]:
            clave = (linea["proveedor_id"], _semana_iso(date.fromisoformat(o["fecha_llegada"])))
            grupos.setdefault(clave, []).append({"material_id": mid, "codigo": ctx["mat"][mid]["codigo"], "cantidad": linea["cantidad"],
                                                 "proveedor": linea["proveedor"]})
    out = []
    for (pid, semana), lineas in sorted(grupos.items()):
        if len(lineas) >= 2:
            out.append({"clave": f"PC-{lineas[0]['proveedor']}-{semana}".replace(" ", ""), "proveedor_id": pid, "proveedor": lineas[0]["proveedor"],
                        "semana_llegada": semana, "lineas": lineas})
    return out


# ───────────────────────── Orquestador ─────────────────────────
def correr(con: sqlite3.Connection, modo: str = "oficial") -> dict:
    plan, meta = mrp.plan_a_explotar(con, modo)
    ctx = mrp.cargar_contexto(con, plan)
    consumo, expl1 = mrp.consumo_diario(ctx)
    sap = mrp.planear_sap(ctx, consumo)
    stats = estadisticas_proveedor(con)
    cv = variabilidad_productos(con)
    anomalias, excluidas = detectar_anomalias(con, ctx, expl1, stats)
    ref = refinar(con, ctx, consumo, expl1, sap, stats, cv, excluidas)
    cons = consolidar_ordenes(ref, ctx)
    cons_por_mat: dict[int, dict] = {}
    for g in cons:
        for l in g["lineas"]:
            cons_por_mat[l["material_id"]] = {"clave": g["clave"], "n_lineas": len(g["lineas"]), "proveedor": g["proveedor"]}

    prod = ctx["prod"]
    materiales = []
    for mid, r in ref.items():
        m = ctx["mat"][mid]
        s = sap[mid]
        e = expl1.get(mid, {"total": 0.0, "por_producto": {}, "via_premezcla": {}})
        total = e["total"] or 1.0
        materiales.append({
            "material_id": mid, "codigo": m["codigo"], "nombre": m["nombre"], "tipo": m["tipo"], "unidad": m["unidad"],
            "costo_unitario": m["costo_unitario"], "stock": ctx["inv"].get(mid, 0.0), "consumo_semana": e["total"],
            "consumo_diario": e["total"] / 7, "cobertura_dias": r["cobertura_dias"], "zona": r["zona"], "prioridad": r["prioridad"],
            "proveedores": [{"proveedor_id": p["proveedor_id"], "proveedor": p["nombre"], "cuota_pct": p["cuota_pct"], "lead_time_nominal": p["lt"]} for p in ctx["prov"].get(mid, [])],
            "sap": {"quiebre_dia": s["quiebre_dia"], "orden": s["orden"], "recibos_contados": s["recibos_contados"], "ss": s["ss"],
                    "lead_time": s["lead_time"], "serie": [round(x, 1) for x in s["serie"]]},
            "ia": {"quiebre_dia": r["quiebre_dia"], "orden": r["orden"], "ss": r["ss"], "ss_estatico": r["ss_estatico"], "lead_time": r["lead_time"],
                   "proxima_oc": r["proxima_oc"], "excepcion_cuota": r["excepcion_cuota"], "motivos": r["motivos"], "cv_demanda": r["cv_demanda"],
                   "consolidacion": cons_por_mat.get(mid), "serie": [round(x, 1) for x in r["serie"]]},
            "explosion": {"por_producto": sorted(
                [{"producto_id": pid, "producto": prod[pid]["nombre"], "cantidad": q, "pct": q / total} for pid, q in e["por_producto"].items()],
                key=lambda x: -x["cantidad"]),
                "via_premezcla": [{"material": ctx["mat"][pm]["nombre"], "cantidad": q, "pct": q / total} for pm, q in e["via_premezcla"].items()]},
        })
    orden_zona = {"rojo": 0, "amarillo": 1, "verde": 2}
    materiales.sort(key=lambda x: (orden_zona[x["zona"]], x["cobertura_dias"] if x["cobertura_dias"] is not None else 999))

    lead_times = []
    for (mid, pid), st in sorted(stats.items()):
        p = next((x for x in ctx["prov"].get(mid, []) if x["proveedor_id"] == pid), None)
        if p is None:
            continue
        nuevo = _lt_efectivo(p, st)
        lead_times.append({"material_id": mid, "material": ctx["mat"][mid]["nombre"], "codigo": ctx["mat"][mid]["codigo"],
                           "proveedor_id": pid, "proveedor": p["nombre"], "nominal": p["lt"], "real_medio": st["lt_real_medio"],
                           "atraso_medio": st["atraso_medio"], "atraso_p80": st["atraso_p80"], "n": st["n"], "sugerido": nuevo,
                           "actualizar": st["n"] >= MIN_ENTREGAS_LT and abs(st["atraso_medio"]) >= UMBRAL_LT_ACTUALIZAR})
    con_quiebre_sap = sum(1 for x in materiales if x["sap"]["quiebre_dia"] is not None)
    con_quiebre_ia = sum(1 for x in materiales if x["ia"]["quiebre_dia"] is not None)
    ocultos = [x["codigo"] for x in materiales if x["ia"]["quiebre_dia"] is not None and x["sap"]["quiebre_dia"] is None]
    valor = sum(x["ia"]["orden"]["cantidad"] * x["costo_unitario"] for x in materiales if x["ia"]["orden"])
    return {
        "meta": {**meta, "hoy": ctx["hoy"].isoformat(), "horizonte_dias": mrp.HORIZONTE_DIAS},
        "resumen": {"materiales": len(materiales), "quiebre_sap": con_quiebre_sap, "quiebre_ia": con_quiebre_ia,
                    "quiebres_ocultos_por_sap": ocultos, "zonas": {z: sum(1 for x in materiales if x["zona"] == z) for z in ("rojo", "amarillo", "verde")},
                    "propuestas_reposicion": sum(1 for x in materiales if x["ia"]["orden"]),
                    "criticas": sum(1 for x in materiales if x["prioridad"] == "critico"), "anomalias": len(anomalias),
                    "lead_times_a_actualizar": sum(1 for x in lead_times if x["actualizar"]), "valor_reposicion": valor},
        "materiales": materiales, "anomalias": anomalias, "lead_times": lead_times, "consolidaciones": cons,
        "excluidas": {str(k): v for k, v in excluidas.items()},
    }
