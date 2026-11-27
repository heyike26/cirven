import hashlib
from io import BytesIO
import sqlite3

from PIL import Image, ImageDraw
import pytest

from packages.domain.store import Store
from test_documents import project, upload


def original_image(format="PNG"):
    image = Image.new("RGB", (240, 160), "#f1f6ff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 20, 80, 100), fill="#285dcc")
    draw.line((80, 60, 180, 60), fill="#253451", width=4)
    draw.rectangle((180, 20, 225, 100), outline="#253451", width=3)
    target = BytesIO()
    image.save(target, format=format)
    return target.getvalue()


@pytest.mark.parametrize("format,name,mime", [("PNG", "原创框图.png", "image/png"), ("JPEG", "diagram.jpg", "image/jpeg")])
def test_original_image_import_asset_preview_hash_download_and_revision_isolation(client, tmp_path, format, name, mime):
    pid, other = project(client), project(client, "隔离项目")
    raw = original_image(format)
    response = upload(client, pid, raw, name)
    assert response.status_code == 201
    doc = response.json()
    assert doc["format"] == "image" and doc["asset_count"] == 1
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "IMAGE_TEXT_NOT_EXTRACTED" in doc["warnings"]
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    listing = client.get(path + "/assets", params={"revision": 1}).json()
    assert listing["revision"] == 1 and listing["sha256"] == doc["sha256"]
    asset = listing["assets"][0]
    assert asset["media_type"] == mime and asset["width"] == 240 and asset["height"] == 160
    assert asset["sha256"] == doc["sha256"] and asset["byte_length"] == len(raw)
    preview = client.get(path + f"/assets/{asset['asset_id']}", params={"revision": 1})
    assert preview.content == raw and preview.headers["content-type"] == mime
    assert preview.headers["x-content-type-options"] == "nosniff"
    download = client.get(path + "/download", params={"revision": 1})
    assert download.content == raw and download.headers["content-disposition"].startswith("attachment;")
    assert client.get(path.replace(pid, other) + "/assets", params={"revision": 1}).status_code == 404
    assert client.get(path + "/assets/not-an-asset", params={"revision": 1}).status_code == 404
    assert client.get(path + f"/assets/{asset['asset_id']}").status_code == 422
    content = client.get(path).json()["content"]
    assert "尚未提取" in content
    assert client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": doc["id"], "revision": 1}).status_code == 422
    assert client.put(path, files={"file": ("replacement.txt", b"new source")}, data={"expected_revision": 1}).status_code == 200
    assert client.get(path + "/assets").json()["assets"] == []
    assert client.get(path + f"/assets/{asset['asset_id']}", params={"revision": 1}).content == raw
    store = Store(tmp_path / "eeas.sqlite3")
    with store.connect() as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE document_assets SET raw_bytes=?", (b"changed",))
    assert client.delete(path).status_code == 200
    assert client.get(path + f"/assets/{asset['asset_id']}", params={"revision": 1}).status_code == 404


@pytest.mark.parametrize("name,raw", [("fake.png", b"not an image"), ("wrong.jpg", original_image()),
    ("truncated.png", original_image()[:60]), ("../path.png", original_image()),
    ("C:\\path.png", original_image()), ("oversize.png", b"x" * (16 * 1024 * 1024 + 1))])
def test_corrupt_mismatched_unsafe_and_oversized_images_do_not_write_any_records(client, tmp_path, name, raw):
    pid = project(client)
    assert upload(client, pid, raw, name).status_code == 422
    assert client.get(f"/api/projects/{pid}/documents").json()["documents"] == []
    with Store(tmp_path / "eeas.sqlite3").connect() as db:
        assert db.execute("SELECT count(*) FROM document_versions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM document_assets").fetchone()[0] == 0


def test_image_pixel_and_animation_limits_are_enforced_before_persistence(client):
    pid = project(client)
    raw = BytesIO()
    Image.new("1", (5000, 4000)).save(raw, "PNG")
    assert upload(client, pid, raw.getvalue(), "pixel-limit.png").status_code == 422
    animation = BytesIO()
    Image.new("RGB", (10, 10), "blue").save(animation, "PNG", save_all=True,
        append_images=[Image.new("RGB", (10, 10), "white")], duration=200, loop=0)
    assert upload(client, pid, animation.getvalue(), "animated.png").status_code == 422


def test_v13_database_migrates_without_rewriting_original_document(tmp_path, migrate_through):
    database = tmp_path / "eeas.sqlite3"
    migrate_through(database, 13, "013_task_native_reviews.sql")
    raw = b"REQ: original revision\r\nACCEPT: untouched bytes\r\n"
    digest = hashlib.sha256(raw).hexdigest()
    pid, doc_id, stamp = "image-project", "original-doc", "2026-10-13T00:00:00Z"
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO projects VALUES (?,?,?,?)", (pid, "原工程", "", stamp))
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?)", (doc_id, pid, "original.txt", 1, None, stamp))
        db.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)",
                   (doc_id, 1, digest, raw, raw.decode(), "legacy:original", stamp))
    upgraded = Store(database)
    preserved = upgraded.document(pid, doc_id, 1)
    assert preserved["raw_bytes"] == raw and preserved["sha256"] == digest
    assert upgraded.document_assets(pid, doc_id, 1) == []
    assert upgraded.document_chunks(pid, doc_id, 1)["chunks"][0]["content"] == raw.decode()
