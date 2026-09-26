#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from contract_common import NS, qn


INDEX_TAGS = {
    0: "contract_no", 1: "service_name", 2: "sales_name", 3: "sales_email",
    4: "sales_wechat", 15: "party_b_email", 23: "dispute_place",
    24: "arbitration", 27: "party_a_signature_name", 28: "party_b_signature_name",
    29: "party_a_signature_rep", 30: "party_b_signature_rep",
    31: "party_a_date", 32: "party_b_date",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Add stable tags to the bundled contract template")
    parser.add_argument("template")
    args = parser.parse_args()
    path = Path(args.template).resolve()
    with tempfile.TemporaryDirectory(prefix="contract-template-tags-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(root)
        xml_path = root / "word" / "document.xml"
        tree = etree.parse(str(xml_path))
        sdts = tree.xpath("//w:sdt", namespaces=NS)
        if len(sdts) < 33:
            raise RuntimeError(f"unexpected template controls: {len(sdts)}")
        for index, tag_value in INDEX_TAGS.items():
            props = sdts[index].find("w:sdtPr", NS)
            tag = props.find("w:tag", NS)
            if tag is None:
                tag = etree.SubElement(props, qn("tag"))
            tag.set(qn("val"), tag_value)
        xml_path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))
        replacement = path.with_suffix(".tagged.docx")
        with zipfile.ZipFile(replacement, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(root.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(root).as_posix())
        replacement.replace(path)


if __name__ == "__main__":
    main()
