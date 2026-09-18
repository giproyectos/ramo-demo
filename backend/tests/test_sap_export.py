"""Salida a SAP: solo lo aprobado, por planta, con contenido exacto, huella y auditoría."""
import hashlib
import io
import json
import zipfile

import pytest

from backend.engine import ciclo as cy
from backend.engine import datos, sap_export as sx
from backend.engine import propuestas as pr
from backend.tests.test_mrp_ia import plan_oficial

CAP, INV, GER, ANA = cy.CAPACIDAD, cy.INVENTARIOS, cy.GERENCIA, cy.ANALISTA


def filas(contenido: str) -> list[list[str]]:
    lineas = contenido.strip("\n").split("\n")
    return [l.split(";") for l in lineas]


def test_no_se_exporta_sin_plan_oficial(con):
    cid = cy.iniciar_ciclo(con, datos.semana_semilla(con))
    cy.ejecutar_crp(con, cid, CAP)
    with pytest.raises(cy.EstadoError, match="oficial"):
        sx.exportar_ordenes(con, cid, CAP)


def test_ordenes_provisionales_por_planta_reproducen_el_plan(con):
    cid = plan_oficial(con, ("Ponqué Individual Vainilla 40 g", -120_000))
    for rol in (INV, ANA, GER):
        with pytest.raises(cy.PermisoError):
            sx.exportar_ordenes(con, cid, rol)
    r = sx.exportar_ordenes(con, cid, CAP)
    assert {a["archivo"].split("_")[2] for a in r["archivos"]} == {"PlantaA", "PlantaB"}
    total = {}
    for a in r["archivos"]:
        f = filas(sx.obtener(con, a["id"])["contenido"])
        cab, datos_ = f[0], f[1:]
        assert cab[:5] == ["CENTRO", "LINEA", "SKU", "DESCRIPCION", "CANTIDAD"] and len(datos_) == a["filas"]
        assert all(len(x) == len(cab) and x[8] == "PROVISIONAL" and x[6] == "2026-09-21" and x[7] == "2026-09-27" for x in datos_)
        for x in datos_:
            total[x[1]] = total.get(x[1], 0) + float(x[4])
    assert total["L01"] == 3_280_000 and total["L02"] == 1_920_000 and total["L03"] == 1_600_000     # plan final, con el ajuste
    assert sum(1 for a in r["archivos"]) == 2
    barra = next(x for a in r["archivos"] for x in filas(sx.obtener(con, a["id"])["contenido"])[1:] if x[1] == "L02")
    assert float(barra[10]) == pytest.approx(8.5, abs=0.1)                                            # horas extra de la línea


def test_las_ordenes_solo_se_exportan_una_vez_y_dejan_huella(con):
    cid = plan_oficial(con)
    r = sx.exportar_ordenes(con, cid, CAP)
    with pytest.raises(cy.EstadoError, match="ya se exportaron"):
        sx.exportar_ordenes(con, cid, CAP)
    a = sx.obtener(con, r["archivos"][0]["id"])
    assert hashlib.sha256(a["contenido"].encode("utf-8")).hexdigest() == a["sha256"]
    assert any(x["accion"] == "exportar_ordenes" for x in cy.estado_ciclo(con, cid)["auditoria"])
    assert sx.estado_salida(con, cid)["ordenes_exportadas"] is True


