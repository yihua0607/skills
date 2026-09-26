#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

from contract_common import NS, W, extract_quotation_fields, identify_entity, qn


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a generated China-entity Indonesia service contract")
    parser.add_argument("contract"); parser.add_argument("--quotation", required=True)
    args = parser.parse_args(); contract = Path(args.contract); quotation = Path(args.quotation)
    _, cfg = identify_entity(quotation); q = extract_quotation_fields(quotation); expected = {"company": cfg["company"], **cfg["contract"]}
    issues = []
    with zipfile.ZipFile(contract) as zf:
        root = etree.fromstring(zf.read("word/document.xml")); settings = etree.fromstring(zf.read("word/settings.xml")); names = set(zf.namelist())
        text = "".join(root.xpath("//w:t/text()", namespaces=NS))
        if settings.xpath("//w:documentProtection", namespaces=NS): issues.append("存在文档级保护")
        locks = root.xpath("//w:sdtPr/w:lock", namespaces=NS)
        if len(locks) != 7: issues.append(f"乙方锁定字段数量不是7: {len(locks)}")
        expected_lock_tags = {"山海图单位名称", "地址", "山海图授权代表人", "山海图授权代表人的职务", "party_b_email", "party_b_signature_name", "party_b_signature_rep"}
        actual_lock_tags = set()
        for lock in locks:
            sdt = lock.getparent().getparent()
            tag = sdt.find("w:sdtPr/w:tag", NS)
            if tag is not None: actual_lock_tags.add(tag.get(qn("val"), ""))
            if sdt.xpath(".//w:permStart", namespaces=NS): issues.append("乙方锁定字段错误地标为可编辑区域")
        if actual_lock_tags != expected_lock_tags: issues.append("乙方七处锁定字段标签不正确")
        starts = root.xpath("//w:permStart", namespaces=NS); ends = root.xpath("//w:permEnd", namespaces=NS)
        start_ids = Counter(node.get(qn("id"), "") for node in starts); end_ids = Counter(node.get(qn("id"), "") for node in ends)
        if start_ids != end_ids or any(count != 1 for count in start_ids.values()): issues.append("逐字段可编辑区域标记未一一配对")
        if not starts or any(not value.startswith("910") for value in start_ids): issues.append("存在来源附件旧权限标记或缺少生成的输入提示")
        if len(starts) not in {26, 27}: issues.append(f"逐字段可编辑区域数量异常: {len(starts)}")
        for value, label in ((expected["company"], "乙方名称"), (expected["address"], "乙方地址"), (expected["representative"], "乙方代表人"), (expected["email"], "乙方邮箱"), (q["customer_name"], "甲方名称"), (q["contact_name"], "甲方代表人"), (q["contract_no"], "合同编号"), (expected["dispute_place"], "争议地点"), (expected["arbitration"], "仲裁机构")):
            if value and value not in text: issues.append(f"缺少{label}: {value}")
        if re.search(r"[a-z]", q["contract_no"]): issues.append("报价单合同编号含小写")
        contract_runs = [r for r in root.xpath("//w:r", namespaces=NS) if q["contract_no"] and q["contract_no"] in "".join(r.xpath(".//w:t/text()", namespaces=NS))]
        if not contract_runs or contract_runs[0].find("w:rPr/w:caps", NS) is None: issues.append("合同首页编号缺少w:caps")
        dropdowns = {}
        for sdt in root.xpath("//w:sdt[w:sdtPr/w:dropDownList]", namespaces=NS):
            current = "".join(sdt.xpath(".//w:sdtContent//w:t/text()", namespaces=NS))
            dropdowns[current] = len(sdt.xpath("./w:sdtPr/w:dropDownList/w:listItem", namespaces=NS))
        if dropdowns.get(expected["dispute_place"]) != 8: issues.append("争议地点选择列表不完整或不是只选下拉列表")
        if dropdowns.get(expected["arbitration"]) != 11: issues.append("仲裁机构选择列表不完整或不是只选下拉列表")
        if root.xpath('//w:sdt[w:sdtPr/w:tag[@w:val="dispute_place" or @w:val="arbitration"]]/w:sdtPr/w:comboBox', namespaces=NS):
            issues.append("争议解决控件仍允许自由输入")
        for tag in ("dispute_place", "arbitration"):
            nodes = root.xpath(f'//w:sdt[w:sdtPr/w:tag[@w:val="{tag}"]]', namespaces=NS)
            if len(nodes) != 1 or len(nodes[0].xpath(".//w:permStart", namespaces=NS)) != 1: issues.append(f"{tag}缺少逐字段输入提示")
        clause_six_ranges = sum(len(p.xpath(".//w:permStart", namespaces=NS)) for p in root.xpath('//w:p[contains(string(.),"最高罚款达到")]', namespaces=NS))
        if clause_six_ranges != 4: issues.append(f"第6条百分比输入提示不是4处: {clause_six_ranges}")
        for part in ("word/header1.xml", "word/footer1.xml"):
            if part not in names: issues.append(f"缺少{part}")
        for rel in root.xpath("//w:sectPr/w:headerReference|//w:sectPr/w:footerReference", namespaces=NS):
            if rel.get(f"{{{NS['r']}}}id") is None: issues.append("节的页眉页脚引用无关系ID")
    if issues:
        for issue in issues: print("❌", issue)
        raise SystemExit(1)
    print("✅ 合同结构、主体、锁定、编号、争议下拉及页眉页脚校验通过")


if __name__ == "__main__":
    main()
