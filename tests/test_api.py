import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

TMP = tempfile.TemporaryDirectory()
os.environ["SII_DB_PATH"] = str(Path(TMP.name) / "test.sqlite3")
from app.main import app  # noqa: E402
from tests.test_dte import xml_document  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.email = f"test-{self.id().replace('.', '-')}@example.cl"
        result = self.client.post("/api/register", json={
            "email": self.email, "password": "ClaveMuySegura123",
            "organization": "Empresa Test", "rut": "11.111.111-1"
        })
        self.assertEqual(result.status_code, 201, result.text)
        self.csrf = result.json()["csrf"]

    def upload(self, payload=None):
        return self.client.post("/api/documents/import",
            files={"file": ("factura.xml", payload or xml_document(), "application/xml")},
            headers={"X-CSRF-Token": self.csrf})

    def test_import_review_export(self):
        self.assertEqual(self.upload().json()["imported"], 1)
        self.assertEqual(self.upload().json()["imported"], 0)
        result = self.client.get("/api/documents")
        self.assertEqual(result.status_code, 200)
        docs = result.json()["documents"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["direccion"], "compra")
        doc_id = docs[0]["id"]
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/original").content, xml_document())
        review = {k: docs[0][k] for k in (
            "tipo_dte", "folio", "fecha_emision", "rut_emisor", "razon_emisor",
            "rut_receptor", "razon_receptor", "monto_neto", "monto_exento",
            "iva", "monto_total", "direccion"
        )}
        review["razon_emisor"] = "Empresa Corregida"
        response = self.client.patch(f"/api/documents/{doc_id}", json=review,
                                     headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["estado"], "revisado")
        exported = self.client.get("/api/export.csv")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("Empresa Corregida", exported.text)

    def test_csrf_and_tenant_isolation(self):
        self.assertEqual(self.client.post("/api/documents/import",
            files={"file": ("factura.xml", xml_document(), "application/xml")}).status_code, 403)
        self.assertEqual(self.upload().status_code, 200)
        document_id = self.client.get("/api/documents").json()["documents"][0]["id"]
        other = TestClient(app)
        result = other.post("/api/register", json={
            "email": "other-" + self.email, "password": "ClaveMuySegura123",
            "organization": "Otra Empresa"
        })
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(other.get(f"/api/documents/{document_id}").status_code, 404)
        self.assertEqual(other.get("/api/documents").json()["documents"], [])


if __name__ == "__main__":
    unittest.main()
