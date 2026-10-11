import unittest
from app.dte import normalize_rut, parse_dte_xml, valid_rut


def xml_document(folio="123", total=1190):
    return f"""<DTE xmlns="http://www.sii.cl/SiiDte"><Documento ID="T33F{folio}">
      <Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>{folio}</Folio><FchEmis>2026-10-11</FchEmis></IdDoc>
      <Emisor><RUTEmisor>12.345.678-5</RUTEmisor><RznSoc>Empresa de Prueba</RznSoc></Emisor>
      <Receptor><RUTRecep>11.111.111-1</RUTRecep><RznSocRecep>Cliente de Prueba</RznSocRecep></Receptor>
      <Totales><MntNeto>1000</MntNeto><IVA>190</IVA><MntTotal>{total}</MntTotal></Totales>
      </Encabezado></Documento></DTE>""".encode()


class DteTests(unittest.TestCase):
    def test_rut(self):
        self.assertTrue(valid_rut("12.345.678-5"))
        self.assertTrue(valid_rut("11111111-1"))
        self.assertFalse(valid_rut("12.345.678-9"))
        self.assertFalse(valid_rut(""))
        self.assertEqual(normalize_rut("12.345.678-5"), "12345678-5")

    def test_single_dte_with_namespace(self):
        docs = parse_dte_xml(xml_document())
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["folio"], "123")
        self.assertEqual(docs[0]["rut_emisor"], "12345678-5")
        self.assertEqual(docs[0]["monto_total"], 1190)
        self.assertEqual(docs[0]["warnings"], [])

    def test_envelope_multiple_dte(self):
        first = xml_document("123").decode()
        second = xml_document("124").decode()
        envelope = ("<EnvioDTE><SetDTE>" + first + second + "</SetDTE></EnvioDTE>").encode()
        self.assertEqual(len(parse_dte_xml(envelope)), 2)

    def test_amount_warning(self):
        self.assertTrue(parse_dte_xml(xml_document(total=1000))[0]["warnings"])

    def test_reject_external_entity(self):
        payload = b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><DTE>&xxe;</DTE>'
        with self.assertRaises(ValueError):
            parse_dte_xml(payload)

    def test_reject_missing_dte(self):
        with self.assertRaises(ValueError):
            parse_dte_xml(b"<foo/>")


if __name__ == "__main__":
    unittest.main()
