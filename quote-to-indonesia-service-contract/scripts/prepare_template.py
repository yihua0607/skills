#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from contract_common import NS, qn


CHINESE_INDEX_TAGS = {
    0: "contract_no", 1: "sales_name", 2: "sales_email",
    3: "sales_wechat", 14: "party_b_email", 22: "dispute_place",
    23: "arbitration", 26: "party_a_signature_name", 27: "party_b_signature_name",
    28: "party_a_signature_rep", 29: "party_b_signature_rep",
    30: "party_a_date", 31: "party_b_date",
}

TRILINGUAL_INDEX_TAGS = {
    0: "contract_no", 1: "sales_name", 2: "sales_email", 3: "sales_wechat",
    14: "party_b_email", 15: "termination_completed", 16: "termination_date_enabled",
    24: "termination_early", 28: "law_id", 29: "law_cn", 30: "law_en",
    31: "language_id", 32: "language_cn", 33: "language_en",
    34: "institution_id", 35: "location_id", 36: "location_cn", 37: "institution_cn",
    38: "institution_en", 39: "location_en", 40: "copies_id", 41: "copies_each_id",
    42: "copies_cn", 43: "copies_each_cn", 44: "copies_en", 45: "copies_each_en",
    46: "party_a_signature_name", 47: "party_b_signature_name",
    48: "party_a_signature_rep", 49: "party_b_signature_rep",
    50: "party_a_date", 51: "party_b_date",
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
        if len(sdts) == 32:
            index_tags = CHINESE_INDEX_TAGS
        elif len(sdts) == 52:
            index_tags = TRILINGUAL_INDEX_TAGS
        else:
            raise RuntimeError(f"unexpected template controls: {len(sdts)}")
        for index, tag_value in index_tags.items():
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
