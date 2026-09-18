"""API de extremo a extremo con roles reales en la cabecera."""
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.engine import ciclo as cy

CAP, INV, GER = cy.CAPACIDAD, cy.INVENTARIOS, cy.GERENCIA


def h(rol):
    return {"X-Rol": rol}


@pytest.fixture()
def cli(db_plantilla, tmp_path, monkeypatch):
    import shutil
    ruta = tmp_path / "api.db"
    shutil.copy(db_plantilla, ruta)
    monkeypatch.setenv("RAMO_DB", str(ruta))
    return TestClient(app)


def linea(res, nombre):
    return next(r for r in res["crp"]["lineas"] if r["nombre"] == nombre)


def test_meta_y_rol_obligatorio(cli):
    m = cli.get("/api/meta").json()
    assert m["datos_sinteticos"] and len(m["roles"]) == 4 and m["semana"]["es_semilla"] == 1
    assert cli.post("/api/ciclo").status_code == 400
    assert cli.post("/api/ciclo", headers=h("rol_inventado")).status_code == 400


def test_flujo_completo_por_http(cli):
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is None
    r = cli.post("/api/ciclo", headers=h(CAP))
    cid = r.json()["ciclo_id"]
    v1 = cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(CAP)).json()
    assert round(linea(v1, "Barra Rellena")["saturacion"] * 100) == 137
    assert round(v1["crp"]["horas_extra_total"], 1) == 8.5
    assert cli.post(f"/api/ciclo/{cid}/compartir", headers=h(CAP)).status_code == 200

    pid = next(p["producto_id"] for p in cli.get("/api/necesidad").json()["productos"]
               if p["nombre"] == "Ponqué Individual Vainilla 40 g")
    a = cli.post(f"/api/ciclo/{cid}/ajustes", headers=h(INV), json={
        "producto_id": pid, "delta": -120000, "motivo": "riesgo_rotacion", "justificacion": "Baja rotación"})
    assert a.status_code == 200
    aid = a.json()["ajuste_id"]
    assert cli.post(f"/api/ciclo/{cid}/ajustes/{aid}/decision", headers=h(INV),
                    json={"aprobar": True, "justificacion": "Validado"}).status_code == 200
    e = cli.get(f"/api/ciclo/{cid}").json()
    assert e["ciclo"]["estado"] == "decisiones" and e["desactualizada"] is True     # ajuste aprobado sin consolidar
    v = cli.post(f"/api/ciclo/{cid}/consolidar", headers=h(INV)).json()
    assert linea(v, "Ponqué Individual")["necesidad"] == 3_280_000
    assert cli.get(f"/api/ciclo/{cid}").json()["desactualizada"] is False

    # sin aceptar horas extra -> 409; aceptándolas -> oficial
    r = cli.post(f"/api/ciclo/{cid}/aprobar", headers=h(GER), json={"justificacion": "ok"})
    assert r.status_code == 409 and "horas extra" in r.json()["detail"]
    r = cli.post(f"/api/ciclo/{cid}/aprobar", headers=h(GER),
                 json={"justificacion": "Turno extendido validado", "acepta_horas_extra": True})
    assert r.status_code == 200 and r.json()["origen"] == "final"
    e = cli.get(f"/api/ciclo/{cid}").json()
    assert e["ciclo"]["estado"] == "oficial" and len(e["versiones"]) == 3
    cmp = cli.get(f"/api/ciclo/{cid}/comparar", params={"a": 1, "b": 2}).json()
    assert next(c for c in cmp if c["nombre"] == "Ponqué Individual")["cambio"]


def test_codigos_de_error(cli):
    cid = cli.post("/api/ciclo", headers=h(CAP)).json()["ciclo_id"]
    assert cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(INV)).status_code == 403     # rol sin permiso
    assert cli.post(f"/api/ciclo/{cid}/consolidar", headers=h(INV)).status_code == 409       # estado incorrecto
    assert cli.post("/api/ciclo", headers=h(CAP)).status_code == 409                         # ya existe
    cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(CAP))
    cli.post(f"/api/ciclo/{cid}/compartir", headers=h(CAP))
    r = cli.post(f"/api/ciclo/{cid}/ajustes", headers=h(INV),
                 json={"producto_id": 1, "delta": -10, "motivo": "otro", "justificacion": " "})
    assert r.status_code == 400 and "justificación" in r.json()["detail"]
    assert cli.get("/api/ciclo/999").status_code == 400
    assert any(a["accion"].endswith(":denegado") for a in cli.get("/api/auditoria").json())


def test_simular_es_solo_lectura_y_acepta_escenarios(cli):
    r = cli.post("/api/simular", json={"escenario": {"ritmos": {"3": 15000}}}).json()
    g = r["crp"]["grupos"][0]
    assert g["capacidad"] == 1_400_000 + 15_000 * 70 and g["horas_extra"] > 30
    r = cli.post("/api/simular", json={"escenario": {"sin_tripulacion_compartida": True}}).json()
    assert r["crp"]["grupos"] == [] and linea(r, "Barra Rellena")["horas_extra"] == pytest.approx(520000 / 14000)
    assert cli.get("/api/auditoria").json() == []
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is None


def test_necesidad_y_diagnostico_de_fuentes(cli):
    n = cli.get("/api/necesidad").json()
    assert sum(1 for d in n["diagnostico_fuentes"] if d["inconsistente"]) == 2
    tot = sum(p["total"] for p in n["productos"] if p["linea_id"] == 2)
    assert tot == 1_920_000
    ls = cli.get("/api/lineas").json()
    assert len(ls["lineas"]) == 7 and len(ls["grupos"]) == 2


