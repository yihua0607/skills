#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

from contract_common import NS, controls_by_tag, extract_quotation_fields, qn, resolve_party_snapshot


def control_text(control) -> str:
    return "".join(control.xpath(".//w:sdtContent//w:t/text()", namespaces=NS))


def media_hashes(archive: zipfile.ZipFile) -> set[str]:
    return {
        hashlib.sha256(archive.read(name)).hexdigest()
        for name in archive.namelist() if name.startswith("word/media/")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a generated Indonesia service contract")
    parser.add_argument("contract"); parser.add_argument("--quotation", required=True)
    args = parser.parse_args()
    contract = Path(args.contract); quotation = Path(args.quotation)
    expected = resolve_party_snapshot(quotation); q = extract_quotation_fields(quotation)
    issues: list[str] = []

    with zipfile.ZipFile(contract) as zf, zipfile.ZipFile(quotation) as qz:
        names = set(zf.namelist())
        root = etree.fromstring(zf.read("word/document.xml"))
        settings = etree.fromstring(zf.read("word/settings.xml"))
        controls = controls_by_tag(root)
        text = "".join(root.xpath("//w:t/text()", namespaces=NS))
        if settings.xpath("//w:documentProtection", namespaces=NS):
            issues.append("存在文档级保护")

        expected_lock_tags = {
            "山海图单位名称", "地址", "山海图授权代表人", "山海图授权代表人的职务",
            "party_b_email", "party_b_signature_name", "party_b_signature_rep",
        }
        actual_lock_tags = set()
        for tag, control in controls.items():
            lock = control.find("w:sdtPr/w:lock", NS)
            if lock is not None and lock.get(qn("val")) == "sdtContentLocked":
                actual_lock_tags.add(tag)
                if control.xpath(".//w:permStart", namespaces=NS):
                    issues.append(f"乙方锁定字段错误地标为可编辑: {tag}")
        if actual_lock_tags != expected_lock_tags:
            issues.append(f"乙方七处锁定字段不正确: {sorted(actual_lock_tags)}")

        expected_values = {
            "山海图单位名称": expected["company"], "地址": expected["address"],
            "山海图授权代表人": expected["representative"],
            "山海图授权代表人的职务": expected["title"], "party_b_email": expected["email"],
            "party_b_signature_name": expected["company"],
            "party_b_signature_rep": expected["representative"],
        }
        for tag, value in expected_values.items():
            if tag not in controls:
                issues.append(f"缺少内容控件: {tag}")
            elif control_text(controls[tag]) != value:
                issues.append(f"{tag}不一致: {control_text(controls[tag])!r} != {value!r}")

        contract_control = controls.get("contract_no")
        if contract_control is None:
            issues.append("缺少合同编号控件")
        else:
            stored = control_text(contract_control)
            if stored != q["contract_no"] or re.search(r"[a-z]", stored):
                issues.append("合同编号未与报价单同步为大写")
            if contract_control.find("w:sdtPr/w:rPr/w:caps", NS) is None:
                issues.append("合同编号控件属性缺少w:caps")
            runs = contract_control.xpath(".//w:sdtContent//w:r", namespaces=NS)
            if not runs or any(run.find("w:rPr/w:caps", NS) is None for run in runs):
                issues.append("合同编号内容运行缺少w:caps")

        starts = root.xpath("//w:permStart", namespaces=NS); ends = root.xpath("//w:permEnd", namespaces=NS)
        start_ids = Counter(node.get(qn("id"), "") for node in starts)
        end_ids = Counter(node.get(qn("id"), "") for node in ends)
        if start_ids != end_ids or any(count != 1 for count in start_ids.values()):
            issues.append("逐字段可编辑区域标记未一一配对")

        if expected["country"] == "ID":
            localized = expected["localized"]
            localized_tags = {
                "law_id": localized["law"]["id"], "law_cn": localized["law"]["cn"], "law_en": localized["law"]["en"],
                "language_id": localized["language"]["id"], "language_cn": localized["language"]["cn"], "language_en": localized["language"]["en"],
                "institution_id": localized["institution"]["id"], "institution_cn": localized["institution"]["cn"], "institution_en": localized["institution"]["en"],
                "location_id": localized["location"]["id"], "location_cn": localized["location"]["cn"], "location_en": localized["location"]["en"],
            }
            for tag, value in localized_tags.items():
                control = controls.get(tag)
                if control is None or control_text(control) != value:
                    issues.append(f"三语默认值错误: {tag}")
                elif control.find("w:sdtPr/w:dropDownList", NS) is None or control.find("w:sdtPr/w:comboBox", NS) is not None:
                    issues.append(f"三语默认值不是只选下拉: {tag}")
            for tag, value in {"termination_completed": "☑", "termination_date_enabled": "☐", "termination_early": "☑"}.items():
                if tag not in controls or control_text(controls[tag]) != value:
                    issues.append(f"终止条件状态错误: {tag}")
            for required_text in ("PERJANJIAN", "服务合同", "SERVICE AGREEMENT"):
                if required_text not in text:
                    issues.append(f"三语文本缺失: {required_text}")
        else:
            if expected["dispute_place"]:
                if "dispute_place" not in controls or control_text(controls["dispute_place"]) != expected["dispute_place"]:
                    issues.append("争议地点不符合主体默认值")
            if expected["arbitration"]:
                if "arbitration" not in controls or control_text(controls["arbitration"]) != expected["arbitration"]:
                    issues.append("仲裁机构不符合主体默认值")

        for part in ("word/header1.xml", "word/footer1.xml"):
            if part not in names or part not in qz.namelist():
                issues.append(f"缺少{part}")
            elif zf.read(part) != qz.read(part):
                issues.append(f"{part}未原样继承报价单")
        if not (media_hashes(qz) & media_hashes(zf)):
            issues.append("合同未保留报价单页眉图片")
        if "word/footer1.xml" in names:
            footer = etree.fromstring(zf.read("word/footer1.xml"))
            fields = " ".join(footer.xpath("//w:instrText/text()", namespaces=NS))
            if "NUMPAGES" not in fields or "SECTIONPAGES" in fields:
                issues.append("页脚总页数字段不是NUMPAGES")

        quote_root = etree.fromstring(qz.read("word/document.xml"))
        quote_text = "".join(quote_root.xpath("//w:body//w:t/text()", namespaces=NS))
        if quote_text and quote_text not in text:
            issues.append("附后的报价单正文不完整")

        rels_name = "word/_rels/document.xml.rels"
        if rels_name in names:
            rels = etree.fromstring(zf.read(rels_name))
            rel_ids = {node.get("Id") for node in rels}
            for ref in root.xpath("//w:sectPr/w:headerReference|//w:sectPr/w:footerReference", namespaces=NS):
                rid = ref.get(f"{{{NS['r']}}}id")
                if rid not in rel_ids:
                    issues.append(f"无效页眉页脚关系: {rid}")

    if issues:
        for issue in issues: print("❌", issue)
        raise SystemExit(1)
    print("✅ 合同主体、模板字段、锁定、编号、三语条款、附件及页眉页脚校验通过")


if __name__ == "__main__":
    main()
