"""Build full local WOFF2 fonts and verify the decoded fonts against originals."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {
    "ui_hans": "项目工作台材料任务审批验证运行暂停取消重试来源版本原件下载搜索设置预算模型会话恢复分支图片图像节点关系文档历史离线内网中文字体数据许可错误失败成功确认需求已保存未确认候选人工只读全文上下文尾部",
    "hans_punctuation": "，。；：？！、（）【】《》“”‘’—…￥",
    "latin_extended": "EEAS Studio Manrope ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 Café Straße naïve Ångström Łódź čćžš ñ € £ ¥",
    "interface_symbols_optional": "→ ← ✓ ⚠ ⌘ ⏎",
}
FAMILIES = {
    "notosanssc": {"original": "NotoSansSC[wght].ttf", "woff2": "noto-sans-sc-variable.woff2",
                   "alias": "Studio Sans SC", "family": "Noto Sans SC", "weight": [100, 900],
                   "required_samples": ["ui_hans", "hans_punctuation"]},
    "manrope": {"original": "Manrope[wght].ttf", "woff2": "manrope-variable.woff2",
                "alias": "Studio Manrope", "family": "Manrope", "weight": [200, 800],
                "required_samples": ["latin_extended"]},
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def axes(font: TTFont) -> list[dict]:
    return [{"tag": axis.axisTag, "min": axis.minValue, "default": axis.defaultValue,
             "max": axis.maxValue} for axis in font["fvar"].axes]


def names(font: TTFont) -> dict:
    return {str(name_id): sorted({item.toUnicode() for item in font["name"].names if item.nameID == name_id})
            for name_id in (1, 2, 4, 5, 6, 16, 17)}


def ranges(cmap: dict) -> dict:
    result = {}
    for label, start, end in (("cjk_unified", 0x4E00, 0x9FFF), ("cjk_extension_a", 0x3400, 0x4DBF),
                              ("cjk_extension_b", 0x20000, 0x2A6DF), ("basic_latin_printable", 0x20, 0x7E),
                              ("latin_1_supplement", 0xA0, 0xFF)):
        covered = sum(point in cmap for point in range(start, end + 1))
        result[label] = {"covered": covered, "range_size": end - start + 1,
                         "complete": covered == end - start + 1}
    return result


def build_and_verify(build: bool) -> dict:
    source = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    tools = json.loads((ROOT / "generation" / "tools-manifest.json").read_text(encoding="utf-8"))
    for record in source["files"]:
        path = ROOT / record["path"]
        require(path.stat().st_size == record["byte_length"] and sha(path) == record["sha256"],
                f"original source changed: {record['path']}")
    for record in tools["files"]:
        path = ROOT / "generation" / record["path"]
        require(path.stat().st_size == record["byte_length"] and sha(path) == record["sha256"], "tool wheel changed")
        for license_record in record["licenses"]:
            require(sha(ROOT / "generation" / license_record["path"]) == license_record["sha256"], "tool licence changed")
    require(importlib.metadata.version("fonttools") == "4.65.0", "unexpected FontTools version")
    require(importlib.metadata.version("Brotli") == "1.2.0", "unexpected Brotli version")
    results = []
    for key, spec in FAMILIES.items():
        original = ROOT / "upstream" / key / spec["original"]
        destination = ROOT / spec["woff2"]
        original_font = TTFont(original, recalcTimestamp=False)
        if build:
            require(not destination.exists(), "refuse to overwrite an existing generated font")
            original_font.flavor = "woff2"
            original_font.save(destination)
            original_font.close()
            original_font = TTFont(original, recalcTimestamp=False)
        require(destination.read_bytes()[:4] == b"wOF2", "invalid WOFF2 signature")
        decoded = TTFont(destination, recalcTimestamp=False)
        original_cmap = original_font.getBestCmap()
        decoded_cmap = decoded.getBestCmap()
        require(original_cmap == decoded_cmap, "WOFF2 cmap/glyph mappings changed")
        require(original_font.getGlyphOrder() == decoded.getGlyphOrder(), "glyph order changed")
        require(axes(original_font) == axes(decoded), "variable font axes changed")
        require(names(original_font) == names(decoded), "original font names changed")
        require(set(original_font.keys()) == set(decoded.keys()), "font table inventory changed")
        require(original_font["hmtx"].metrics == decoded["hmtx"].metrics, "horizontal glyph metrics changed")
        for name in original_font.getGlyphOrder():
            left = original_font["glyf"][name].getCoordinates(original_font["glyf"])
            right = decoded["glyf"][name].getCoordinates(decoded["glyf"])
            require(left == right, f"glyph outline changed: {name}")
        weight = next(axis for axis in axes(decoded) if axis["tag"] == "wght")
        require([weight["min"], weight["max"]] == spec["weight"], "unexpected weight range")
        family_names = names(decoded)["16"] or names(decoded)["1"]
        require(spec["family"] in family_names, "unexpected source family")
        sample_results = {}
        for label, text in SAMPLES.items():
            points = sorted({ord(character) for character in text})
            missing = [f"U+{point:04X} {chr(point)}" for point in points if point not in decoded_cmap]
            sample_results[label] = {"text": text, "unique_codepoints": len(points), "missing": missing}
            if label in spec["required_samples"]:
                require(not missing, f"required sample glyphs absent: {spec['family']}/{label}: {missing}")
        result = {"family": spec["family"], "css_alias": spec["alias"],
                  "original_path": original.relative_to(ROOT).as_posix(), "original_sha256": sha(original),
                  "woff2_path": destination.relative_to(ROOT).as_posix(), "woff2_sha256": sha(destination),
                  "woff2_bytes": destination.stat().st_size, "axes": axes(decoded), "names": names(decoded),
                  "cmap_codepoint_count": len(decoded_cmap), "glyph_count": len(decoded.getGlyphOrder()),
                  "range_coverage": ranges(decoded_cmap), "sample_coverage": sample_results,
                  "verification": {"full_unsubsetted_cmap": True, "all_glyph_outline_coordinates": True,
                                   "horizontal_metrics": True, "names_tables_and_axes": True}}
        results.append(result)
        original_font.close()
        decoded.close()
        print(f"verified {spec['family']}: {result['cmap_codepoint_count']} codepoints; {result['glyph_count']} glyphs; {result['woff2_bytes']} WOFF2 bytes", flush=True)
    return {"schema": "eeas.offline-web-fonts.v1", "source_repository": source["repository"], "source_commit": source["commit"],
            "generator": {"python": platform.python_version(), "fonttools": importlib.metadata.version("fonttools"),
                          "Brotli": importlib.metadata.version("Brotli"), "script_sha256": sha(Path(__file__))},
            "fonts": results, "subsetting": "NONE", "runtime_fetch": "NONE",
            "qualification": {"binary_and_cmap": "VERIFIED", "browser_visual": "NOT_RUN", "native_windows": "NOT_RUN"},
            "limitations": "Coverage is measured from the actual cmap; it is not a claim to cover all CJK extensions, emoji or every script. Browser loading/layout/rendering requires separate UI evidence."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    arguments = parser.parse_args()
    result = build_and_verify(arguments.build)
    target = ROOT / "manifest.json"
    if arguments.build:
        with target.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    else:
        existing = json.loads(target.read_text(encoding="utf-8"))
        # Historical generator provenance remains unchanged when another host
        # verifies the same font bytes; Python patch level is not font identity.
        for key in ("schema", "source_repository", "source_commit", "fonts", "subsetting", "runtime_fetch"):
            require(result[key] == existing[key], f"frozen font manifest changed: {key}")
        for key in ("fonttools", "Brotli"):
            require(result["generator"][key] == existing["generator"][key], "unexpected verifier toolchain")
    print("full-font checks completed; browser and native Windows qualification NOT_RUN", flush=True)
