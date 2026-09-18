"""MRP + capa IA: explosión, proyección, relato del azúcar, anomalías sembradas y gobernanza de propuestas."""
import datetime
import json

import pytest

from backend.engine import ciclo as cy
from backend.engine import datos, ia, mrp
from backend.engine import propuestas as pr

CAP, INV, GER, ANA = cy.CAPACIDAD, cy.INVENTARIOS, cy.GERENCIA, cy.ANALISTA


def control(con, clave):
    return json.loads(con.execute("SELECT valor FROM _synth_control WHERE clave=?", (clave,)).fetchone()[0])


def plan_oficial(con, ajuste=None):
    """Recorre el ciclo hasta dejar un plan oficial aprobado."""
    cid = cy.iniciar_ciclo(con, datos.semana_semilla(con))
    cy.ejecutar_crp(con, cid, CAP)
    cy.compartir_plan(con, cid, CAP)
    if ajuste:
        pid = con.execute("SELECT id FROM producto WHERE nombre=?", (ajuste[0],)).fetchone()[0]
        a = cy.proponer_ajuste(con, cid, INV, pid, ajuste[1], "riesgo_rotacion", "test")
        cy.decidir_ajuste(con, cid, INV, a, True, "ok")
        cy.consolidar(con, cid, INV)
    else:
        cy.abrir_decisiones(con, cid, INV)
    cy.aprobar_plan(con, cid, GER, "ok", acepta_horas_extra=True)
    return cid


def mat(res, codigo):
    return next(m for m in res["materiales"] if m["codigo"] == codigo)


# ───────────────────────── Explosión y proyección ─────────────────────────
def test_la_explosion_coincide_con_el_consumo_del_generador(con):
    plan, _ = mrp.plan_a_explotar(con, "simulacion")
    ctx = mrp.cargar_contexto(con, plan)
    _, expl = mrp.consumo_diario(ctx)
    esperado = control(con, "consumo_semanal_material")
    for mid, m in ctx["mat"].items():
        assert expl.get(mid, {"total": 0.0})["total"] == pytest.approx(esperado[m["codigo"]], rel=1e-3, abs=1)


def test_la_explosion_atraviesa_premezclas(con):
    plan, _ = mrp.plan_a_explotar(con, "simulacion")
    ctx = mrp.cargar_contexto(con, plan)
    _, expl = mrp.consumo_diario(ctx)
    harina = next(i for i, m in ctx["mat"].items() if m["codigo"] == "MP-001")
    assert expl[harina]["via_premezcla"], "la harina de trigo llega también a través de las premezclas"
    assert sum(expl[harina]["por_producto"].values()) == pytest.approx(expl[harina]["total"])


def test_proyeccion_y_quiebre_fraccionario():
    fin, q = mrp.proyectar(40, [10] * 10, {})
    assert q == pytest.approx(4.0)
    fin, q = mrp.proyectar(40, [10] * 10, {6: 100})              # llega la OC pero ya hubo quiebre antes
    assert q == pytest.approx(4.0) and fin[-1] > 0
    fin, q = mrp.proyectar(45, [10] * 10, {6: 100})
    assert q == pytest.approx(4.5)
    fin, q = mrp.proyectar(500, [10] * 10, {})
    assert q is None and fin[-1] == 400


def test_la_orden_respeta_moq_multiplo_y_lead_time():
    h = datetime.date(2026, 9, 21)
    fin, _ = mrp.proyectar(100, [10] * 28, {})
    o = mrp.proponer_orden(fin, [10] * 28, 20, 5, 500, 25, h)
    assert o["dia_necesidad"] == 8 and o["fecha_pedido"] == "2026-09-24" and not o["retrasado"]
    assert o["cantidad"] >= 500 and o["cantidad"] % 25 == 0
    fin, _ = mrp.proyectar(30, [10] * 28, {})
    o = mrp.proponer_orden(fin, [10] * 28, 0, 10, 100, 10, h)
    assert o["retrasado"] and o["fecha_pedido"] == "2026-09-21" and o["dias_retraso"] > 0


# ───────────────────────── Modos ─────────────────────────
def test_el_modo_oficial_exige_plan_aprobado_y_la_simulacion_no(con):
    with pytest.raises(mrp.MrpError):
        ia.correr(con, "oficial")
    r = ia.correr(con, "simulacion")
    assert not r["meta"]["es_oficial"] and r["meta"]["origen"] == "necesidad_sin_crp"
    cid = cy.iniciar_ciclo(con, datos.semana_semilla(con))
    cy.ejecutar_crp(con, cid, CAP)
    r = ia.correr(con, "simulacion")
    assert r["meta"]["origen"] == "crp" and not r["meta"]["es_oficial"]
    with pytest.raises(mrp.MrpError):
        ia.correr(con, "oficial")


