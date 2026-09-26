#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lxml import etree

from contract_common import NS, R, controls_by_tag, extract_quotation_fields, qn, resolve_party_snapshot, split_contact
from merge_quotation import merge


PARTY_A_TAGS = {
    "客户名称": "customer_name", "客户地址": "address", "客户授权代表人": "representative",
    "代表人职务": "title", "客户邮箱": "email_or_wechat", "客户电话号": "phone",
}
PARTY_B_TAGS = {
    "山海图单位名称": "company", "地址": "address", "山海图授权代表人": "representative",
    "山海图授权代表人的职务": "title", "party_b_email": "email",
    "party_b_signature_name": "company", "party_b_signature_rep": "representative",
}
KEEP_TAGS = set(PARTY_B_TAGS) | {
    "contract_no", "dispute_place", "arbitration",
    "party_a_signature_name", "party_a_signature_rep",
}


def _set_text(sdt, value: str, caps: bool = False) -> None:
    nodes = sdt.xpath(".//w:sdtContent//w:t", namespaces=NS)
    if not nodes:
        raise ValueError("内容控件没有文本节点")
    nodes[0].text = value
    for node in nodes[1:]:
        node.text = ""
    placeholder = sdt.find("w:sdtPr/w:showingPlcHdr", NS)
    if placeholder is not None:
        placeholder.getparent().remove(placeholder)
    for run in sdt.xpath(".//w:sdtContent//w:r", namespaces=NS):
        rpr = run.find("w:rPr", NS)
        if rpr is None:
            rpr = etree.Element(qn("rPr")); run.insert(0, rpr)
        color = rpr.find("w:color", NS)
        if color is None:
            color = etree.SubElement(rpr, qn("color"))
        color.attrib.clear(); color.set(qn("val"), "000000")
        old_caps = rpr.find("w:caps", NS)
        if old_caps is not None:
            rpr.remove(old_caps)
        if caps:
            cap = etree.Element(qn("caps")); anchor = rpr.find("w:color", NS)
            rpr.insert(rpr.index(anchor), cap)


def _set_text_or_placeholder(sdt, value: str) -> None:
    if value:
        _set_text(sdt, value)
        return
    nodes = sdt.xpath(".//w:sdtContent//w:t", namespaces=NS)
    if not nodes:
        raise ValueError("内容控件没有文本节点")
    nodes[0].text = "Click or tap here to enter text."
    for node in nodes[1:]:
        node.text = ""
    props = sdt.find("w:sdtPr", NS)
    if props.find("w:showingPlcHdr", NS) is None:
        props.append(etree.Element(qn("showingPlcHdr")))


def _lock(sdt) -> None:
    props = sdt.find("w:sdtPr", NS)
    lock = props.find("w:lock", NS)
    if lock is None:
        lock = etree.SubElement(props, qn("lock"))
    lock.set(qn("val"), "sdtContentLocked")


def _unlock(sdt) -> None:
    props = sdt.find("w:sdtPr", NS); lock = props.find("w:lock", NS)
    if lock is not None:
        props.remove(lock)


def _make_select_only(sdt) -> None:
    props = sdt.find("w:sdtPr", NS)
    combo = props.find("w:comboBox", NS)
    if combo is not None:
        combo.tag = qn("dropDownList")
    if props.find("w:dropDownList", NS) is None:
        raise ValueError("争议解决控件缺少下拉选项列表")


def _unwrap(sdt) -> None:
    content = sdt.find("w:sdtContent", NS); parent = sdt.getparent(); pos = parent.index(sdt)
    for child in list(content):
        content.remove(child); parent.insert(pos, child); pos += 1
    parent.remove(sdt)


def _copy_header_and_footer(quote_root: Path, contract_root: Path) -> None:
    header_parts = list((quote_root / "word").glob("header*.xml"))
    if not header_parts:
        raise ValueError("报价单没有页眉部件")
    footer_parts = list((quote_root / "word").glob("footer*.xml"))
    for src in header_parts + footer_parts:
        shutil.copy2(src, contract_root / "word" / src.name)
        rel = quote_root / "word" / "_rels" / f"{src.name}.rels"
        if rel.exists():
            shutil.copy2(rel, contract_root / "word" / "_rels" / rel.name)
            rel_tree = etree.parse(str(rel))
            for node in rel_tree.getroot():
                target = node.get("Target", "")
                if target.startswith("media/"):
                    source_media = quote_root / "word" / target
                    dest_media = contract_root / "word" / target
                    dest_media.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_media, dest_media)


