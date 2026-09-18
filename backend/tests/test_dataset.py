"""Valida que el dataset sintético reproduce el escenario semilla y trae la suciedad sembrada."""
import json
import sqlite3

import pytest

from backend.synth.generate import generar


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "t.db"
    generar(db, seed=42)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def control(con, clave):
    return json.loads(con.execute("SELECT valor FROM _synth_control WHERE clave=?", (clave,)).fetchone()[0])


def necesidad_linea(con, semana=1):
    """Necesidad total por línea usando modelo_drp + origen_pedido (nunca el archivo estándar)."""
    q = """SELECT l.nombre, SUM(n.cantidad) t FROM necesidad_semana n
           JOIN producto p ON p.id=n.producto_id JOIN linea l ON l.id=p.linea_id
           WHERE n.semana_id=? AND n.fuente!='archivo_estandar' GROUP BY l.nombre"""
    return {r["nombre"]: r["t"] for r in con.execute(q, (semana,))}


def horas_disponibles(con, linea, semana=1):
    r = con.execute("""SELECT l.horas_brutas_semana - COALESCE(SUM(e.horas),0) h, l.ritmo_actual r
                       FROM linea l LEFT JOIN evento_calendario e ON e.linea_id=l.id AND e.semana_id=?
                       WHERE l.nombre=? GROUP BY l.id""", (semana, linea)).fetchone()
    return r["h"], r["r"]


def test_necesidad_semilla_coincide_con_el_mockup(con):
    esperado = control(con, "escenario_semilla")["necesidad_total"]
    assert necesidad_linea(con) == {k: pytest.approx(v) for k, v in esperado.items()}


def test_saturaciones_del_mockup(con):
    nec = necesidad_linea(con)
    sat = {}
    for linea in nec:
        h, r = horas_disponibles(con, linea)
        sat[linea] = nec[linea] / (h * r)
    assert round(sat["Ponqué Individual"] * 100) == 96
    assert round(sat["Barra Rellena"] * 100) == 137
    assert round(sat["Mini Ponqué"] * 100) == 85
    assert round(sat["Galleta Extruida"] * 100) == 89
    assert round(sat["Snack Horneado"] * 100) == 90
    # las demás líneas quedan holgadas
    assert sat["Tostada Horneada"] < .9 and sat["Maicito Extruido"] < .9


def test_horas_disponibles_descuentan_mantenimiento(con):
    assert horas_disponibles(con, "Galleta Extruida")[0] == 100    # 112 - 12
    assert horas_disponibles(con, "Snack Horneado")[0] == 76       # 84 - 8
    assert horas_disponibles(con, "Ponqué Individual")[0] == 126


def test_conjunto_barra_mini_da_8_5_horas_extra(con):
    nec = necesidad_linea(con)
    cap = 0
    for l in ("Barra Rellena", "Mini Ponqué"):
        h, r = horas_disponibles(con, l)
        cap += h * r
    excedente = nec["Barra Rellena"] + nec["Mini Ponqué"] - cap
    assert cap == 3_290_000 and excedente == 230_000
    assert round(excedente / 27_000, 1) == 8.5


def test_tripulaciones_compartidas(con):
    q = """SELECT g.nombre, GROUP_CONCAT(l.nombre) ls FROM linea_grupo lg
           JOIN grupo_tripulacion g ON g.id=lg.grupo_id JOIN linea l ON l.id=lg.linea_id GROUP BY g.id"""
    grupos = [set(r["ls"].split(",")) for r in con.execute(q)]
    assert {"Barra Rellena", "Mini Ponqué"} in grupos
    assert {"Tostada Horneada", "Maicito Extruido"} in grupos


def test_mini_ponque_tiene_ritmo_anterior(con):
    r = con.execute("SELECT ritmo_actual, ritmo_anterior FROM linea WHERE nombre='Mini Ponqué'").fetchone()
    assert (r["ritmo_actual"], r["ritmo_anterior"]) == (27000, 15000)


def test_archivo_estandar_trae_basura_y_origen_es_limpio(con):
    q = """SELECT n.fuente, SUM(n.cantidad) t FROM necesidad_semana n WHERE n.semana_id=1
           AND n.flujo='canal_directo_mto' GROUP BY n.fuente"""
    r = {x["fuente"]: x["t"] for x in con.execute(q)}
    assert r["origen_pedido"] == 170_000 and r["archivo_estandar"] == 340_000      # duplicado
    q = """SELECT n.fuente, SUM(n.cantidad) t FROM necesidad_semana n WHERE n.semana_id=1
           AND n.flujo='exportacion_mto' GROUP BY n.fuente"""
    r = {x["fuente"]: x["t"] for x in con.execute(q)}
    assert r["origen_pedido"] == 100_000 and r["archivo_estandar"] == 0            # faltante


def test_horizonte_y_calendario(con):
    assert con.execute("SELECT COUNT(*) FROM semana").fetchone()[0] == 13
    assert con.execute("SELECT COUNT(*) FROM semana WHERE es_semilla=1").fetchone()[0] == 1
    tipos = {r[0] for r in con.execute("SELECT DISTINCT tipo FROM evento_calendario")}
    assert tipos == {"mantenimiento", "parada", "festivo"}
    p = con.execute("SELECT avisado_dias_antes FROM evento_calendario WHERE tipo='parada'").fetchone()
    assert p[0] == 3                                                               # avisada con poca anticipación


