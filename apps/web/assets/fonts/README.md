# Offline Studio fonts

Two full variable fonts are available as local WOFF2 assets. No CDN, network fetch, account cache or installed system font is needed at runtime. Original TTFs, original OFL texts, pinned Google Fonts metadata and acquisition hashes are retained under `upstream/` and `sources.json`.

| CSS alias recommended to the UI owner | Local path relative to `apps/web/` | Actual weight axis | WOFF2 bytes |
| --- | --- | --- | --- |
| `Studio Manrope` | `assets/fonts/manrope-variable.woff2` | 200–800 | 53,892 |
| `Studio Sans SC` | `assets/fonts/noto-sans-sc-variable.woff2` | 100–900 | 7,782,072 |

CSS aliases do not rename or change the original font's internal names. Define each as a normal-style variable face with its full weight range and `font-display: swap`, then use `"Studio Manrope", "Studio Sans SC", sans-serif`. WOFF2 is full and unsubsetted; the large Chinese font trades initial same-origin transfer size for complete preservation of this upstream font. The source variable defaults are Thin 100 / ExtraLight 200, so the UI should explicitly request its intended body/title weights through CSS.

Official source repository: <https://github.com/google/fonts>, pinned commit `66928797f271fed5ee428544e7229685e2d90127`. Each family's `OFL.txt` is unchanged. Official pinned metadata also identifies the respective upstream Noto/Manrope source commits. The local manifest records actual family/version names, original and WOFF2 SHA256, axes, cmap counts and measured Unicode coverage. No glyphs, outlines, names, licences or weight ranges were altered during format conversion.

The full FontTools decode comparison checked all 31,036 Noto Sans SC glyph outlines and 742 Manrope glyph outlines, glyph order, cmap mappings, metrics, name/table inventories and variable axes. Noto Sans SC has 30,890 cmap codepoints. Coverage is 20,976/20,992 in U+4E00–9FFF, 6,582/6,592 in Extension A and 54/42,720 in Extension B. This is not all extended CJK or emoji. Manrope has 678 cmap codepoints and covers the tested Latin accents, including é/ß/ï. The sampled `⌘⏎⚠✓` are missing in Manrope and present in the selected Noto Sans SC; use the font fallback or explicit icons.

`generation/ui-source-coverage-806d230.json` records static actual Han coverage for UI source commit `806d2303cfd176f592efd2b9d56e53a34a7d10be`: 370 distinct HTML and 428 distinct JS Han characters, zero missing. It includes the original UI-file hashes and actual characters, including comments. That check is distinct from browser text rendering and does not qualify future changed UI source.

FontTools 4.65.0 and Brotli 1.2.0 are **developer generation/inspection tools only**, not application runtime dependencies. `generation/wheels/` contains the pure FontTools wheel and original CPython 3.10 Linux x86_64/Windows AMD64 Brotli wheels. `generation/tools-manifest.json` binds official PyPI URLs/hashes and original extracted wheel licences. `requirements.txt` includes permitted hashes for both target platforms. Windows generation has not been run.

To verify the existing assets offline with Python 3.10, use a separate virtual environment:

```text
python -m venv .font-tools
# Windows: .font-tools\Scripts\python.exe; Linux: .font-tools/bin/python
<font-tools-python> -m pip install --isolated --disable-pip-version-check --no-index --no-cache-dir --require-hashes --find-links apps/web/assets/fonts/generation/wheels --requirement apps/web/assets/fonts/generation/requirements.txt
<font-tools-python> apps/web/assets/fonts/generation/build_and_verify.py
```

Verification preserves historical `manifest.json` build provenance rather than rewriting it for the verifier's host/Python patch version. `--build` is an explicit developer command that refuses to overwrite an existing WOFF2 or manifest; use a new clean destination/version when rebuilding. The online `acquire_*.py` commands are developer acquisition utilities, never application startup or offline build dependencies. They verify public HTTPS sources and bounded download sizes. Curl is only an optional online acquisition fallback for a failing Python TLS backend; the packaged offline conversion needs no curl.

Original build evidence: `reports/E07-offline-studio-fonts-build/20261013T011314621399Z`. The build completed exit 0 and is separate from the later verification command after portability handling was clarified. A PyPI TLS-acquisition FAIL remains in `reports/E07-offline-font-generation-tools/20261013T010911762016Z`, with recovery in `E07-offline-font-generation-tools-recovery/20261013T011207682023Z`. The build's owned processes were temporarily SIGSTOP/SIGCONT paused during the parent team's exclusive reference-browser window; the original run and freeze were preserved.

No HTML/CSS/JS was modified by this asset slice. Browser loading, layout, visual quality, native Windows rendering and complete new UI regression tests remain separate checks.

公开源码快照随附字体许可与来源清单。仓库根目录的 `scripts/prepare_source_checkout.py` 从固定上游取得原始 TTF，重新生成并核对运行用 WOFF2 的哈希。旧版离线生成用的依赖轮子和报告需要单独取得。
