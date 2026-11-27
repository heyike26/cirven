from io import BytesIO
import hashlib

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject

from test_documents import project, upload


def pdf_fixture(texts, *, image=False, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands = "BT /F1 10 Tf 40 800 Td " + " ".join(f"({line}) Tj 0 -14 Td" for line in escaped.splitlines()) + " ET"
        if image:
            bitmap = Image.new("RGB", (32, 24), "#205acb")
            obj = DecodedStreamObject()
            obj.set_data(bitmap.tobytes())
            obj.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                        NameObject("/Width"): NumberObject(32), NameObject("/Height"): NumberObject(24),
                        NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): writer._add_object(obj)})
            commands += " q 64 0 0 48 40 40 cm /Im0 Do Q"
        stream.set_data(commands.encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("original-fixture-password")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def test_long_pdf_tail_page_citations_and_original_hash(client):
    pid = project(client)
    raw = pdf_fixture(["Reliable timestamp collection.\n" * 2500, "REQ: tail page requirement\nACCEPT: original PDF hashes match"])
    response = upload(client, pid, raw, "long-taskbook.pdf")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["format"] == "pdf" and doc["character_length"] > 70000
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    row = client.get(path).json()
    assert len(row["outline"]["pages"]) == 2
    page = row["outline"]["pages"][-1]
    hit = client.get(f"/api/projects/{pid}/search", params={"keyword": "tail page requirement"}).json()["hits"][0]
    assert page["line_start"] <= hit["line_start"] <= page["line_end"]
    proposal = client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": doc["id"], "revision": 1})
    assert proposal.status_code == 200
    assert client.post(f"/api/projects/{pid}/requirements", json=proposal.json()["proposal"]).status_code == 201
    assert client.get(path + "/download", params={"revision": 1}).content == raw


def test_scanned_pdf_image_is_preserved_without_fake_ocr(client):
    pid = project(client)
    doc = upload(client, pid, pdf_fixture([""], image=True), "scan.pdf").json()
    assert doc["asset_count"] == 1
    assert "PDF_PAGE_1_NO_TEXT_LAYER_OCR_OR_VISION_REQUIRED" in doc["warnings"]
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    assets = client.get(path + "/assets").json()["assets"]
    assert assets[0]["member_path"].startswith("pages/1/images/")
    response = client.get(path + f"/assets/{assets[0]['asset_id']}", params={"revision": 1})
    assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == assets[0]["sha256"]
    assert Image.open(BytesIO(response.content)).size == (32, 24)
    assert client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": doc["id"], "revision": 1}).status_code == 422


def test_encrypted_corrupt_and_page_limit_pdf_rejected_without_records(client):
    pid = project(client)
    for raw in (b"%PDF-1.7 corrupt fixture", pdf_fixture(["private"], encrypted=True), pdf_fixture([""] * 401)):
        assert upload(client, pid, raw, "invalid.pdf").status_code == 422
        assert client.get(f"/api/projects/{pid}/documents").json()["documents"] == []
