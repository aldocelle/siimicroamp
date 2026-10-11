"""Lectura segura y validación local de DTE chilenos. No consulta el SII."""
import re
from defusedxml import ElementTree as ET


def local_name(tag):
    return str(tag).rsplit("}", 1)[-1]


def child(node, name):
    if node is None:
        return None
    return next((item for item in node if local_name(item.tag) == name), None)


def at(node, *names):
    for name in names:
        node = child(node, name)
        if node is None:
            return ""
    return (node.text or "").strip()


def normalize_rut(value):
    raw = re.sub(r"[^0-9Kk]", "", str(value or "")).upper()
    if len(raw) < 2 or not raw[:-1].isdigit():
        return str(value or "").strip().upper()
    return f"{int(raw[:-1])}-{raw[-1]}"


def valid_rut(value):
    rut = normalize_rut(value)
    if not re.fullmatch(r"[0-9]+-[0-9K]", rut):
        return False
    body, dv = rut.split("-")
    if int(body) == 0:
        return False
    total = sum(int(digit) * (2 + i % 6) for i, digit in enumerate(reversed(body)))
    expected = "0123456789K"[11 - (total % 11)] if (11 - total % 11) < 11 else "0"
    # Resto 0 => DV 0; resto 1 => DV K.
    return expected == dv


def amount(value):
    value = str(value or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"-?\d+", value):
        raise ValueError(f"Monto no entero en XML: {value[:30]}")
    return int(value)


def parse_document(node):
    header = child(node, "Encabezado")
    if header is None:
        raise ValueError("El DTE no contiene Encabezado.")
    ide = child(header, "IdDoc")
    issuer = child(header, "Emisor")
    receiver = child(header, "Receptor")
    totals = child(header, "Totales")
    data = {
        "tipo_dte": at(ide, "TipoDTE"),
        "folio": at(ide, "Folio"),
        "fecha_emision": at(ide, "FchEmis"),
        "rut_emisor": normalize_rut(at(issuer, "RUTEmisor")),
        "razon_emisor": at(issuer, "RznSoc") or at(issuer, "RznSocEmisor"),
        "rut_receptor": normalize_rut(at(receiver, "RUTRecep")),
        "razon_receptor": at(receiver, "RznSocRecep"),
        "monto_neto": amount(at(totals, "MntNeto")),
        "monto_exento": amount(at(totals, "MntExe")),
        "iva": amount(at(totals, "IVA")),
        "monto_total": amount(at(totals, "MntTotal")),
    }
    warnings = []
    for key, label in (("rut_emisor", "emisor"), ("rut_receptor", "receptor")):
        if not valid_rut(data[key]):
            warnings.append(f"RUT de {label} ausente o con dígito verificador inválido.")
    if not data["tipo_dte"] or not data["folio"]:
        raise ValueError("El DTE no contiene TipoDTE o Folio.")
    if data["monto_total"] is None:
        warnings.append("El documento no incluye MntTotal.")
    elif any(data[k] is not None for k in ("monto_neto", "monto_exento", "iva")):
        partial = sum(data[k] or 0 for k in ("monto_neto", "monto_exento", "iva"))
        if partial != data["monto_total"]:
            warnings.append("Neto + exento + IVA no coincide con total; pueden existir otros impuestos o ajustes.")
    data["warnings"] = warnings
    return data


def parse_dte_xml(content: bytes):
    if not content or len(content) > 10 * 1024 * 1024:
        raise ValueError("El XML está vacío o supera el límite de 10 MB.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("El archivo no es XML válido.") from exc
    except (ET.EntitiesForbidden, ET.DTDForbidden, ET.ExternalReferenceForbidden) as exc:
        raise ValueError("XML rechazado: entidades o DTD no permitidas.") from exc

    documents = []
    for node in root.iter():
        if local_name(node.tag) == "DTE":
            doc = child(node, "Documento")
            if doc is not None:
                documents.append(doc)
    if not documents:
        if local_name(root.tag) == "Documento":
            documents = [root]
        else:
            # Algunos sistemas exportan directamente un contenedor de Documento.
            documents = [node for node in root.iter() if local_name(node.tag) == "Documento"]
    if not documents:
        raise ValueError("No se encontraron elementos Documento de DTE.")
    if len(documents) > 500:
        raise ValueError("El XML contiene más de 500 DTE.")
    return [parse_document(doc) for doc in documents]