def test_el_plan_oficial_con_ajuste_reduce_el_consumo(con):
    base = ia.correr(con, "simulacion")
    plan_oficial(con, ("Ponqué Individual Vainilla 40 g", -120_000))
    of = ia.correr(con, "oficial")
    assert of["meta"]["es_oficial"] and of["meta"]["origen"] == "final"
    assert mat(of, "MP-002")["consumo_semana"] < mat(base, "MP-002")["consumo_semana"]


# ───────────────────────── El relato del azúcar ─────────────────────────
def test_azucar_quiebre_en_4_dias_y_oc_que_llega_tarde(con):
    r = ia.correr(con, "simulacion")
    az = mat(r, "MP-002")
    assert az["cobertura_dias"] == pytest.approx(4.0, abs=0.1)
    assert az["sap"]["quiebre_dia"] == pytest.approx(4.0, abs=0.1)
    assert az["sap"]["orden"]["lead_time"] == 6 and az["ia"]["lead_time"] > 12
    assert az["ia"]["proxima_oc"]["dia_llegada"] > 12 > az["ia"]["quiebre_dia"]
    assert az["zona"] == "rojo" and az["prioridad"] == "critico"
    assert az["ia"]["orden"]["retrasado"]


def test_azucar_rompe_la_cuota_por_riesgo_de_lead_time_y_queda_explicado(con):
    az = mat(ia.correr(con, "simulacion"), "MP-002")
    exc = az["ia"]["excepcion_cuota"]
    assert exc and exc["proveedor_preferido"] == "Proveedor 02"
    assert [x["cuota_pct"] for x in exc["cuota_original"]] == [60.0, 40.0]
    assert [l["proveedor"] for l in az["ia"]["orden"]["reparto"]] == ["Proveedor 02"]
    assert any("cuota" in m for m in az["ia"]["motivos"]) and any("Lead time real" in m for m in az["ia"]["motivos"])


def test_sin_riesgo_de_lead_time_la_ia_respeta_la_cuota(con):
    r = ia.correr(con, "simulacion")
    con_reparto = [m for m in r["materiales"] if m["ia"]["orden"] and len(m["proveedores"]) > 1 and not m["ia"]["excepcion_cuota"]]
    assert con_reparto, "debe haber materiales con dos proveedores repartidos por cuota"
    for m in con_reparto:
        o = m["ia"]["orden"]
        assert sum(l["cantidad"] for l in o["reparto"]) == pytest.approx(o["cantidad"])
        assert o["reparto"][0]["cuota_pct"] == m["proveedores"][0]["cuota_pct"]


def test_hay_quiebres_que_sap_no_ve(con):
    """SAP los esconde por OC sucias (aceite de palma) o por confiar en fechas prometidas que el proveedor no cumple (saborizante)."""
    r = ia.correr(con, "simulacion")
    assert set(r["resumen"]["quiebres_ocultos_por_sap"]) >= {"MP-016", "MP-018"}
    for cod in ("MP-016", "MP-018"):
        m = mat(r, cod)
        assert m["sap"]["quiebre_dia"] is None and m["ia"]["quiebre_dia"] is not None
    assert any("OC sucias" in x for x in mat(r, "MP-016")["ia"]["motivos"])
    assert any("atraso histórico" in x for x in mat(r, "MP-018")["ia"]["motivos"])
    assert r["resumen"]["quiebre_ia"] > r["resumen"]["quiebre_sap"]


# ───────────────────────── Anomalías ─────────────────────────
def test_detecta_toda_la_suciedad_sembrada(con):
    r = ia.correr(con, "simulacion")
    por_tipo = {}
    for a in r["anomalias"]:
        por_tipo.setdefault(a["tipo"], []).append(a["entidad"])
    dups = {b for _, b in control(con, "oc_duplicadas")}
    assert set(por_tipo["oc_duplicada"]) == dups
    assert set(por_tipo["oc_obsoleta"]) == set(control(con, "oc_obsoletas"))
    assert set(control(con, "oc_vencidas_proveedor_incumplido")) <= set(por_tipo["oc_vencida"])
    assert set(por_tipo["solped_duplica_oc"]) == {sp for _, sp in control(con, "solped_duplica_oc")}
    assert set(por_tipo["solped_fuera_de_rango"]) == set(control(con, "solped_fuera_de_rango"))
    assert len(por_tipo["triangulacion_duplicada"]) == len(control(con, "triangulacion_duplicados"))
    assert len(por_tipo["op_obsoleta"]) == 3
    assert len(r["excluidas"]) == len(dups) + 5


