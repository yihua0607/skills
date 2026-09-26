#!/usr/bin/env python3
from __future__ import annotations

import argparse
import zipfile
from copy import deepcopy
from pathlib import Path

from lxml import etree

from contract_common import NS, W


def merge(base: Path, quotation: Path, output: Path) -> int:
    with zipfile.ZipFile(base) as zb, zipfile.ZipFile(quotation) as zq:
        base_root = etree.fromstring(zb.read("word/document.xml"))
        quote_root = etree.fromstring(zq.read("word/document.xml"))
        base_body = base_root.find(f"{{{W}}}body")
        quote_body = quote_root.find(f"{{{W}}}body")
        if quote_body.xpath(".//w:drawing | .//w:pict", namespaces=NS):
            raise ValueError("报价单正文含图片/绘图；当前安全合并器无法保留其关系。")
        base_sect = base_body.find("w:sectPr", NS)
        if base_sect is not None:
            base_body.remove(base_sect)
        children = list(quote_body)
        if children and children[-1].tag == f"{{{W}}}sectPr":
            children = children[:-1]
        for child in children:
            base_body.append(deepcopy(child))
        if base_sect is not None:
            base_body.append(base_sect)
        document = etree.tostring(base_root, xml_declaration=True, encoding="UTF-8", standalone=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zb.infolist():
                zout.writestr(info, document if info.filename == "word/document.xml" else zb.read(info.filename))
    return len(children)


def main() -> None:
    parser = argparse.ArgumentParser(description="Append a quotation to a prepared contract")
    parser.add_argument("base")
    parser.add_argument("quotation")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    count = merge(Path(args.base), Path(args.quotation), Path(args.output))
    print(f"[OK] appended {count} body elements")


if __name__ == "__main__":
    main()
