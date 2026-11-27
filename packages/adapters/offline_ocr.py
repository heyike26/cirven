"""Pinned local OCR over authority-read bitmap bytes; output is observation only."""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import threading
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from packages.domain.document_import import DocumentImportError, image_metadata
from packages.domain.local_workspaces import FileSource, digest, sha
from packages.domain.store import Conflict, InvalidInput

HEX = r"^[0-9a-f]{64}$"
MAX_BITMAP_BYTES = 16 * 1024 * 1024


class NativeBitmapRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["NATIVE_CAPTURE"]
    view_ref: str = Field(pattern=r"^[0-9a-f]{32}$")
    frame_revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=HEX)
    binding_sha256: str = Field(pattern=HEX)


class LocalBitmapRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["LOCAL_FILE"]
    file_source_ref: FileSource
    sha256: str = Field(pattern=HEX)

    @model_validator(mode="after")
    def same_bytes(self):
        if self.sha256 != self.file_source_ref.sha256:
            raise ValueError("Local bitmap must refer to the exact FileSource bytes")
        return self


BitmapRef = Annotated[NativeBitmapRef | LocalBitmapRef, Field(discriminator="kind")]
BITMAP_REF = TypeAdapter(BitmapRef)


class OCRUnavailable(Conflict):
    """Configuration/dependency absence is explicit; it never triggers a fetch."""