def test_las_solped_normales_no_se_marcan(con):
    r = ia.correr(con, "simulacion")
    marcadas = {a["entidad"] for a in r["anomalias"] if a["tipo"].startswith("solped")}
    total = con.execute("SELECT COUNT(*) FROM solped").fetchone()[0]
    assert len(marcadas) == 7 and total == 27


# ───────────────────────── Lead times, prioridad y consolidación ─────────────────────────
def test_lead_times_dinamicos_del_proveedor_incumplido(con):
    lt = {(l["codigo"], l["proveedor"]): l for l in ia.correr(con, "simulacion")["lead_times"]}
    a = lt[("MP-002", "Proveedor 07")]
    assert a["nominal"] == 6 and a["sugerido"] >= 14 and a["actualizar"] and a["n"] >= 20
    buenos = [l for (c, p), l in lt.items() if p in ("Proveedor 01", "Proveedor 03")]
    assert buenos and all(not l["actualizar"] for l in buenos)


def test_prioridades_validas_y_criticos_primero(con):
    r = ia.correr(con, "simulacion")
    assert {m["prioridad"] for m in r["materiales"]} <= {"critico", "normal", "puede_esperar", None}
    assert r["resumen"]["criticas"] >= 3
    assert r["materiales"][0]["zona"] == "rojo"
    for m in r["materiales"]:
        if m["prioridad"] == "critico" and m["ia"]["quiebre_dia"] is not None:
            assert m["ia"]["quiebre_dia"] < ia.UMBRAL_CRITICO_DIAS or m["ia"]["orden"]["retrasado"]


def test_consolidacion_agrupa_por_proveedor_y_semana(con):
    r = ia.correr(con, "simulacion")
    assert r["consolidaciones"]
    for g in r["consolidaciones"]:
        assert len(g["lineas"]) >= 2 and all(l["proveedor"] == g["proveedor"] for l in g["lineas"])
    marcado = [m for m in r["materiales"] if m["ia"]["consolidacion"]]
    assert marcado and all(m["ia"]["consolidacion"]["n_lineas"] >= 2 for m in marcado)


def test_el_resultado_es_serializable_a_json(con):
    json.dumps(ia.correr(con, "simulacion"))


# ───────────────────────── Gobernanza de propuestas ─────────────────────────
def test_generar_y_listar_propuestas(con):
    cid = plan_oficial(con)
    with pytest.raises(cy.PermisoError):
        pr.generar_propuestas(con, cid, CAP)
    g = pr.generar_propuestas(con, cid, ANA)
    assert g["meta"]["es_oficial"] and g["nuevas"] > 20
    props = pr.listar_propuestas(con, cid)
    assert {p["tipo"] for p in props} == {"orden_reposicion", "cancelar_oc", "actualizar_lead_time"}
    assert props[0]["prioridad"] == "critico" and all(p["estado"] == "pendiente" and not p["es_simulacion"] for p in props)
    assert sum(1 for p in props if p["tipo"] == "cancelar_oc") == 8            # 3 duplicadas + 5 obsoletas


