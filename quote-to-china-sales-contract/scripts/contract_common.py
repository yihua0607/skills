from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W, "r": R}


def qn(local: str) -> str:
    return f"{{{W}}}{local}"


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def quotation_skill_root() -> Path:
    return skill_root().parent / "quotation-generator"


def load_entities() -> dict:
    path = quotation_skill_root() / "config" / "entities.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identify_entity(quotation: Path) -> tuple[str, dict]:
    entities = load_entities()
    configured = {}
    for key, cfg in entities.items():
        if key.startswith("_") or not isinstance(cfg, dict) or "contract" not in cfg:
            continue
        image = quotation_skill_root() / "assets" / cfg["header_image"]["file"]
        configured[_sha256(image.read_bytes())] = (key, cfg)
    with zipfile.ZipFile(quotation) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/"):
                match = configured.get(_sha256(zf.read(name)))
                if match:
                    return match
    raise ValueError("无法通过报价单页眉图片匹配签约主体；请先更新统一主体配置或报价单页眉。")


def document_text(docx: Path) -> etree._Element:
    with zipfile.ZipFile(docx) as zf:
        return etree.fromstring(zf.read("word/document.xml"))


def extract_quotation_fields(quotation: Path) -> dict[str, str]:
    root = document_text(quotation)
    result = {"customer_name": "", "contact_name": "", "contact_info": "", "contract_no": ""}
    labels = {
        "公司名称": "customer_name",
        "联系人": "contact_name",
        "联系方式": "contact_info",
        "合同号": "contract_no",
    }
    for p in root.xpath("//w:body//w:p", namespaces=NS):
        text = "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
        for label, key in labels.items():
            match = re.match(rf"^{label}\s*[：:]\s*(.*)$", text)
            if match:
                result[key] = match.group(1).strip()
    result["contract_no"] = result["contract_no"].upper()
    return result


def extract_service_name(quotation: Path) -> str:
    """Extract the optional service summary from a quotation title.

    This function is intentionally separate from the default quotation-field
    extraction path: contracts do not include a service row unless the user
    explicitly asks for it.
    """
    root = document_text(quotation)
    candidates = []
    for p in root.xpath("//w:body//w:p", namespaces=NS):
        text = "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
        text = re.sub(r"[（(]样例[）)]$", "", text).strip()
        if text.endswith("服务方案"):
            name = text[: -len("服务方案")].strip()
            if name and name not in candidates:
                candidates.append(name)
    if len(candidates) != 1:
        raise ValueError(f"无法唯一识别报价单方案标题中的服务名称: {candidates}")
    return candidates[0]


def split_contact(value: str) -> tuple[str, str]:
    value = value.strip()
    if not value:
        return "", ""
    if "@" in value:
        return value, ""
    compact = re.sub(r"[\s()+-]", "", value)
    if compact.isdigit() and 6 <= len(compact) <= 20:
        return "", value
    return value, ""


def tag_of(sdt: etree._Element) -> str:
    tag = sdt.find("w:sdtPr/w:tag", NS)
    return tag.get(qn("val"), "") if tag is not None else ""


def controls_by_tag(root: etree._Element) -> dict[str, etree._Element]:
    controls = {}
    for sdt in root.xpath("//w:sdt", namespaces=NS):
        tag = tag_of(sdt)
        if tag:
            if tag in controls:
                raise ValueError(f"模板内容控件标签重复: {tag}")
            controls[tag] = sdt
    return controls