def test_bom_completo_y_sin_huerfanos(con):
    sin_bom = con.execute("""SELECT COUNT(*) FROM producto p
                             WHERE NOT EXISTS (SELECT 1 FROM bom_producto b WHERE b.producto_id=p.id)""").fetchone()[0]
    assert sin_bom == 0
    assert con.execute("SELECT COUNT(*) FROM bom_premezcla").fetchone()[0] > 0
    con.execute("PRAGMA foreign_keys=ON")
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proveedores_con_cuota_suman_100(con):
    for r in con.execute("SELECT material_id, SUM(cuota_pct) s FROM material_proveedor GROUP BY material_id"):
        assert r["s"] == 100
    assert con.execute("SELECT COUNT(*) FROM (SELECT material_id FROM material_proveedor "
                       "GROUP BY material_id HAVING COUNT(*)>1)").fetchone()[0] >= 5


def test_azucar_con_cobertura_de_4_dias_y_oc_tardia(con):
    consumo = control(con, "consumo_semanal_material")["MP-002"]
    stock = con.execute("""SELECT i.cantidad FROM inventario_material i JOIN material m ON m.id=i.material_id
                           WHERE m.codigo='MP-002'""").fetchone()[0]
    assert stock / (consumo / 7) == pytest.approx(4.0, abs=0.05)
    oc = con.execute("""SELECT o.fecha_entrega_prometida FROM orden_compra o JOIN material m ON m.id=o.material_id
                        JOIN proveedor p ON p.id=o.proveedor_id
                        WHERE m.codigo='MP-002' AND p.nombre='Proveedor 07'""").fetchone()[0]
    assert oc == "2026-09-27"


def test_lead_time_real_del_proveedor_incumplido_supera_al_nominal(con):
    q = """SELECT p.nombre,
                  AVG(julianday(h.fecha_recepcion)-julianday(h.fecha_prometida)) atraso, COUNT(*) n
           FROM historial_entrega h JOIN proveedor p ON p.id=h.proveedor_id GROUP BY p.id"""
    atraso = {r["nombre"]: r["atraso"] for r in con.execute(q)}
    assert atraso["Proveedor 07"] > 6
    assert min(atraso.values()) < 2
    assert con.execute("SELECT COUNT(*) FROM historial_entrega").fetchone()[0] > 800


def test_suciedad_sembrada_esta_presente(con):
    for oc_a, oc_b in control(con, "oc_duplicadas"):
        a, b = (con.execute("SELECT material_id, proveedor_id, cantidad FROM orden_compra WHERE numero=?", (n,)).fetchone()
                for n in (oc_a, oc_b))
        assert tuple(a) == tuple(b)
    for n in control(con, "oc_obsoletas"):
        f, est = con.execute("SELECT fecha_entrega_prometida, estado FROM orden_compra WHERE numero=?", (n,)).fetchone()
        assert est == "abierta" and f < "2026-06-01"
    assert len(control(con, "oc_vencidas_proveedor_incumplido")) >= 2
    assert len(control(con, "solped_duplica_oc")) == 4 and len(control(con, "solped_fuera_de_rango")) == 3
    assert len(control(con, "triangulacion_duplicados")) == 6
    obsoletas = con.execute("SELECT COUNT(*) FROM orden_produccion WHERE tipo='provisional' AND estado='abierta' "
                            "AND fecha_inicio < '2026-07-01'").fetchone()[0]
    assert obsoletas == 3


def test_historial_necesidad_2_anios_por_sku_regular(con):
    n = con.execute("SELECT COUNT(DISTINCT producto_id) FROM historial_necesidad").fetchone()[0]
    assert n == 19                                                                 # 21 SKUs - 2 MTO
    assert con.execute("SELECT COUNT(*) FROM historial_necesidad WHERE producto_id=1").fetchone()[0] == 104


def test_tablas_de_estado_arrancan_vacias(con):
    for t in ("ciclo", "plan_version", "plan_version_linea", "ajuste_distribucion", "propuesta_ia", "auditoria"):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_reproducible_misma_semilla(tmp_path):
    def huella(seed):
        db = tmp_path / f"{seed}.db"
        generar(db, seed=seed)
        c = sqlite3.connect(db)
        h = [c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
             for t in ("necesidad_semana", "orden_compra", "historial_entrega", "inventario_material")]
        c.close()
        return h
    assert huella(42) == huella(42)
    assert huella(42) != huella(7)


def test_conflicto_de_barra_es_de_la_semilla_no_cronico(con):
    """Barra pica en la semana semilla (137 %) pero su mediana en el horizonte es manejable."""
    sat = []
    for w in range(1, 14):
        nec = sum(r[0] for r in con.execute(
            """SELECT n.cantidad FROM necesidad_semana n JOIN producto p ON p.id=n.producto_id
               JOIN linea l ON l.id=p.linea_id WHERE l.nombre='Barra Rellena' AND n.semana_id=?
               AND n.fuente!='archivo_estandar'""", (w,)))
        h, r = horas_disponibles(con, "Barra Rellena", w)
        sat.append(nec / (h * r))
    assert sat[0] > 1.3 and sorted(sat)[6] < 1.10