def test_aprobar_la_orden_de_azucar_exige_justificacion_por_romper_la_cuota(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    az = next(p for p in pr.listar_propuestas(con, cid)
              if p["tipo"] == "orden_reposicion" and p["detalle"]["material"]["codigo"] == "MP-002")
    with pytest.raises(cy.CicloError, match="cuota"):
        pr.decidir_propuesta(con, cid, ANA, az["id"], "aprobar")
    r = pr.decidir_propuesta(con, cid, ANA, az["id"], "aprobar", "Proveedor 07 no llega antes del quiebre")
    assert r == {"estado": "aprobada", "cuota_rota": True}
    log = [a for a in cy.estado_ciclo(con, cid)["auditoria"] if a["accion"] == "propuesta_aprobada"]
    d = json.loads(log[0]["detalle"])
    assert d["cuota_rota"] is True and "Proveedor 07" in d["justificacion"]
    with pytest.raises(cy.EstadoError, match="ya fue"):
        pr.decidir_propuesta(con, cid, ANA, az["id"], "rechazar", "x")


def test_modificar_y_rechazar_requieren_justificacion_y_validan(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    ordenes = [p for p in pr.listar_propuestas(con, cid)
               if p["tipo"] == "orden_reposicion" and not p["detalle"]["ia"]["excepcion_cuota"]]
    p = ordenes[0]
    with pytest.raises(cy.CicloError, match="justificación"):
        pr.decidir_propuesta(con, cid, ANA, p["id"], "rechazar", "  ")
    with pytest.raises(cy.CicloError, match="justificación"):
        pr.decidir_propuesta(con, cid, ANA, p["id"], "modificar", "", {"cantidad": 1000})
    with pytest.raises(cy.CicloError, match="múltiplo"):
        pr.decidir_propuesta(con, cid, ANA, p["id"], "modificar", "ajuste", {"cantidad": 12345.5})
    with pytest.raises(cy.CicloError, match="mayor"):
        pr.decidir_propuesta(con, cid, ANA, p["id"], "modificar", "ajuste", {"cantidad": -5})
    orig = p["detalle"]["ia"]["orden"]["cantidad"]
    nueva = orig + 500
    r = pr.decidir_propuesta(con, cid, ANA, p["id"], "modificar", "Compra parcial por espacio en bodega", {"cantidad": nueva})
    assert r["estado"] == "modificada"
    fin = next(x for x in pr.listar_propuestas(con, cid) if x["id"] == p["id"])["detalle_final"]
    assert fin["cantidad"] == nueva and sum(l["cantidad"] for l in fin["reparto"]) == pytest.approx(nueva)
    otra = ordenes[1]
    assert pr.decidir_propuesta(con, cid, ANA, otra["id"], "rechazar", "Ya hay compra directa")["estado"] == "rechazada"


def test_modificar_a_un_solo_proveedor_marca_la_cuota_rota(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    doble = next(p for p in pr.listar_propuestas(con, cid)
                 if p["tipo"] == "orden_reposicion" and len(p["detalle"]["ia"]["orden"]["reparto"]) == 2)
    alt = doble["detalle"]["ia"]["orden"]["reparto"][1]["proveedor_id"]
    r = pr.decidir_propuesta(con, cid, ANA, doble["id"], "modificar", "Negociación puntual con el segundo proveedor",
                             {"cantidad": doble["detalle"]["ia"]["orden"]["cantidad"], "proveedor_id": alt})
    assert r == {"estado": "modificada", "cuota_rota": True}
    otra = next(p for p in pr.listar_propuestas(con, cid) if p["estado"] == "pendiente" and p["tipo"] == "orden_reposicion")
    with pytest.raises(cy.CicloError, match="homologado"):
        pr.decidir_propuesta(con, cid, ANA, otra["id"], "modificar", "x", {"cantidad": otra["detalle"]["ia"]["orden"]["cantidad"], "proveedor_id": 999})


def test_solo_ordenes_son_modificables(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    canc = next(p for p in pr.listar_propuestas(con, cid) if p["tipo"] == "cancelar_oc")
    with pytest.raises(cy.CicloError, match="órdenes de reposición"):
        pr.decidir_propuesta(con, cid, ANA, canc["id"], "modificar", "x", {"cantidad": 1})
    assert pr.decidir_propuesta(con, cid, ANA, canc["id"], "aprobar")["estado"] == "aprobada"


def test_propuestas_de_una_simulacion_no_se_pueden_decidir(con):
    cid = cy.iniciar_ciclo(con, datos.semana_semilla(con))
    cy.ejecutar_crp(con, cid, CAP)
    pr.generar_propuestas(con, cid, ANA, modo="simulacion")
    props = pr.listar_propuestas(con, cid)
    assert props and all(p["es_simulacion"] for p in props)
    with pytest.raises(cy.EstadoError, match="simulación"):
        pr.decidir_propuesta(con, cid, ANA, props[0]["id"], "aprobar", "x")


def test_regenerar_conserva_lo_decidido_y_no_duplica(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    n = len(pr.listar_propuestas(con, cid))
    canc = next(p for p in pr.listar_propuestas(con, cid) if p["tipo"] == "cancelar_oc")
    pr.decidir_propuesta(con, cid, ANA, canc["id"], "aprobar")
    g = pr.generar_propuestas(con, cid, ANA)
    props = pr.listar_propuestas(con, cid)
    assert g["ya_decididas"] == 1 and len(props) == n
    assert sum(1 for p in props if p["estado"] == "aprobada") == 1


def test_solo_el_analista_decide(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    p = pr.listar_propuestas(con, cid)[0]
    for rol in (CAP, INV, GER):
        with pytest.raises(cy.PermisoError):
            pr.decidir_propuesta(con, cid, rol, p["id"], "aprobar", "x")