def _unify_header_footer_refs(docx: Path) -> None:
    """将合并后文档中所有 sectPr 的页眉页脚引用统一到同一组 rId。

    合并报价单后，报价单段落中的 sectPr 仍引用报价单原始的 rId（如 rId5/rId6），
    但输出文件使用的是合同模板的 rels，这些 rId 可能指向不同的部件。
    本函数从文档中第一个含有页眉或页脚引用的 sectPr 读取正确的 rId，
    然后将所有其他 sectPr 的 headerReference/footerReference 更新为相同的 rId。
    """
    with tempfile.TemporaryDirectory(prefix="unify-hf-") as temp:
        root = Path(temp) / "docx"
        with zipfile.ZipFile(docx) as zf:
            zf.extractall(root)
        doc_path = root / "word" / "document.xml"
        tree = etree.parse(str(doc_path))
        body = tree.find("w:body", NS)
        all_sects = body.xpath(".//w:sectPr", namespaces=NS)
        if not all_sects:
            return
        # 从第一个含有页眉或页脚引用的 sectPr 读取正确的 rId
        source_sect = None
        target_header_rids = {}
        target_footer_rids = {}
        for sect in all_sects:
            for ref in sect.xpath("w:headerReference", namespaces=NS):
                rtype = ref.get(qn("type"), "default")
                rid = ref.get(f"{{{R}}}id")
                if rid:
                    target_header_rids[rtype] = rid
                    source_sect = sect
            for ref in sect.xpath("w:footerReference", namespaces=NS):
                rtype = ref.get(qn("type"), "default")
                rid = ref.get(f"{{{R}}}id")
                if rid:
                    target_footer_rids[rtype] = rid
                    source_sect = sect
            if target_header_rids or target_footer_rids:
                break
        if source_sect is None or (not target_header_rids and not target_footer_rids):
            return
        changed = False
        for sect in all_sects:
            if sect is source_sect:
                continue
            for ref in sect.xpath("w:headerReference", namespaces=NS):
                rtype = ref.get(qn("type"), "default")
                if rtype in target_header_rids:
                    old = ref.get(f"{{{R}}}id")
                    new = target_header_rids[rtype]
                    if old != new:
                        ref.set(f"{{{R}}}id", new)
                        changed = True
            for ref in sect.xpath("w:footerReference", namespaces=NS):
                rtype = ref.get(qn("type"), "default")
                if rtype in target_footer_rids:
                    old = ref.get(f"{{{R}}}id")
                    new = target_footer_rids[rtype]
                    if old != new:
                        ref.set(f"{{{R}}}id", new)
                        changed = True
        if changed:
            doc_path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))
            rebuilt = Path(temp) / "rebuilt.docx"
            with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in sorted(root.rglob("*")):
                    if item.is_file():
                        zf.write(item, item.relative_to(root).as_posix())
            shutil.copy2(rebuilt, docx)


def _enforce_scheme_a(docx: Path, allowed_permission_ids: set[str] | None = None) -> None:
    """Remove document protection and retain only generated field markers.

    Scheme A leaves the document generally editable and relies only on the
    seven Party B content-control locks.
    """
    with tempfile.TemporaryDirectory(prefix="scheme-a-") as temp:
        root = Path(temp) / "docx"
        with zipfile.ZipFile(docx) as zf:
            zf.extractall(root)
        document_path = root / "word" / "document.xml"
        document = etree.parse(str(document_path))
        allowed_permission_ids = allowed_permission_ids or set()
        for node in document.xpath("//w:permStart | //w:permEnd", namespaces=NS):
            if node.get(qn("id"), "") not in allowed_permission_ids:
                node.getparent().remove(node)
        document_path.write_bytes(etree.tostring(document, xml_declaration=True, encoding="UTF-8", standalone=True))
        settings_path = root / "word" / "settings.xml"
        settings = etree.parse(str(settings_path))
        for node in settings.xpath("//w:documentProtection", namespaces=NS):
            node.getparent().remove(node)
        settings_path.write_bytes(etree.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True))
        rebuilt = Path(temp) / "rebuilt.docx"
        with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(root.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(root).as_posix())
        shutil.copy2(rebuilt, docx)


def _mark_editable_range(sdt, permission_id: str) -> None:
    content = sdt.find("w:sdtContent", NS)
    start = etree.Element(qn("permStart"))
    start.set(qn("id"), permission_id)
    start.set(qn("edGrp"), "everyone")
    end = etree.Element(qn("permEnd"))
    end.set(qn("id"), permission_id)
    content.insert(0, start)
    content.append(end)


