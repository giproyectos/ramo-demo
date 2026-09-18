"""MRP: explosión del plan a materiales, proyección diaria de inventario y propuesta de reposición tipo SAP.

Es el cálculo "nativo": lead time fijo del dato maestro, stock de seguridad estático y todas las OC abiertas
contadas tal cual (incluidas las sucias). La capa IA (ia.py) refina este resultado sin reemplazarlo.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta

from . import datos

HORIZONTE_DIAS = 28
COBERTURA_CICLO_DIAS = 14        # cuánto cubre cada reposición después del punto de necesidad


class MrpError(Exception):
    pass


# ───────────────────────── Contexto ─────────────────────────
def hoy(con: sqlite3.Connection) -> date:
    return date.fromisoformat(con.execute("SELECT fecha_inicio FROM semana WHERE es_semilla=1").fetchone()[0])


def _necesidad_semana(con, semana_id: int) -> dict[int, float]:
    return {p["producto_id"]: p["total"] for p in datos.cargar_necesidad_productos(con, semana_id)}


def plan_a_explotar(con: sqlite3.Connection, modo: str = "oficial") -> tuple[dict[int, float], dict]:
    """Cantidades de la semana 1: plan oficial aprobado o simulación (última versión, o necesidad sin CRP)."""
    if modo not in ("oficial", "simulacion"):
        raise MrpError("Modo inválido: use 'oficial' o 'simulacion'")
    sem = datos.semana_semilla(con)
    ciclo = con.execute("SELECT id FROM ciclo WHERE semana_id=?", (sem,)).fetchone()
    ver = None
    if ciclo:
        q = "SELECT * FROM plan_version WHERE ciclo_id=?" + (" AND origen='final'" if modo == "oficial" else "")
        ver = con.execute(q + " ORDER BY numero DESC LIMIT 1", (ciclo["id"],)).fetchone()
    if modo == "oficial":
        if ver is None:
            raise MrpError("Todavía no hay un plan oficial aprobado; usa la simulación para adelantarte")
    if ver is not None:
        plan = {r["producto_id"]: r["cantidad"] for r in con.execute(
            "SELECT producto_id, cantidad FROM plan_version_producto WHERE plan_version_id=?", (ver["id"],))}
        meta = {"modo": modo, "es_oficial": ver["origen"] == "final", "ciclo_id": ciclo["id"],
                "plan_version_id": ver["id"], "version": ver["numero"], "origen": ver["origen"]}
    else:
        plan = _necesidad_semana(con, sem)
        meta = {"modo": modo, "es_oficial": False, "ciclo_id": ciclo["id"] if ciclo else None,
                "plan_version_id": None, "version": None, "origen": "necesidad_sin_crp"}
    return plan, meta


def cargar_contexto(con: sqlite3.Connection, plan: dict[int, float]) -> dict:
    h = hoy(con)
    semanas = [r["id"] for r in con.execute("SELECT id FROM semana ORDER BY fecha_inicio LIMIT 4")]
    necesidad = [plan] + [_necesidad_semana(con, s) for s in semanas[1:]]
    mat = {r["id"]: dict(r) for r in con.execute("SELECT * FROM material")}
    prod = {r["id"]: dict(r) for r in con.execute("SELECT * FROM producto")}
    bom_prod: dict[int, list] = {}
    for r in con.execute("SELECT producto_id, material_id, cantidad, merma_pct FROM bom_producto"):
        bom_prod.setdefault(r[0], []).append((r[1], r[2], r[3]))
    bom_pm: dict[int, list] = {}
    for r in con.execute("SELECT premezcla_id, material_id, cantidad FROM bom_premezcla"):
        bom_pm.setdefault(r[0], []).append((r[1], r[2]))
    prov: dict[int, list] = {}
    for r in con.execute("""SELECT mp.material_id, mp.proveedor_id, p.nombre, p.origen, mp.cuota_pct,
                                   mp.lead_time_nominal_dias AS lt, mp.moq, mp.multiplo
                            FROM material_proveedor mp JOIN proveedor p ON p.id=mp.proveedor_id
                            ORDER BY mp.material_id, mp.cuota_pct DESC, mp.proveedor_id"""):
        prov.setdefault(r["material_id"], []).append(dict(r))
    inv = {r["material_id"]: r["cantidad"] for r in con.execute("SELECT material_id, cantidad FROM inventario_material")}
    ocs = [dict(r) for r in con.execute(
        "SELECT * FROM orden_compra WHERE estado='abierta' ORDER BY fecha_entrega_prometida, id")]
    solpeds = [dict(r) for r in con.execute("SELECT * FROM solped WHERE estado='abierta' ORDER BY id")]
    return {"hoy": h, "necesidad": necesidad, "mat": mat, "prod": prod, "bom_prod": bom_prod, "bom_pm": bom_pm,
            "prov": prov, "inv": inv, "ocs": ocs, "solpeds": solpeds}


# ───────────────────────── Explosión ─────────────────────────
def explotar(ctx: dict, need: dict[int, float]) -> dict[int, dict]:
    """material_id -> {total, por_producto, via_premezcla}. Las premezclas se explotan a sus componentes."""
    res: dict[int, dict] = {}

    def sumar(mid, q, pid, via=None):
        d = res.setdefault(mid, {"total": 0.0, "por_producto": {}, "via_premezcla": {}})
        d["total"] += q
        d["por_producto"][pid] = d["por_producto"].get(pid, 0.0) + q
        if via is not None:
            d["via_premezcla"][via] = d["via_premezcla"].get(via, 0.0) + q

    for pid, q in need.items():
        if q <= 0:
            continue
        for mid, cant, merma in ctx["bom_prod"].get(pid, []):
            mq = q * cant * (1 + merma)
            sumar(mid, mq, pid)
            if ctx["mat"][mid]["tipo"] == "premezcla":
                for cid, c in ctx["bom_pm"].get(mid, []):
                    sumar(cid, mq * c, pid, via=mid)
    return res


def consumo_diario(ctx: dict) -> tuple[dict[int, list[float]], dict[int, dict]]:
    """Consumo diario por material en el horizonte (cada semana se reparte en 7 días) y explosión de la semana 1."""
    por_semana = [explotar(ctx, n) for n in ctx["necesidad"]]
    diario: dict[int, list[float]] = {}
    for mid in ctx["mat"]:
        v = []
        for t in range(HORIZONTE_DIAS):
            w = min(t // 7, len(por_semana) - 1)
            v.append(por_semana[w].get(mid, {"total": 0.0})["total"] / 7)
        diario[mid] = v
    return diario, por_semana[0]


# ───────────────────────── Proyección y reposición ─────────────────────────
def proyectar(stock0: float, consumo: list[float], recibos: dict[int, float]) -> tuple[list[float], float | None]:
    """Stock al cierre de cada día y momento (en días, fraccionario) en que el inventario llega a cero."""
    stock, fin, quiebre = stock0, [], None
    for t, c in enumerate(consumo):
        antes = stock + recibos.get(t, 0.0)
        stock = antes - c
        if quiebre is None and stock < -1e-6 and c > 0:
            quiebre = t + max(0.0, antes) / c
        fin.append(stock)
    return fin, quiebre


def _redondear_arriba(q: float, moq: float, mult: float) -> float:
    q = max(q, moq)
    return math.ceil(q / mult - 1e-9) * mult if mult > 0 else q


def proponer_orden(fin: list[float], consumo: list[float], ss_qty: float, lt: int, moq: float, mult: float,
                   h: date) -> dict | None:
    """Primera necesidad neta (stock < seguridad) -> cantidad para cubrir un ciclo, con fecha de pedido según LT."""
    t_need = next((t for t, s in enumerate(fin) if s < ss_qty - 1e-6), None)
    if t_need is None:
        return None
    fin_t = t_need + COBERTURA_CICLO_DIAS + 1
    ventana = consumo[t_need:fin_t] or [0.0]
    prom = sum(ventana) / len(ventana)
    objetivo = ss_qty + COBERTURA_CICLO_DIAS * prom
    qty = _redondear_arriba(max(0.0, objetivo - fin[t_need]), moq, mult)
    f_nec = h + timedelta(days=t_need)
    f_ped = f_nec - timedelta(days=lt)
    retrasado = f_ped < h
    f_ped_real = max(f_ped, h)
    return {"dia_necesidad": t_need, "fecha_necesidad": f_nec.isoformat(), "fecha_pedido": f_ped_real.isoformat(),
            "fecha_pedido_ideal": f_ped.isoformat(), "retrasado": retrasado, "dias_retraso": (h - f_ped).days if retrasado else 0,
            "fecha_llegada": (f_ped_real + timedelta(days=lt)).isoformat(), "lead_time": lt, "cantidad": qty}


def principal(prov: list[dict]) -> dict | None:
    return prov[0] if prov else None


def repartir_por_cuota(qty: float, prov: list[dict]) -> list[dict]:
    """Reparte una cantidad entre proveedores según su cuota (respeta múltiplos; lo que sobra va al principal)."""
    if not prov:
        return []
    if len(prov) == 1:
        return [{"proveedor_id": prov[0]["proveedor_id"], "proveedor": prov[0]["nombre"], "cuota_pct": 100.0, "cantidad": qty}]
    partes, asignado = [], 0.0
    for p in prov[1:]:
        q = math.floor(qty * p["cuota_pct"] / 100 / p["multiplo"]) * p["multiplo"]
        q = q if q >= p["moq"] else 0.0
        partes.append({"proveedor_id": p["proveedor_id"], "proveedor": p["nombre"], "cuota_pct": p["cuota_pct"], "cantidad": q})
        asignado += q
    p0 = prov[0]
    partes.insert(0, {"proveedor_id": p0["proveedor_id"], "proveedor": p0["nombre"], "cuota_pct": p0["cuota_pct"],
                      "cantidad": max(0.0, qty - asignado)})
    return [x for x in partes if x["cantidad"] > 0]


def recibos_sap(ctx: dict, mid: int) -> tuple[dict[int, float], list[dict]]:
    """SAP cuenta todas las OC abiertas en su fecha prometida; las vencidas caen hoy y siguen inflando la proyección."""
    rec: dict[int, float] = {}
    usados = []
    for o in ctx["ocs"]:
        if o["material_id"] != mid:
            continue
        d = max(0, (date.fromisoformat(o["fecha_entrega_prometida"]) - ctx["hoy"]).days)
        usados.append(o)
        if d < HORIZONTE_DIAS:
            rec[d] = rec.get(d, 0.0) + o["cantidad"]
    return rec, usados


def planear_sap(ctx: dict, consumo: dict[int, list[float]]) -> dict[int, dict]:
    """Corrida tipo SAP por material comprable."""
    out: dict[int, dict] = {}
    h = ctx["hoy"]
    for mid, m in ctx["mat"].items():
        if m["tipo"] == "premezcla":
            continue
        c = consumo[mid]
        prom = sum(c) / len(c)
        rec, usados = recibos_sap(ctx, mid)
        fin, quiebre = proyectar(ctx["inv"].get(mid, 0.0), c, rec)
        pr = principal(ctx["prov"].get(mid, []))
        ss = m["stock_seguridad_dias"] * prom
        orden = proponer_orden(fin, c, ss, pr["lt"], pr["moq"], pr["multiplo"], h) if pr and prom > 0 else None
        if orden:
            orden["reparto"] = repartir_por_cuota(orden["cantidad"], ctx["prov"][mid])
        out[mid] = {"quiebre_dia": quiebre, "serie": fin, "recibos_contados": len(usados),
                    "recibos_cantidad": sum(o["cantidad"] for o in usados), "ss": ss, "lead_time": pr["lt"] if pr else None,
                    "orden": orden}
    return out
