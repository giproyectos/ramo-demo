"""Generador del dataset sintético del demo MPS/CRP -> MRP.

Reproducible (semilla fija), solo biblioteca estándar. Todos los datos son inventados.
La semana semilla (semana 0 del horizonte) reproduce el escenario del mockup:
Barra Rellena 137 % -> 107 % conjunto con Mini Ponqué -> ~8,5 h extra.

Uso:  python -m backend.synth.generate [--seed 42] [--out ramo.db]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = Path(__file__).with_name("schema.sql")

HOY = date(2026, 9, 21)            # lunes de la semana semilla; corte de inventario 8am
N_SEMANAS = 13                     # horizonte táctico de 3 meses
N_SEMANAS_HIST = 104               # 2 años de histórico de necesidad
FESTIVOS = [date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 16), date(2026, 12, 8)]

# ───────────────────────── Maestros ─────────────────────────
PLANTAS = ["Planta A", "Planta B"]

# codigo, nombre, planta, unidad, ritmo, ritmo_prev, horas_brutas, regular_semilla, nota
LINEAS = [
    ("L01", "Ponqué Individual", 0, "u", 28000, None, 126, 3_400_000, None),
    ("L02", "Barra Rellena", 0, "u", 14000, None, 100, 1_750_000, "Comparte tripulación con Mini Ponqué"),
    ("L03", "Mini Ponqué", 0, "u", 27000, 15000, 70, 1_500_000, "Ritmo subió de 15.000 a 27.000 u/h por automatización"),
    ("L04", "Galleta Extruida", 1, "kg", 620, None, 112, 55_000, None),
    ("L05", "Snack Horneado", 1, "kg", 70, None, 84, 4_800, None),
    ("L06", "Tostada Horneada", 1, "kg", 300, None, 90, 23_000, "Comparte tripulación con Maicito Extruido"),
    ("L07", "Maicito Extruido", 1, "kg", 640, None, 80, 40_000, None),
]
# La semana semilla es un pico para Barra Rellena; en semanas normales su necesidad regular es menor.
FACTOR_NORMAL = {"L02": 0.72}
GRUPOS = {"Tripulación Planta A-1": ["L02", "L03"], "Tripulación Planta B-1": ["L06", "L07"]}

# linea, nombre, participación regular, peso kg/u, costo/u, unidades por caja, extras de BOM, flag MTO
SKUS = [
    ("L01", "Ponqué Individual Vainilla 40 g", .30, .040, 620, 24, {"MP-012": .0004}, None),
    ("L01", "Ponqué Individual Chocolate 40 g", .28, .040, 650, 24, {"MP-006": .003}, None),
    ("L01", "Ponqué Individual Arequipe 45 g", .22, .045, 700, 24, {"MP-013": .008}, None),
    ("L01", "Ponqué Individual Fresa 40 g", .20, .040, 660, 24, {"MP-014": .008}, None),
    ("L02", "Barra Rellena Dulce de leche 35 g", .50, .035, 780, 30, {}, None),
    ("L02", "Barra Rellena Fresa 35 g", .30, .035, 760, 30, {"MP-014": .004}, None),
    ("L02", "Barra Rellena Chocolate 35 g", .20, .035, 790, 30, {"MP-006": .002}, None),
    ("L02", "Barra Rellena Dulce de leche 35 g (canal directo)", 0, .035, 740, 30, {}, "canal_directo_mto"),
    ("L03", "Mini Ponqué Vainilla 25 g", .50, .025, 430, 36, {"MP-012": .0003}, None),
    ("L03", "Mini Ponqué Chocolate 25 g", .50, .025, 450, 36, {"MP-006": .002}, None),
    ("L03", "Mini Ponqué Vainilla 25 g (exportación)", 0, .025, 470, 36, {}, "exportacion_mto"),
    ("L04", "Galleta Extruida Original", .50, 1, 8200, 10, {}, None),
    ("L04", "Galleta Extruida Avena", .30, 1, 8900, 10, {"MP-022": .15}, None),
    ("L04", "Galleta Extruida Miel", .20, 1, 9100, 10, {"MP-021": .04}, None),
    ("L05", "Snack Horneado Queso", .60, 1, 12500, 10, {"MP-018": .02}, None),
    ("L05", "Snack Horneado Natural", .25, 1, 11800, 10, {}, None),
    ("L05", "Snack Horneado Picante", .15, 1, 12900, 10, {}, None),
    ("L06", "Tostada Horneada Clásica", .60, 1, 9800, 10, {}, None),
    ("L06", "Tostada Horneada Integral", .40, 1, 10400, 10, {"MP-022": .05}, None),
    ("L07", "Maicito Extruido Queso", .60, 1, 9300, 10, {"MP-018": .015}, None),
    ("L07", "Maicito Extruido Natural", .40, 1, 8700, 10, {}, None),
]

# BOM base por línea: material -> cantidad por unidad productiva
BOM_LINEA = {
    "L01": {"PM-001": .022, "MP-003": .008, "MP-004": .006, "MP-002": .004, "MP-005": .003,
            "EM-001": .003, "EM-003": 1 / 24, "EM-004": 1.0},
    "L02": {"PM-001": .012, "MP-013": .010, "MP-007": .008, "MP-009": .004,
            "EM-001": .003, "EM-003": 1 / 30, "EM-007": 1.0},
    "L03": {"PM-001": .015, "MP-003": .005, "MP-004": .004, "MP-002": .003,
            "EM-001": .002, "EM-003": 1 / 36, "EM-004": 1.0, "EM-005": .02},
    "L04": {"PM-002": .70, "MP-016": .06, "MP-009": .05, "MP-019": .002,
            "EM-006": .015, "EM-003": .10},
    "L05": {"MP-015": .55, "MP-016": .08, "PM-003": .06, "EM-002": .02, "EM-003": .10},
    "L06": {"MP-001": .70, "MP-016": .06, "MP-010": .01, "MP-011": .004, "PM-003": .03,
            "EM-002": .02, "EM-003": .10},
    "L07": {"MP-015": .75, "MP-016": .08, "PM-003": .07, "MP-020": .05, "EM-002": .02, "EM-003": .10},
}

# codigo, nombre, tipo, unidad, costo, stock de seguridad (días, estático)
MATERIALES = [
    ("MP-001", "Harina de trigo", "materia_prima", "kg", 3200, 10),
    ("MP-002", "Azúcar refinada", "materia_prima", "kg", 3600, 10),
    ("MP-003", "Huevo líquido", "materia_prima", "kg", 7500, 4),
    ("MP-004", "Aceite vegetal", "materia_prima", "kg", 9000, 8),
    ("MP-005", "Margarina", "materia_prima", "kg", 10500, 8),
    ("MP-006", "Cacao en polvo", "materia_prima", "kg", 22000, 25),
    ("MP-007", "Cobertura de chocolate", "materia_prima", "kg", 26000, 25),
    ("MP-008", "Leche en polvo", "materia_prima", "kg", 24000, 25),
    ("MP-009", "Glucosa", "materia_prima", "kg", 4200, 10),
    ("MP-010", "Sal", "materia_prima", "kg", 900, 12),
    ("MP-011", "Levadura química", "materia_prima", "kg", 8000, 15),
    ("MP-012", "Esencia de vainilla", "materia_prima", "kg", 60000, 30),
    ("MP-013", "Relleno de dulce de leche", "materia_prima", "kg", 14000, 10),
    ("MP-014", "Relleno de fresa", "materia_prima", "kg", 12000, 10),
    ("MP-015", "Harina de maíz", "materia_prima", "kg", 2800, 10),
    ("MP-016", "Aceite de palma", "materia_prima", "kg", 6000, 10),
    ("MP-017", "Almidón modificado", "materia_prima", "kg", 5200, 12),
    ("MP-018", "Saborizante de queso", "materia_prima", "kg", 48000, 30),
    ("MP-019", "Conservante", "materia_prima", "kg", 30000, 20),
    ("MP-020", "Cereal inflado", "materia_prima", "kg", 9500, 10),
    ("MP-021", "Miel", "materia_prima", "kg", 11000, 12),
    ("MP-022", "Avena", "materia_prima", "kg", 4800, 10),
    ("EM-001", "Película flexible para ponqué", "empaque", "kg", 21000, 15),
    ("EM-002", "Película flexible para snack", "empaque", "kg", 23000, 15),
    ("EM-003", "Caja corrugada", "empaque", "u", 2600, 12),
    ("EM-004", "Etiqueta adhesiva", "empaque", "u", 40, 15),
    ("EM-005", "Bandeja plástica", "empaque", "u", 180, 15),
    ("EM-006", "Bolsa de alta barrera", "empaque", "kg", 25000, 15),
    ("EM-007", "Estuche para barra", "empaque", "u", 120, 15),
    ("PM-001", "Premezcla base de ponqué", "premezcla", "kg", 5200, 3),
    ("PM-002", "Premezcla de masa de galleta", "premezcla", "kg", 4900, 3),
    ("PM-003", "Premezcla de sazonador de snack", "premezcla", "kg", 14000, 3),
]
BOM_PREMEZCLA = {
    "PM-001": {"MP-001": .55, "MP-002": .30, "MP-011": .03, "MP-010": .01, "MP-008": .11},
    "PM-002": {"MP-001": .60, "MP-002": .20, "MP-017": .10, "MP-010": .02, "MP-019": .01, "MP-005": .07},
    "PM-003": {"MP-010": .40, "MP-018": .40, "MP-017": .15, "MP-019": .05},
}
IMPORTADOS = {"MP-006", "MP-007", "MP-008", "MP-012", "MP-018"}
# cobertura inicial pactada para el relato de la demo (días). El resto se sortea.
COBERTURA_FORZADA = {"MP-002": 4.0, "EM-002": 3.5, "MP-018": 5.0}

# nombre, origen, atraso medio (días vs. prometido), desv., fill rate medio
PROVEEDORES = [
    ("Proveedor 01", "local", 0.5, 1.0, .99), ("Proveedor 02", "local", 1.0, 1.5, .98),
    ("Proveedor 03", "local", 0.0, 1.0, .99), ("Proveedor 04", "local", 1.5, 2.0, .97),
    ("Proveedor 05", "local", 2.0, 2.0, .97), ("Proveedor 06", "local", 0.5, 1.5, .99),
    ("Proveedor 07", "local", 9.0, 4.0, .90),          # incumplido crónico
    ("Proveedor 08", "local", 1.0, 1.5, .98),
    ("Proveedor 09", "importado", 4.0, 3.0, .96), ("Proveedor 10", "importado", 3.0, 2.5, .97),
    ("Proveedor 11", "importado", 6.0, 4.0, .94), ("Proveedor 12", "importado", 2.0, 2.0, .98),
]
PV_INCUMPLIDO = "Proveedor 07"


# ───────────────────────── Utilidades ─────────────────────────
def estacionalidad(d: date) -> float:
    """Factor estacional relativo (1.0 en la semana semilla)."""
    def crudo(x: date) -> float:
        doy = x.timetuple().tm_yday
        f = 1 + 0.07 * math.sin(2 * math.pi * (doy - 60) / 365)
        if x.month == 12 or (x.month == 11 and x.day >= 20):
            f += 0.14
        if x.month == 1 and x.day <= 20:
            f -= 0.10
        if x.month == 4 and x.day <= 8:
            f -= 0.06
        return f
    return crudo(d) / crudo(HOY)


def reparto_entero(total: int, pesos: list[float], paso: int) -> list[int]:
    """Reparte `total` en múltiplos de `paso` según `pesos`; el resto va al mayor peso. Suma exacta."""
    if not pesos or sum(pesos) == 0:
        return [0] * len(pesos)
    partes = [int(total * p / sum(pesos) // paso) * paso for p in pesos]
    partes[max(range(len(pesos)), key=pesos.__getitem__)] += total - sum(partes)
    return partes


def redondear(x: float, paso: int) -> int:
    return int(round(x / paso) * paso)


def iso(d: date) -> str:
    return d.isoformat()


# ───────────────────────── Generación ─────────────────────────
def generar(db_path: Path, seed: int = 42) -> dict:
    rng = random.Random(seed)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    control: dict[str, object] = {"seed": seed}

    # Plantas, líneas, tripulaciones
    con.executemany("INSERT INTO planta(id, nombre) VALUES (?,?)", [(i + 1, n) for i, n in enumerate(PLANTAS)])
    linea_id: dict[str, int] = {}
    for i, (cod, nom, pl, un, ritmo, prev, hb, _, nota) in enumerate(LINEAS, start=1):
        linea_id[cod] = i
        con.execute(
            "INSERT INTO linea VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, cod, nom, pl + 1, un, ritmo, prev, iso(date(2026, 7, 6)) if prev else None, hb, nota),
        )
    for gi, (gn, miembros) in enumerate(GRUPOS.items(), start=1):
        con.execute("INSERT INTO grupo_tripulacion VALUES (?,?)", (gi, gn))
        for m in miembros:
            con.execute("INSERT INTO linea_grupo VALUES (?,?)", (linea_id[m], gi))

    # Semanas y calendario
    semanas = [HOY + timedelta(weeks=k) for k in range(N_SEMANAS)]
    for k, s in enumerate(semanas, start=1):
        con.execute("INSERT INTO semana VALUES (?,?,?)", (k, iso(s), 1 if k == 1 else 0))
    eventos = []
    for cod, lid in linea_id.items():
        hb = next(l[6] for l in LINEAS if l[0] == cod)
        for k, s in enumerate(semanas, start=1):
            for f in FESTIVOS:
                if s <= f < s + timedelta(days=7):
                    eventos.append((lid, k, iso(f), "festivo", round(hb * 0.10 * 2) / 2, None, "Festivo"))
            if k > 1 and (k + list(linea_id).index(cod)) % 4 == 0:
                eventos.append((lid, k, iso(s + timedelta(days=2)), "mantenimiento",
                                float(rng.choice([8, 10, 12])), 30, "Mantenimiento programado"))
    eventos.append((linea_id["L04"], 1, iso(HOY + timedelta(days=3)), "mantenimiento", 12.0, 30,
                    "Mantenimiento programado"))
    eventos.append((linea_id["L05"], 1, iso(HOY + timedelta(days=2)), "parada", 8.0, 3,
                    "Parada de contingencia avisada con poca anticipación"))
    con.executemany(
        "INSERT INTO evento_calendario(linea_id,semana_id,fecha,tipo,horas,avisado_dias_antes,descripcion)"
        " VALUES (?,?,?,?,?,?,?)", eventos)

    # Productos
    producto_id: dict[str, int] = {}
    linea_de_producto: dict[int, str] = {}
    for i, (lc, nom, part, peso, costo, upc, _, mto) in enumerate(SKUS, start=1):
        un = next(l[3] for l in LINEAS if l[0] == lc)
        sku = f"{lc}-{i:03d}"
        producto_id[nom] = i
        linea_de_producto[i] = lc
        con.execute("INSERT INTO producto VALUES (?,?,?,?,?,?,?,?,?)",
                    (i, sku, nom, linea_id[lc], next(l[1] for l in LINEAS if l[0] == lc),
                     "caja", upc, peso, costo))
        if mto:
            control.setdefault("skus_mto", []).append(sku)
    skus_por_linea: dict[str, list[tuple[int, float, str | None]]] = {}
    for i, (lc, nom, part, *_rest) in enumerate(SKUS, start=1):
        skus_por_linea.setdefault(lc, []).append((i, part, _rest[-1]))

    # Necesidad por semana (salida de Demanda/DRP, ya calculada)
    nec_rows = []

    def paso(lc: str) -> int:
        return 1000 if next(l[3] for l in LINEAS if l[0] == lc) == "u" else 10

    for k, s in enumerate(semanas, start=1):
        for lc, *_ in LINEAS:
            base = next(l[7] for l in LINEAS if l[0] == lc)
            factor = 1.0 if k == 1 else estacionalidad(s) * rng.uniform(0.95, 1.05)
            total = redondear(base * factor * (FACTOR_NORMAL.get(lc, 1.0) if k > 1 else 1.0), paso(lc))
            regs = [(pid, p) for pid, p, mto in skus_por_linea[lc] if mto is None]
            partes = reparto_entero(total, [p * (1 if k == 1 else rng.uniform(.92, 1.08)) for _, p in regs], paso(lc))
            for (pid, _), q in zip(regs, partes):
                nec_rows.append((k, pid, "regular_cedi", "modelo_drp", q))
    # MTO
    def mto_pid(flujo: str) -> int:
        return next(pid for lc in skus_por_linea for pid, _, m in skus_por_linea[lc] if m == flujo)
    pid_cd, pid_ex = mto_pid("canal_directo_mto"), mto_pid("exportacion_mto")
    for k in range(1, N_SEMANAS + 1):
        if k == 1:
            cd, ex = 170_000, 100_000
        else:
            cd = redondear(rng.uniform(100_000, 250_000), 1000) if rng.random() < .40 else 0
            ex = redondear(rng.uniform(60_000, 150_000), 1000) if rng.random() < .30 else 0
        if cd:
            nec_rows.append((k, pid_cd, "canal_directo_mto", "origen_pedido", cd))
        if ex:
            nec_rows.append((k, pid_ex, "exportacion_mto", "origen_pedido", ex))
        # 'archivo estándar' con basura: el canal directo duplicado y la exportación en cero
        if k == 1:
            nec_rows.append((k, pid_cd, "canal_directo_mto", "archivo_estandar", cd))
            nec_rows.append((k, pid_cd, "canal_directo_mto", "archivo_estandar", cd))
            nec_rows.append((k, pid_ex, "exportacion_mto", "archivo_estandar", 0))
        else:
            if cd:
                nec_rows.append((k, pid_cd, "canal_directo_mto", "archivo_estandar", cd))
            if ex:
                nec_rows.append((k, pid_ex, "exportacion_mto", "archivo_estandar", ex))
    con.executemany("INSERT INTO necesidad_semana(semana_id,producto_id,flujo,fuente,cantidad) VALUES (?,?,?,?,?)",
                    nec_rows)
    control["archivo_estandar_basura"] = "semana 1: canal directo duplicado (2x170.000), exportación en 0"

    # Plan táctico: capacidad máxima proyectada a mix (92 %)
    horas_evento: dict[tuple[int, int], float] = {}
    for lid, sid, _, _, h, *_ in eventos:
        horas_evento[(lid, sid)] = horas_evento.get((lid, sid), 0) + h
    for lc, lid in linea_id.items():
        l = next(x for x in LINEAS if x[0] == lc)
        for k in range(1, N_SEMANAS + 1):
            cap = l[4] * (l[6] - horas_evento.get((lid, k), 0))
            con.execute("INSERT INTO plan_tactico VALUES (?,?,?)", (lid, k, redondear(cap * .92, paso(lc))))

    # Histórico de necesidad por SKU regular (pronóstico vs real)
    hist = []
    for pid, (lc, nom, part, *_r) in enumerate(SKUS, start=1):
        if part == 0:
            continue
        base = next(l[7] for l in LINEAS if l[0] == lc) * part * FACTOR_NORMAL.get(lc, 1.0)
        cv = rng.uniform(.05, .22)
        sesgo = rng.uniform(-.03, .03)
        for w in range(N_SEMANAS_HIST, 0, -1):
            d = HOY - timedelta(weeks=w)
            pron = base * estacionalidad(d) * (1 + .0008 * (N_SEMANAS_HIST - w) / 10)
            real = pron * (1 + sesgo + rng.gauss(0, cv))
            paso_l = paso(lc)
            hist.append((pid, iso(d), redondear(pron, paso_l), max(0, redondear(real, paso_l))))
    con.executemany("INSERT INTO historial_necesidad VALUES (?,?,?,?)", hist)

    # Materiales y BOM
    mat_id = {}
    for i, (cod, nom, tipo, un, costo, ss) in enumerate(MATERIALES, start=1):
        mat_id[cod] = i
        con.execute("INSERT INTO material VALUES (?,?,?,?,?,?,?)", (i, cod, nom, tipo, un, costo, ss)[:7])
    bom_rows = []
    for pid, (lc, nom, part, peso, costo, upc, extras, mto) in enumerate(SKUS, start=1):
        comp = dict(BOM_LINEA[lc])
        comp.update(extras)
        for cod, q in comp.items():
            bom_rows.append((pid, mat_id[cod], q, 0.02 if cod.startswith("MP") else 0.01))
    con.executemany("INSERT INTO bom_producto VALUES (?,?,?,?)", bom_rows)
    for pm, comp in BOM_PREMEZCLA.items():
        for cod, q in comp.items():
            con.execute("INSERT INTO bom_premezcla VALUES (?,?,?)", (mat_id[pm], mat_id[cod], q))

    # Explosión de la semana semilla -> consumo semanal por material (para dimensionar inventario/OC)
    need_seed: dict[int, float] = {}
    for k, pid, flujo, fuente, q in nec_rows:
        if k == 1 and fuente != "archivo_estandar":
            need_seed[pid] = need_seed.get(pid, 0) + q
    consumo: dict[str, float] = {m[0]: 0.0 for m in MATERIALES}
    for pid, q in need_seed.items():
        for _, cod, cant, merma in [(r[0], next(c for c, i in mat_id.items() if i == r[1]), r[2], r[3])
                                    for r in bom_rows if r[0] == pid]:
            consumo[cod] += q * cant * (1 + merma)
    for pm, comp in BOM_PREMEZCLA.items():
        kg_pm = consumo[pm]
        for cod, q in comp.items():
            consumo[cod] += kg_pm * q
    control["consumo_semanal_material"] = {k: round(v, 1) for k, v in consumo.items()}

    # Proveedores
    prov_id = {}
    for i, (nom, origen, mu, sd, fill) in enumerate(PROVEEDORES, start=1):
        prov_id[nom] = i
        con.execute("INSERT INTO proveedor VALUES (?,?,?)", (i, nom, origen))
    locales = [p for p in PROVEEDORES if p[1] == "local" and p[0] != PV_INCUMPLIDO]
    import_ = [p for p in PROVEEDORES if p[1] == "importado"]
    mat_prov: dict[str, list[tuple[str, int, int]]] = {}    # material -> [(prov, cuota, lead_time)]
    for cod, nom, tipo, un, costo, ss in MATERIALES:
        if tipo == "premezcla":
            continue
        pool = import_ if cod in IMPORTADOS else locales
        lt = rng.randint(28, 42) if cod in IMPORTADOS else rng.randint(3, 10)
        if cod == "MP-002":
            lista = [(PV_INCUMPLIDO, 60, 6), ("Proveedor 02", 40, 6)]
        elif cod in ("MP-003", "EM-004"):
            lista = [(PV_INCUMPLIDO, 100, 4)]
        elif rng.random() < .55:
            a, b = rng.sample(pool, 2)
            cuota = rng.choice([(60, 40), (60, 40), (70, 30), (50, 50)])
            lista = [(a[0], cuota[0], lt), (b[0], cuota[1], lt + rng.randint(-1, 2))]
        else:
            lista = [(rng.choice(pool)[0], 100, lt)]
        mat_prov[cod] = lista
        for pv, cuota, l in lista:
            moq = 500.0 if un == "kg" else 5000.0
            mult = 25.0 if un == "kg" else 500.0
            con.execute("INSERT INTO material_proveedor VALUES (?,?,?,?,?,?)",
                        (mat_id[cod], prov_id[pv], cuota, l, moq, mult))

    # Inventario al corte (8am del lunes). Cobertura relativa a lead time salvo casos forzados.
    corte = f"{iso(HOY)}T08:00"
    stock: dict[str, float] = {}
    for cod, nom, tipo, un, costo, ss in MATERIALES:
        diario = consumo[cod] / 7
        if cod in COBERTURA_FORZADA:
            cob = COBERTURA_FORZADA[cod]
        elif tipo == "premezcla":
            cob = rng.uniform(1.5, 4)
        else:
            lt_max = max(l for _, _, l in mat_prov[cod])
            cob = rng.uniform(.9, 2.2) * lt_max + ss * rng.uniform(.4, 1.0)
        stock[cod] = round(diario * cob, 1)
        con.execute("INSERT INTO inventario_material VALUES (?,?,?)", (mat_id[cod], stock[cod], corte))
    control["cobertura_forzada_dias"] = COBERTURA_FORZADA

    # Inventario de producto terminado (0,5 a 2 días de necesidad)
    for pid, q in need_seed.items():
        con.execute("INSERT INTO inventario_producto VALUES (?,?,?)",
                    (pid, redondear(q / 7 * rng.uniform(.5, 2), 10), corte))
    for pid, (lc, nom, part, *_r) in enumerate(SKUS, start=1):
        if pid not in need_seed:
            con.execute("INSERT INTO inventario_producto VALUES (?,?,?)", (pid, 0, corte))

    # Movimientos de 1 mes (materiales y producto)
    mov = []
    for cod in consumo:
        for d in range(30, 0, -1):
            f = HOY - timedelta(days=d)
            mov.append((iso(f), mat_id[cod], None, "consumo", round(-consumo[cod] / 7 * rng.uniform(.75, 1.25), 1)))
            if consumo[cod] and rng.random() < .13:
                mov.append((iso(f), mat_id[cod], None, "recibo", round(consumo[cod] / 7 * rng.uniform(4, 10), 1)))
    for pid, q in need_seed.items():
        for d in range(30, 0, -1):
            f = HOY - timedelta(days=d)
            mov.append((iso(f), None, pid, "produccion", round(q / 7 * rng.uniform(.8, 1.2), 0)))
            mov.append((iso(f), None, pid, "despacho", round(-q / 7 * rng.uniform(.8, 1.2), 0)))
    con.executemany("INSERT INTO movimiento_inventario(fecha,material_id,producto_id,tipo,cantidad) VALUES (?,?,?,?,?)", mov)

    # Fuente 2: triangulación zona franca (con movimientos contados dos veces)
    tri = []
    for n in range(36):
        pid = rng.choice([pid_cd, pid_ex])
        f = HOY - timedelta(days=rng.randint(1, 20))
        q = redondear(rng.uniform(20_000, 80_000), 1000)
        doc = f"TRI-{100200 + n}"
        tri.append((iso(f), pid, "Bodega producción", "Recibo logística", q, doc))
        tri.append((iso(f + timedelta(days=1)), pid, "Recibo logística", "Área customer", q, doc + "-B"))
    dupes = rng.sample(tri, 6)
    tri.extend(dupes)
    control["triangulacion_duplicados"] = [d[5] for d in dupes]
    con.executemany("INSERT INTO movimiento_triangulacion(fecha,producto_id,origen,destino,cantidad,documento) VALUES (?,?,?,?,?,?)", tri)

    # Fuente 3: trazabilidad de despachos
    for pid, q in need_seed.items():
        for ventana, frac in (("8am-2pm", rng.uniform(.04, .09)), ("post-2pm", rng.uniform(.02, .06))):
            con.execute("INSERT INTO trazabilidad_despacho VALUES (?,?,?)", (pid, ventana, redondear(q * frac, 10)))

    # Historial de entregas (3 años) y comportamiento real por proveedor
    comport = {p[0]: p for p in PROVEEDORES}
    hist_ent = []
    for cod, lista in mat_prov.items():
        for pv, cuota, lt in lista:
            _, _, mu, sd, fill = comport[pv]
            f = date(2023, 10, 2) + timedelta(days=rng.randint(0, 40))
            while f < HOY - timedelta(days=lt + 6):
                prometida = f + timedelta(days=lt)
                atraso = max(-2, min(40, round(rng.gauss(mu, sd))))
                pedido = consumo[cod] * rng.uniform(2, 4) * cuota / 100
                pedido = max(500.0, round(pedido / 25) * 25)
                recibida = round(pedido * min(1.0, max(.6, rng.gauss(fill, .03))), 1)
                hist_ent.append((mat_id[cod], prov_id[pv], iso(f), iso(prometida), iso(prometida + timedelta(days=atraso)),
                                 pedido, recibida))
                f += timedelta(days=int(rng.uniform(30, 55)))
    con.executemany("INSERT INTO historial_entrega(material_id,proveedor_id,fecha_oc,fecha_prometida,fecha_recepcion,"
                    "cantidad_pedida,cantidad_recibida) VALUES (?,?,?,?,?,?,?)", hist_ent)

    # Órdenes de compra abiertas (con suciedad sembrada)
    ocs: list[dict] = []
    n_oc = 0

    def nueva_oc(cod, pv, q, emision, prometida, estado="abierta"):
        nonlocal n_oc
        n_oc += 1
        o = dict(numero=f"OC-{450000 + n_oc}", m=mat_id[cod], p=prov_id[pv], q=q, e=emision, pr=prometida, est=estado, cod=cod)
        ocs.append(o)
        return o

    for cod, lista in mat_prov.items():
        for pv, cuota, lt in lista:
            if cod == "MP-002":                                       # relato de la demo: solo la OC del proveedor incumplido
                if pv == PV_INCUMPLIDO:
                    nueva_oc(cod, pv, 6000.0, HOY, HOY + timedelta(days=6))   # llega tarde
                continue
            if rng.random() < .55 or (cod, pv) == ("MP-018", mat_prov["MP-018"][0][0]):
                mult = 25.0
                q = max(500.0, round(consumo[cod] * rng.uniform(1.5, 3.5) * cuota / 100 / mult) * mult)
                pr = HOY + timedelta(days=rng.randint(1, 28))
                nueva_oc(cod, pv, q, pr - timedelta(days=lt), pr)
    # incumplidas (vencidas y aún abiertas)
    mats_inc = [c for c, l in mat_prov.items() if any(p == PV_INCUMPLIDO for p, _, _ in l) and c not in ("MP-002",)]
    vencidas = []
    for cod in mats_inc[:2]:
        lt = next(l for p, _, l in mat_prov[cod] if p == PV_INCUMPLIDO)
        pr = HOY - timedelta(days=rng.randint(5, 9))
        vencidas.append(nueva_oc(cod, PV_INCUMPLIDO, max(500.0, round(consumo[cod] * 2 / 25) * 25), pr - timedelta(days=lt), pr)["numero"])
    control["oc_vencidas_proveedor_incumplido"] = vencidas
    # duplicadas
    base_oc = [o for o in ocs if o["cod"] not in ("MP-002",)]
    dups = []
    for o in rng.sample(base_oc, 3):
        d = nueva_oc(o["cod"], next(n for n, i in prov_id.items() if i == o["p"]), o["q"],
                     o["e"] + timedelta(days=1), o["pr"])
        dups.append([o["numero"], d["numero"]])
    control["oc_duplicadas"] = dups
    # obsoletas
    obs = []
    for cod in rng.sample([c for c in mat_prov if c not in COBERTURA_FORZADA], 5):
        pv, _, lt = mat_prov[cod][0]
        pr = HOY - timedelta(days=rng.randint(130, 180))
        obs.append(nueva_oc(cod, pv, max(500.0, round(consumo[cod] / 25) * 25), pr - timedelta(days=lt), pr)["numero"])
    control["oc_obsoletas"] = obs
    con.executemany("INSERT INTO orden_compra(id,numero,material_id,proveedor_id,cantidad,fecha_emision,"
                    "fecha_entrega_prometida,estado) VALUES (?,?,?,?,?,?,?,?)",
                    [(i, o["numero"], o["m"], o["p"], o["q"], iso(o["e"]), iso(o["pr"]), o["est"])
                     for i, o in enumerate(ocs, start=1)])

    # SolPed: normales + duplicadas de OC + cantidades fuera de rango
    sps = []
    n_sp = 0

    def nueva_sp(cod, q, f):
        nonlocal n_sp
        n_sp += 1
        num = f"SP-{700000 + n_sp}"
        sps.append((n_sp, num, mat_id[cod], q, iso(f), "abierta"))
        return num

    for cod in rng.sample(list(mat_prov), 20):
        nueva_sp(cod, max(500.0, round(consumo[cod] * rng.uniform(1, 2) / 25) * 25), HOY - timedelta(days=rng.randint(0, 6)))
    sp_dup = []
    for o in rng.sample([o for o in ocs if o["est"] == "abierta" and o["pr"] >= HOY and o["cod"] != "MP-002"], 4):
        sp_dup.append([o["numero"], nueva_sp(o["cod"], o["q"], HOY - timedelta(days=rng.randint(0, 3)))])
    control["solped_duplica_oc"] = sp_dup
    fuera = [nueva_sp(cod, round(consumo[cod] * 12 / 25) * 25, HOY - timedelta(days=1))
             for cod in rng.sample([c for c in mat_prov if consumo[c] > 0], 3)]
    control["solped_fuera_de_rango"] = fuera
    con.executemany("INSERT INTO solped VALUES (?,?,?,?,?,?)", sps)

    # Órdenes de producción (algunas obsoletas abiertas que contaminan el MRP)
    ops = []
    for pid in rng.sample(list(need_seed), 8):
        lc = linea_de_producto[pid]
        ops.append((pid, linea_id[lc], redondear(need_seed[pid] * .1, paso(lc)), iso(HOY - timedelta(days=rng.randint(1, 4))), "firme", "abierta"))
    obs_op = []
    for pid in rng.sample(list(need_seed), 3):
        lc = linea_de_producto[pid]
        ops.append((pid, linea_id[lc], redondear(need_seed[pid] * .2, paso(lc)), iso(HOY - timedelta(days=rng.randint(90, 200))), "provisional", "abierta"))
        obs_op.append(pid)
    control["op_obsoletas_abiertas_producto_ids"] = obs_op
    con.executemany("INSERT INTO orden_produccion(producto_id,linea_id,cantidad,fecha_inicio,tipo,estado) VALUES (?,?,?,?,?,?)", ops)

    # Valores esperados del escenario semilla (los verifican los tests)
    control["escenario_semilla"] = {
        "necesidad_total": {"Ponqué Individual": 3_400_000, "Barra Rellena": 1_920_000, "Mini Ponqué": 1_600_000,
                            "Galleta Extruida": 55_000, "Snack Horneado": 4_800, "Tostada Horneada": 23_000,
                            "Maicito Extruido": 40_000},
        "conjunto_barra_mini": {"necesidad": 3_520_000, "capacidad": 3_290_000, "excedente": 230_000, "horas_extra": 8.5},
    }
    for k, v in control.items():
        con.execute("INSERT INTO _synth_control VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
    con.commit()
    resumen = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
    con.close()
    return resumen


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=ROOT / "ramo.db")
    a = ap.parse_args()
    resumen = generar(a.out, a.seed)
    print(f"Dataset sintético generado en {a.out} (semilla {a.seed})")
    for t, n in resumen.items():
        print(f"  {t:28s} {n:>7d}")


if __name__ == "__main__":
    main()