def _mark_run_editable(run, permission_id: str) -> None:
    parent = run.getparent()
    position = parent.index(run)
    start = etree.Element(qn("permStart"))
    start.set(qn("id"), permission_id)
    start.set(qn("edGrp"), "everyone")
    end = etree.Element(qn("permEnd"))
    end.set(qn("id"), permission_id)
    parent.insert(position, start)
    parent.insert(position + 2, end)


def _mark_run_span_editable(first_run, last_run, permission_id: str) -> None:
    parent = first_run.getparent()
    if last_run.getparent() is not parent:
        raise ValueError("可编辑区域的文本运行不在同一段落")
    first_position = parent.index(first_run)
    last_position = parent.index(last_run)
    start = etree.Element(qn("permStart"))
    start.set(qn("id"), permission_id)
    start.set(qn("edGrp"), "everyone")
    end = etree.Element(qn("permEnd"))
    end.set(qn("id"), permission_id)
    parent.insert(first_position, start)
    parent.insert(last_position + 2, end)


def build(template: Path, quotation: Path, output: Path, sign_date: str) -> None:
    party_b = resolve_party_snapshot(quotation)
    if party_b["country"] == "ID":
        build_trilingual(template, quotation, output, sign_date, party_b)
        return
    entity = party_b["entity"]
    qfields = extract_quotation_fields(quotation)
    email_or_wechat, phone = split_contact(qfields["contact_info"])
    party_a = {"customer_name": qfields["customer_name"], "address": "", "representative": qfields["contact_name"],
               "title": "", "email_or_wechat": email_or_wechat, "phone": phone}
    with tempfile.TemporaryDirectory(prefix="china-contract-") as temp:
        root = Path(temp) / "contract"; quote_root = Path(temp) / "quote"
        with zipfile.ZipFile(template) as zf: zf.extractall(root)
        with zipfile.ZipFile(quotation) as zf: zf.extractall(quote_root)
        _copy_header_and_footer(quote_root, root)
        doc_path = root / "word" / "document.xml"; tree = etree.parse(str(doc_path)); controls = controls_by_tag(tree.getroot())
        required = set(PARTY_A_TAGS) | set(PARTY_B_TAGS) | {"contract_no", "sales_name", "sales_email", "sales_wechat", "dispute_place", "arbitration", "party_a_signature_name", "party_a_signature_rep", "party_a_date", "party_b_date"}
        missing = sorted(required - set(controls))
        if missing: raise ValueError("模板缺少内容控件标签: " + ", ".join(missing))
        _set_text(controls["contract_no"], qfields["contract_no"], caps=True)
        for tag in ("sales_name", "sales_email", "sales_wechat"): _set_text(controls[tag], "")
        for tag, key in PARTY_A_TAGS.items(): _set_text(controls[tag], party_a[key])
        for tag, key in PARTY_B_TAGS.items(): _set_text(controls[tag], party_b[key]); _lock(controls[tag])
        if party_b["dispute_place"]:
            _set_text(controls["dispute_place"], party_b["dispute_place"])
        if party_b["arbitration"]:
            _set_text(controls["arbitration"], party_b["arbitration"])
        _make_select_only(controls["dispute_place"])
        _make_select_only(controls["arbitration"])
        _set_text_or_placeholder(controls["party_a_signature_name"], party_a["customer_name"])
        _set_text_or_placeholder(controls["party_a_signature_rep"], party_a["representative"])
        _set_text(controls["party_a_date"], sign_date); _set_text(controls["party_b_date"], sign_date)
        editable_permission_ids = set()
        next_permission_id = 910000
        for tag, sdt in list(controls.items()):
            if tag not in PARTY_B_TAGS and sdt.getparent() is not None:
                permission_id = str(next_permission_id)
                next_permission_id += 1
                _mark_editable_range(sdt, permission_id)
                editable_permission_ids.add(permission_id)
            if tag in KEEP_TAGS:
                if tag not in PARTY_B_TAGS:
                    _unlock(sdt)
                continue
            _unlock(sdt)
            if tag in {"dispute_place", "arbitration"}: continue
            if sdt.getparent() is not None: _unwrap(sdt)
        for sdt in tree.xpath("//w:sdt[not(w:sdtPr/w:tag) and not(w:sdtPr/w:lock)]", namespaces=NS):
            visible_text = "".join(sdt.xpath(".//w:sdtContent//w:t/text()", namespaces=NS)).strip()
            if visible_text in {"☑", "☐"}:
                continue
            for marker in sdt.xpath(".//w:permStart | .//w:permEnd", namespaces=NS):
                marker.getparent().remove(marker)
            permission_id = str(next_permission_id)
            next_permission_id += 1
            _mark_editable_range(sdt, permission_id)
            editable_permission_ids.add(permission_id)
        for text_node in tree.xpath('//w:body//w:t[text()="1%" or text()="5%"]', namespaces=NS):
            run = text_node.getparent()
            permission_id = str(next_permission_id)
            next_permission_id += 1
            _mark_run_editable(run, permission_id)
            editable_permission_ids.add(permission_id)
        for five_node in tree.xpath('//w:body//w:t[text()="5"]', namespaces=NS):
            five_run = five_node.getparent()
            parent = five_run.getparent()
            position = parent.index(five_run)
            following_runs = [node for node in parent[position + 1:] if node.tag == qn("r")]
            if not following_runs:
                continue
            percent_run = following_runs[0]
            percent_text = "".join(percent_run.xpath(".//w:t/text()", namespaces=NS))
            if percent_text != "%":
                continue
            permission_id = str(next_permission_id)
            next_permission_id += 1
            _mark_run_span_editable(five_run, percent_run, permission_id)
            editable_permission_ids.add(permission_id)
        body = tree.find("w:body", NS); sect = body.find("w:sectPr", NS)
        p = etree.Element(qn("p")); r = etree.SubElement(p, qn("r")); br = etree.SubElement(r, qn("br")); br.set(qn("type"), "page")
        body.insert(body.index(sect), p) if sect is not None else body.append(p)
        doc_path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))
        settings_path = root / "word" / "settings.xml"; settings = etree.parse(str(settings_path))
        for node in settings.xpath("//w:documentProtection", namespaces=NS): node.getparent().remove(node)
        settings_path.write_bytes(etree.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True))
        base = Path(temp) / "base.docx"
        with zipfile.ZipFile(base, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(root.rglob("*")):
                if item.is_file(): zf.write(item, item.relative_to(root).as_posix())
        merge(base, quotation, output)
        _unify_header_footer_refs(output)
        _enforce_scheme_a(output, editable_permission_ids)
    print(f"[OK] entity={entity} company={party_b['company']} output={output}")


def build_trilingual(template: Path, quotation: Path, output: Path, sign_date: str, party_b: dict) -> None:
    qfields = extract_quotation_fields(quotation)
    email_or_wechat, phone = split_contact(qfields["contact_info"])
    party_a = {
        "customer_name": qfields["customer_name"], "address": "",
        "representative": qfields["contact_name"], "title": "",
        "email_or_wechat": email_or_wechat, "phone": phone,
    }
    with tempfile.TemporaryDirectory(prefix="trilingual-contract-") as temp:
        root = Path(temp) / "contract"; quote_root = Path(temp) / "quote"
        with zipfile.ZipFile(template) as zf: zf.extractall(root)
        with zipfile.ZipFile(quotation) as zf: zf.extractall(quote_root)
        _copy_header_and_footer(quote_root, root)
        doc_path = root / "word" / "document.xml"
        tree = etree.parse(str(doc_path)); controls = controls_by_tag(tree.getroot())
        required = set(PARTY_A_TAGS) | set(PARTY_B_TAGS) | {
            "contract_no", "sales_name", "sales_email", "sales_wechat",
            "termination_completed", "termination_date_enabled", "termination_early",
            "law_id", "law_cn", "law_en", "language_id", "language_cn", "language_en",
            "institution_id", "location_id", "location_cn", "institution_cn",
            "institution_en", "location_en", "copies_id", "copies_each_id",
            "copies_cn", "copies_each_cn", "copies_en", "copies_each_en",
            "party_a_signature_name", "party_a_signature_rep", "party_a_date", "party_b_date",
        }
        missing = sorted(required - set(controls))
        if missing: raise ValueError("三语模板缺少内容控件标签: " + ", ".join(missing))
        _set_text(controls["contract_no"], qfields["contract_no"], caps=True)
        for tag in ("sales_name", "sales_email", "sales_wechat"): _set_text(controls[tag], "")
        for tag, key in PARTY_A_TAGS.items(): _set_text(controls[tag], party_a[key])
        for tag, key in PARTY_B_TAGS.items(): _set_text(controls[tag], str(party_b[key])); _lock(controls[tag])
        _set_text(controls["termination_completed"], "☑")
        _set_text(controls["termination_date_enabled"], "☐")
        _set_text(controls["termination_early"], "☑")
        localized = party_b["localized"]
        localized_tags = {
            "law_id": localized["law"]["id"], "law_cn": localized["law"]["cn"], "law_en": localized["law"]["en"],
            "language_id": localized["language"]["id"], "language_cn": localized["language"]["cn"], "language_en": localized["language"]["en"],
            "institution_id": localized["institution"]["id"], "institution_cn": localized["institution"]["cn"], "institution_en": localized["institution"]["en"],
            "location_id": localized["location"]["id"], "location_cn": localized["location"]["cn"], "location_en": localized["location"]["en"],
        }
        for tag, value in localized_tags.items():
            _set_text(controls[tag], value); _make_select_only(controls[tag])
        for tag, value in {
            "copies_id": "2", "copies_each_id": "1", "copies_cn": "2",
            "copies_each_cn": "1", "copies_en": "2", "copies_each_en": "1",
        }.items(): _set_text(controls[tag], value)
        _set_text_or_placeholder(controls["party_a_signature_name"], party_a["customer_name"])
        _set_text_or_placeholder(controls["party_a_signature_rep"], party_a["representative"])
        _set_text(controls["party_a_date"], sign_date); _set_text(controls["party_b_date"], sign_date)

        editable_permission_ids = set(); next_permission_id = 920000
        for tag, sdt in controls.items():
            for marker in sdt.xpath(".//w:permStart | .//w:permEnd", namespaces=NS):
                marker.getparent().remove(marker)
            if tag not in PARTY_B_TAGS:
                permission_id = str(next_permission_id); next_permission_id += 1
                _mark_editable_range(sdt, permission_id); editable_permission_ids.add(permission_id)
                _unlock(sdt)
        for sdt in tree.xpath("//w:sdt[not(w:sdtPr/w:tag)]", namespaces=NS):
            for marker in sdt.xpath(".//w:permStart | .//w:permEnd", namespaces=NS):
                marker.getparent().remove(marker)
            _unlock(sdt)
            permission_id = str(next_permission_id); next_permission_id += 1
            _mark_editable_range(sdt, permission_id); editable_permission_ids.add(permission_id)
        for text_node in tree.xpath('//w:body//w:t[text()="1%" or text()="5%"]', namespaces=NS):
            permission_id = str(next_permission_id); next_permission_id += 1
            _mark_run_editable(text_node.getparent(), permission_id); editable_permission_ids.add(permission_id)
        for five_node in tree.xpath('//w:body//w:t[text()="5"]', namespaces=NS):
            five_run = five_node.getparent(); parent = five_run.getparent(); position = parent.index(five_run)
            following_runs = [node for node in parent[position + 1:] if node.tag == qn("r")]
            if not following_runs:
                continue
            percent_run = following_runs[0]
            if "".join(percent_run.xpath(".//w:t/text()", namespaces=NS)) != "%":
                continue
            permission_id = str(next_permission_id); next_permission_id += 1
            _mark_run_span_editable(five_run, percent_run, permission_id); editable_permission_ids.add(permission_id)
        body = tree.find("w:body", NS); sect = body.find("w:sectPr", NS)
        page = etree.Element(qn("p")); run = etree.SubElement(page, qn("r")); br = etree.SubElement(run, qn("br")); br.set(qn("type"), "page")
        body.insert(body.index(sect), page) if sect is not None else body.append(page)
        doc_path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))
        settings_path = root / "word" / "settings.xml"; settings = etree.parse(str(settings_path))
        for node in settings.xpath("//w:documentProtection", namespaces=NS): node.getparent().remove(node)
        settings_path.write_bytes(etree.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True))
        base = Path(temp) / "base.docx"
        with zipfile.ZipFile(base, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(root.rglob("*")):
                if item.is_file(): zf.write(item, item.relative_to(root).as_posix())
        merge(base, quotation, output)
        _unify_header_footer_refs(output); _enforce_scheme_a(output, editable_permission_ids)
    print(f"[OK] entity={party_b['entity']} company={party_b['company']} output={output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an Indonesia service contract from a quotation")
    parser.add_argument("quotation"); parser.add_argument("--output", required=True)
    parser.add_argument("--template")
    parser.add_argument("--date")
    args = parser.parse_args()
    effective_date = args.date or datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat()
    y, m, d = map(int, effective_date.split("-"))
    quotation = Path(args.quotation)
    snapshot = resolve_party_snapshot(quotation)
    if args.template:
        template = Path(args.template)
    else:
        filename = "印尼服务合同-中文版本.docx" if snapshot["country"] == "CN" else "印尼服务合同--三语版本.docx"
        template = Path(__file__).resolve().parents[1] / "assets" / filename
    build(template, quotation, Path(args.output), f"{y}年{m}月{d}日")


if __name__ == "__main__":
    main()
