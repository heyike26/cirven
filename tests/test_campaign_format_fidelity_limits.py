"""Connector shapes count against the unchanged physical diagram-node cap."""
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

import campaign_format_probes as formats

V = "http://schemas.microsoft.com/office/visio/2012/main"


def test_fidelity_counts_real_connector_shapes_without_expanding_legacy_source_limit():
    def outline(children):
        return formats.source_outline({"text": "# Original root\n" + ''.join(f"## Original child {n}\n" for n in range(children))})
    admitted = formats.outline_archive(outline(31), "vsdx", include_hierarchy_links=True)
    with zipfile.ZipFile(io.BytesIO(admitted)) as archive:
        page = ET.fromstring(archive.read("visio/pages/page1.xml"))
        assert len(list(page.iter("{" + V + "}Shape"))) == 63
        assert len(page.findall("./{" + V + "}Connects/{" + V + "}Connect")) == 62
    with pytest.raises(ValueError, match="at most64 actual diagram nodes including connector shapes"):
        formats.outline_archive(outline(32), "vsdx", include_hierarchy_links=True)
    legacy = formats.outline_archive(outline(32), "vsdx")
    with zipfile.ZipFile(io.BytesIO(legacy)) as archive:
        page = ET.fromstring(archive.read("visio/pages/page1.xml"))
        assert len(list(page.iter("{" + V + "}Shape"))) == 33
        assert not page.findall("./{" + V + "}Connects/{" + V + "}Connect")