def test_compras_exige_propuestas_aprobadas(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    with pytest.raises(cy.EstadoError, match="pendientes de exportar"):
        sx.exportar_compras(con, cid, ANA)
    with pytest.raises(cy.PermisoError):
        sx.exportar_compras(con, cid, CAP)


def test_compras_exporta_solo_lo_aprobado_o_modificado(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    props = pr.listar_propuestas(con, cid)
    ordenes = [p for p in props if p["tipo"] == "orden_reposicion"]
    az = next(p for p in ordenes if p["detalle"]["material"]["codigo"] == "MP-002")
    normal = [p for p in ordenes if not p["detalle"]["ia"]["excepcion_cuota"] and p is not az]
    aprobada, modificada, rechazada = normal[0], normal[1], normal[2]
    pr.decidir_propuesta(con, cid, ANA, az["id"], "aprobar", "El proveedor principal no llega a tiempo")
    pr.decidir_propuesta(con, cid, ANA, aprobada["id"], "aprobar")
    nueva = modificada["detalle"]["ia"]["orden"]["cantidad"] + 500
    pr.decidir_propuesta(con, cid, ANA, modificada["id"], "modificar", "Compra parcial", {"cantidad": nueva})
    pr.decidir_propuesta(con, cid, ANA, rechazada["id"], "rechazar", "Ya se compró directo")
    canc = next(p for p in props if p["tipo"] == "cancelar_oc")
    pr.decidir_propuesta(con, cid, ANA, canc["id"], "aprobar")
    lt = next(p for p in props if p["tipo"] == "actualizar_lead_time")
    pr.decidir_propuesta(con, cid, ANA, lt["id"], "aprobar")

    r = sx.exportar_compras(con, cid, ANA)
    tipos = {a["tipo"]: a for a in r["archivos"]}
    assert set(tipos) == {"compras", "cancelaciones_oc", "lead_times"}
    compras = filas(sx.obtener(con, tipos["compras"]["id"])["contenido"])
    cab, cuerpo = compras[0], compras[1:]
    col = {n: i for i, n in enumerate(cab)}
    codigos = {x[col["COD_MATERIAL"]] for x in cuerpo}
    assert az["detalle"]["material"]["codigo"] in codigos and aprobada["detalle"]["material"]["codigo"] in codigos
    assert rechazada["detalle"]["material"]["codigo"] not in codigos                          # lo rechazado no sale
    fila_az = next(x for x in cuerpo if x[col["COD_MATERIAL"]] == "MP-002")
    assert fila_az[col["PROVEEDOR"]] == "Proveedor 02" and fila_az[col["CUOTA_ROTA"]] == "SI" and "no llega" in fila_az[col["JUSTIFICACION"]]
    cant_mod = sum(float(x[col["CANTIDAD"]]) for x in cuerpo if x[col["COD_MATERIAL"]] == modificada["detalle"]["material"]["codigo"])
    assert cant_mod == pytest.approx(nueva)
    assert any(x[col["ORIGEN"]] == "MODIFICADA" for x in cuerpo)
    canc_f = filas(sx.obtener(con, tipos["cancelaciones_oc"]["id"])["contenido"])
    assert canc_f[1][0] == canc["detalle"]["oc"]
    lt_f = filas(sx.obtener(con, tipos["lead_times"]["id"])["contenido"])
    assert int(lt_f[1][4]) == lt["detalle"]["sugerido"] and int(lt_f[1][3]) == lt["detalle"]["nominal"]


def test_lo_exportado_no_se_repite_y_lo_nuevo_si(con):
    cid = plan_oficial(con)
    pr.generar_propuestas(con, cid, ANA)
    props = pr.listar_propuestas(con, cid)
    c1, c2 = [p for p in props if p["tipo"] == "cancelar_oc"][:2]
    pr.decidir_propuesta(con, cid, ANA, c1["id"], "aprobar")
    r1 = sx.exportar_compras(con, cid, ANA)
    with pytest.raises(cy.EstadoError):
        sx.exportar_compras(con, cid, ANA)
    pr.decidir_propuesta(con, cid, ANA, c2["id"], "aprobar")
    r2 = sx.exportar_compras(con, cid, ANA)
    assert r2["lote"] == r1["lote"] + 1
    a = filas(sx.obtener(con, r2["archivos"][0]["id"])["contenido"])
    assert len(a) == 2 and a[1][0] == c2["detalle"]["oc"]
    assert sx.estado_salida(con, cid)["propuestas_pendientes_de_exportar"]["cancelar_oc"] == 0


def test_zip_del_lote_y_listado(con):
    cid = plan_oficial(con)
    r = sx.exportar_ordenes(con, cid, CAP)
    z = zipfile.ZipFile(io.BytesIO(sx.zip_del_lote(con, cid, r["lote"])))
    assert sorted(z.namelist()) == sorted(a["archivo"] for a in r["archivos"])
    assert z.read(z.namelist()[0]).startswith(b"\xef\xbb\xbf")                                  # UTF-8 con BOM para Excel/LSMW
    assert [x["id"] for x in sx.listar(con, cid)] == sorted((a["id"] for a in r["archivos"]), reverse=True)
    with pytest.raises(cy.CicloError):
        sx.zip_del_lote(con, cid, 99)


def test_los_separadores_no_rompen_el_csv():
    assert sx._celda("a;b\nc") == "a,b c"
    assert sx._celda(3.0) == "3" and sx._celda(2.5) == "2.5" and sx._celda(None) == ""
    assert json.dumps(sx._csv(["A", "B"], [["x;y", 1]])) == json.dumps("A;B\nx,y;1\n")
