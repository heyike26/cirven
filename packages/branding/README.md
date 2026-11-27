# Local brand assets

`product.json` is the visible-name source; `icon.svg` is the canonical vector.
The selected three-part C/V mark uses the product graphite and cool-silver palette.
Its white channel is transparent negative space, not a raster background.
`icon-assets.json` binds the vector, same-geometry monochrome SVG and six PNGs
by exact bytes and SHA-256. The host and VSIX use these local bytes; neither
loads an SVG renderer, font, network resource or additional runtime dependency.

`build_icons.py` is an optional development tool. The current assets were built
with CairoSVG 2.8.2, Pillow 12.3.0 and Cairo 1.18.0; their versions are recorded
in the asset manifest. Run this script only in a development environment with
those tools installed. A different renderer build may produce different PNG
bytes and requires a new reviewed manifest. The source vector is never changed
by rendering. Locally edited derived files are rejected and preserved.

The vector subset supports local paths, basic shapes and gradients; it rejects
scripts, external references, fonts and embedded images. Monochrome output
inherits its host's foreground. Circles' SVG `pathLength` values are converted
to equivalent dash lengths because CairoSVG 2.8.2 does not implement that
normalization; other shapes with `pathLength` are rejected rather than silently
rendered with a different appearance. Supersampling is build-only and bounded.

This pipeline qualifies local asset provenance and PNG generation. It does not
qualify Windows tray rendering, VS Code placement or visual approval of a logo.