def test_reiniciar_demo_deja_todo_en_el_punto_de_partida(cli):
    cli.post("/api/ciclo", headers=h(CAP))
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is not None
    assert cli.post("/api/demo/reiniciar", headers=h(CAP)).status_code == 200
    assert cli.get("/api/ciclo/actual").json()["ciclo_id"] is None


ANA = cy.ANALISTA


def _oficial(cli):
    cid = cli.post("/api/ciclo", headers=h(CAP)).json()["ciclo_id"]
    cli.post(f"/api/ciclo/{cid}/crp", json={}, headers=h(CAP))
    cli.post(f"/api/ciclo/{cid}/compartir", headers=h(CAP))
    cli.post(f"/api/ciclo/{cid}/abrir-decisiones", headers=h(INV))
    r = cli.post(f"/api/ciclo/{cid}/aprobar", headers=h(GER), json={"justificacion": "ok", "acepta_horas_extra": True})
    assert r.status_code == 200
    return cid


def test_mrp_oficial_exige_plan_y_simulacion_funciona(cli):
    r = cli.get("/api/mrp", params={"modo": "oficial"})
    assert r.status_code == 409 and "oficial" in r.json()["detail"]
    s = cli.get("/api/mrp", params={"modo": "simulacion"}).json()
    assert s["meta"]["es_oficial"] is False and len(s["materiales"]) == 29
    assert cli.get("/api/mrp", params={"modo": "otro"}).status_code == 409


def test_flujo_de_propuestas_ia_por_http(cli):
    cid = _oficial(cli)
    of = cli.get("/api/mrp", params={"modo": "oficial"}).json()
    assert of["meta"]["es_oficial"] and of["resumen"]["quiebres_ocultos_por_sap"]
    assert cli.post(f"/api/ciclo/{cid}/ia/generar", json={"modo": "oficial"}, headers=h(CAP)).status_code == 403
    g = cli.post(f"/api/ciclo/{cid}/ia/generar", json={"modo": "oficial"}, headers=h(ANA))
    assert g.status_code == 200 and g.json()["nuevas"] > 20
    props = cli.get(f"/api/ciclo/{cid}/ia/propuestas").json()
    az = next(p for p in props if p["tipo"] == "orden_reposicion" and p["detalle"]["material"]["codigo"] == "MP-002")
    r = cli.post(f"/api/ciclo/{cid}/ia/propuestas/{az['id']}/decision", headers=h(ANA), json={"decision": "aprobar"})
    assert r.status_code == 400 and "cuota" in r.json()["detail"]
    r = cli.post(f"/api/ciclo/{cid}/ia/propuestas/{az['id']}/decision", headers=h(ANA),
                 json={"decision": "aprobar", "justificacion": "El proveedor principal no llega a tiempo"})
    assert r.status_code == 200 and r.json()["cuota_rota"] is True
    assert cli.post(f"/api/ciclo/{cid}/ia/propuestas/{az['id']}/decision", headers=h(ANA),
                    json={"decision": "rechazar", "justificacion": "x"}).status_code == 409
    otra = next(p for p in props if p["tipo"] == "orden_reposicion" and p["estado"] == "pendiente" and not p["detalle"]["ia"]["excepcion_cuota"])
    r = cli.post(f"/api/ciclo/{cid}/ia/propuestas/{otra['id']}/decision", headers=h(ANA),
                 json={"decision": "modificar", "justificacion": "Compra parcial", "cantidad": otra["detalle"]["ia"]["orden"]["cantidad"] + 500})
    assert r.status_code == 200 and r.json()["estado"] == "modificada"
    assert cli.post(f"/api/ciclo/{cid}/ia/propuestas/{otra['id']}/decision", headers=h(INV),
                    json={"decision": "aprobar"}).status_code == 403


def test_la_conexion_se_puede_usar_desde_otro_hilo(db_plantilla):
    """Regresión: FastAPI crea la conexión y ejecuta el endpoint en hilos distintos."""
    import threading
    from backend.engine import datos
    con = datos.conectar(db_plantilla)
    resultado = []
    t = threading.Thread(target=lambda: resultado.append(datos.semana_semilla(con)))
    t.start(); t.join()
    con.close()
    assert resultado == [1]


def test_salida_a_sap_por_http(cli):
    cid = _oficial(cli)
    assert cli.post(f"/api/ciclo/{cid}/exportar/ordenes", headers=h(INV)).status_code == 403
    r = cli.post(f"/api/ciclo/{cid}/exportar/ordenes", headers=h(CAP))
    assert r.status_code == 200 and len(r.json()["archivos"]) == 2
    assert cli.post(f"/api/ciclo/{cid}/exportar/ordenes", headers=h(CAP)).status_code == 409
    a = r.json()["archivos"][0]
    d = cli.get(f"/api/exportaciones/{a['id']}/descargar")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"] and d.content.startswith(b"\xef\xbb\xbfCENTRO;LINEA")
    v = cli.get(f"/api/exportaciones/{a['id']}/vista").json()
    assert v["lineas"][0].startswith("CENTRO;") and v["filas"] == a["filas"]
    z = cli.get(f"/api/ciclo/{cid}/exportaciones/lote/{r.json()['lote']}.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    s = cli.get(f"/api/ciclo/{cid}/salida").json()
    assert s["ordenes_exportadas"] and len(s["archivos"]) == 2
    assert cli.post(f"/api/ciclo/{cid}/exportar/compras", headers=h(ANA)).status_code == 409         # nada aprobado aún
    csv = cli.get("/api/auditoria.csv")
    assert csv.status_code == 200 and b"exportar_ordenes" in csv.content
    assert cli.get("/api/exportaciones/999/vista").status_code == 400