class OfflineOCR:
    def __init__(self, model_manifest: Path | None, bitmap_reader: Callable):
        self.manifest_path = Path(model_manifest) if model_manifest is not None else None
        self.bitmap_reader = bitmap_reader
        self.lock = threading.Lock()
        self._engine = None
        self._loaded_identity = None

    def _configuration(self):
        path = self.manifest_path
        if path is None:
            raise OCRUnavailable("OCR_NOT_CONFIGURED: install the pinned offline OCR component")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise OCRUnavailable("OCR_MANIFEST_UNAVAILABLE")
        try:
            wrapper = json.loads(path.read_bytes())
            config = wrapper["payload"]
            if digest(config) != wrapper["sha256"] or config["schema_version"] != "eeas.offline-ocr-models.v1":
                raise ValueError
            if set(config["models"]) != {"det", "cls", "rec"}:
                raise ValueError
            if config["engine"]["name"] != "rapidocr" or config["engine"]["version"] != "3.9.2":
                raise ValueError
            if not isinstance(config["engine_files"], dict) or not isinstance(config["packages"], dict):
                raise ValueError
            for value in config["models"].values():
                if (not isinstance(value, dict) or not isinstance(value["path"], str) or
                        type(value["byte_length"]) is not int or not 0 < value["byte_length"] <= 128 * 1024 * 1024 or
                        not isinstance(value["sha256"], str) or not re.fullmatch(HEX, value["sha256"])):
                    raise ValueError
            for name, version in config["packages"].items():
                if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
                    raise ValueError
            if config["packages"].get("rapidocr") != "3.9.2" or config["packages"].get("onnxruntime") != "1.23.2":
                raise ValueError
            platform_packages = config.get("platform_packages", {})
            if not isinstance(platform_packages, dict) or any(not isinstance(value, dict) for value in platform_packages.values()):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise Conflict("OCR model manifest integrity failed") from None
        platform = "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else None
        if platform is None or platform not in config["engine_files"]:
            raise OCRUnavailable("OCR_PLATFORM_NOT_QUALIFIED")
        if not isinstance(config["engine_files"][platform], dict):
            raise Conflict("OCR engine file manifest is invalid")
        paths = {}
        for role, model in config["models"].items():
            name = model["path"]
            if (not isinstance(name, str) or "\\" in name or ":" in name or
                    PurePosixPath(name).is_absolute() or any(p in {"", ".", ".."} for p in name.split("/"))):
                raise Conflict("OCR model path is outside its configured component")
            local = path.parent.joinpath(*name.split("/"))
            if any(p.is_symlink() for p in [local, *local.parents[:len(name.split('/'))]]):
                raise Conflict("OCR model links are not permitted")
            if not local.is_file():
                raise OCRUnavailable("OCR_MODEL_MISSING: " + role)
            if local.stat().st_size != model["byte_length"] or sha(local.read_bytes()) != model["sha256"]:
                raise Conflict("OCR_MODEL_DRIFT: " + role)
            paths[role] = local
        distributions = {}
        versions = config["packages"] | config.get("platform_packages", {}).get(platform, {})
        for name, expected in versions.items():
            try:
                distribution = importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                raise OCRUnavailable("OCR_DEPENDENCY_MISSING: " + name) from None
            if distribution.version != expected:
                raise OCRUnavailable("OCR_DEPENDENCY_VERSION_MISMATCH: " + name)
            distributions[name] = distribution
        for package, files in config["engine_files"][platform].items():
            if package not in distributions or not isinstance(files, dict):
                raise Conflict("OCR engine file manifest is invalid")
            distribution = distributions[package]
            for member, expected in files.items():
                if (not isinstance(member, str) or PurePosixPath(member).is_absolute() or "\\" in member or
                        any(p in {"", ".", ".."} for p in member.split("/")) or
                        not isinstance(expected, str) or not re.fullmatch(HEX, expected)):
                    raise Conflict("OCR engine file manifest is invalid")
                local = Path(distribution.locate_file(member))
                if not local.is_file() or sha(local.read_bytes()) != expected:
                    raise Conflict("OCR_ENGINE_DRIFT: " + package + "/" + member)
        return config, paths, digest(config)

    def capabilities(self):
        try:
            config, _, identity = self._configuration()
        except (OCRUnavailable, Conflict) as error:
            return {"available": False, "status": "UNAVAILABLE", "reason": str(error),
                    "network_access": "NEVER", "qualification": "OBSERVATION_ONLY"}
        return {"available": True, "status": "CONFIGURED", "engine": config["engine"],
                "models_sha256": {k: v["sha256"] for k, v in config["models"].items()},
                "component_sha256": identity, "network_access": "NEVER", "qualification": "OBSERVATION_ONLY",
                "formats": ["image/png", "image/jpeg"], "coordinate_space": "DECODED_IMAGE_PIXELS"}

    def _bitmap(self, pid, ref):
        value = self.bitmap_reader(pid, deepcopy(ref))
        if not isinstance(value, dict) or not isinstance(value.get("bytes"), bytes):
            raise Conflict("Bitmap authority did not return exact bytes")
        raw = value["bytes"]
        if len(raw) > MAX_BITMAP_BYTES:
            raise InvalidInput("OCR bitmap exceeds 16 MiB")
        try:
            info = image_metadata(raw)
        except DocumentImportError as error:
            raise InvalidInput(str(error)) from error
        if (info["sha256"] != ref["sha256"] or info["sha256"] != value.get("sha256") or
                any(value.get(key) != info[key] for key in ["width", "height", "media_type"])):
            raise Conflict("Bitmap authority/actual image metadata mismatch")
        if type(value["width"]) is not int or type(value["height"]) is not int:
            raise Conflict("Bitmap dimensions must be actual integer pixels")
        source = value.get("source_ref")
        if not isinstance(source, dict) or source.get("sha256") != info["sha256"]:
            raise Conflict("Bitmap source reference does not bind the image bytes")
        if ref["kind"] == "LOCAL_FILE":
            if ref["file_source_ref"]["project_id"] != pid or source != ref["file_source_ref"]:
                raise Conflict("Local bitmap authority/source project or revision mismatch")
        else:
            if set(source) != {"document_id", "revision", "sha256"} or type(source["revision"]) is not int or source["revision"] < 1:
                raise Conflict("Native bitmap needs an exact Store image revision")
            if value.get("observation_authority") != "SELECTED_WINDOW_PIXELS_NOT_VERIFIED_ACTIVE_DOCUMENT_CONTENT":
                raise Conflict("Native bitmap authority must retain its observed-pixels boundary")
        return raw, {"sha256": info["sha256"], "width": info["width"], "height": info["height"],
                     "media_type": info["media_type"], "source_ref": deepcopy(source),
                     "observation_authority": value.get("observation_authority", "LOCAL_FILE_BYTES_NOT_VERIFIED_SEMANTIC_FACTS")}

    @staticmethod
    def _polygon(value, width, height):
        if len(value) != 4 or any(len(p) != 2 for p in value):
            raise Conflict("OCR returned an invalid four-point box")
        result = []
        for x, y in value:
            x, y = float(x), float(y)
            if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= width or not 0 <= y <= height:
                raise Conflict("OCR box is outside the actual image")
            result.append([round(x, 4), round(y, 4)])
        return result

    def _inference(self, raw, config, paths, identity):
        import cv2
        import numpy as np
        from PIL import Image
        if self._engine is None or self._loaded_identity != identity:
            from rapidocr import RapidOCR
            # Upstream falls back to download only for omitted model paths.
            # Every active stage receives its already-verified explicit local path.
            params = {"Global.return_word_box": True, "Global.return_single_char_box": True,
                      "Global.log_level": "error", "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                      "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                      "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
                      "EngineConfig.onnxruntime.use_cann": False, "EngineConfig.onnxruntime.use_coreml": False,
                      **{stage + ".model_path": str(paths[role]) for role, stage in [("det", "Det"), ("cls", "Cls"), ("rec", "Rec")]}}
            import onnxruntime as ort
            from omegaconf import flag_override
            options = ort.SessionOptions()
            options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
            options.log_severity_level = 4
            sessions = {}
            for role, path in paths.items():
                model_bytes = path.read_bytes()
                if sha(model_bytes) != config["models"][role]["sha256"]:
                    raise Conflict("OCR_MODEL_DRIFT: " + role)
                sessions[role] = ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
                if sessions[role].get_providers() != ["CPUExecutionProvider"]:
                    raise Conflict("OCR requires the configured CPU inference provider")
            # Verify the pinned model metadata before upstream constructs its
            # recognizer, which otherwise can download a missing character map.
            character_map = sessions["rec"].get_modelmeta().custom_metadata_map.get("character")
            if not isinstance(character_map, str) or not character_map.strip():
                raise Conflict("OCR_MODEL_CHARACTER_MAP_MISSING")
            # Reuse upstream's documented custom ONNX-session seam. Model bytes
            # are loaded once from verified memory, closing path replacement races.
            engine = RapidOCR.__new__(RapidOCR)
            cfg = engine._load_config(None, params)
            with flag_override(cfg, "allow_objects", True):
                for role, stage in [("det", "Det"), ("cls", "Cls"), ("rec", "Rec")]:
                    cfg[stage].session = sessions[role]
                engine._initialize(cfg)
            self._engine, self._loaded_identity = engine, identity
        with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as image:
            if image.getexif().get(274, 1) != 1:
                raise InvalidInput("OCR JPEG orientation must be normalized to a registered bitmap first")
            if "A" in image.getbands():
                rgba = image.convert("RGBA")
                rgb = Image.new("RGB", image.size, "white")
                rgb.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
            else:
                rgb = image.convert("RGB")
            pixels = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
        return self._engine(pixels)

    def recognize(self, project_id, bitmap_ref):
        try:
            ref = BITMAP_REF.validate_python(bitmap_ref).model_dump()
        except ValueError as error:
            raise InvalidInput("OCR requires an exact registered bitmap reference") from error
        # Authority, original bytes and configuration are checked for every request,
        # including repeated requests using an already loaded inference session.
        raw, bitmap = self._bitmap(project_id, ref)
        with self.lock:
            config, paths, identity = self._configuration()
            try:
                result = self._inference(raw, config, paths, identity)
            except Exception:
                self._engine = None
                self._loaded_identity = None
                raise
            _, after = self._bitmap(project_id, ref)
            _, _, after_identity = self._configuration()
            if after != bitmap or after_identity != identity:
                raise Conflict("OCR source or model configuration changed during inference")
            text = result.txts
            boxes, scores = result.boxes, result.scores
            lines = []
            if text is not None:
                if boxes is None or scores is None or len(text) != len(boxes) or len(text) != len(scores) or len(text) > 1024:
                    raise Conflict("OCR output counts do not match")
                words = result.word_results
                if words is None or len(words) != len(text):
                    raise Conflict("OCR character output counts do not match")
                for index, (value, score, box, characters) in enumerate(zip(text, scores, boxes, words)):
                    score = float(score)
                    if not isinstance(value, str) or len(value) > 16384 or not math.isfinite(score) or not 0 <= score <= 1:
                        raise Conflict("OCR text/confidence is invalid")
                    items = []
                    for character, confidence, polygon in characters or []:
                        confidence = float(confidence)
                        if not isinstance(character, str) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                            raise Conflict("OCR character/confidence is invalid")
                        items.append({"text": character, "confidence": round(confidence, 6),
                                      "polygon": self._polygon(polygon, bitmap["width"], bitmap["height"])})
                    lines.append({"id": f"line-{index + 1:04d}", "text": value, "confidence": round(score, 6),
                                  "polygon": self._polygon(box, bitmap["width"], bitmap["height"]), "characters": items})
            elif boxes is not None or scores is not None:
                raise Conflict("Empty OCR output has unexpected boxes or scores")
            payload = {"schema_version": "eeas.offline-ocr-observation.v1", "project_id": project_id,
                       "status": "RECOGNIZED" if lines else "NO_TEXT", "qualification": "OBSERVATION_ONLY",
                       "bitmap_ref": ref, "bitmap": bitmap, "engine": config["engine"],
                       "component_sha256": identity, "model_sha256": {k: v["sha256"] for k, v in config["models"].items()},
                       "packages": config["packages"] | config.get("platform_packages", {}).get("windows" if sys.platform == "win32" else "linux", {}),
                       "coordinate_space": "DECODED_IMAGE_PIXELS",
                       "preprocessing": "RGBA_OVER_WHITE;MAX_SIDE_2000;BOXES_MAPPED_TO_ORIGINAL_PIXELS",
                       "lines": lines, "text": "\n".join(line["text"] for line in lines), "network_access": "NEVER"}
            return {**payload, "sha256": digest(payload)}
