from __future__ import annotations

import hashlib
import io
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
    return json.loads((quotation_skill_root() / "config" / "entities.json").read_text(encoding="utf-8"))


def load_parties() -> dict:
    return json.loads((skill_root() / "references" / "parties.json").read_text(encoding="utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized(value: str) -> str:
    return re.sub(r"[.．\s\u00a0]+", "", value).casefold()


def _header_text(quotation: Path) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(quotation) as zf:
        for name in zf.namelist():
            if re.fullmatch(r"word/header\d+\.xml", name):
                root = etree.fromstring(zf.read(name))
                chunks.extend(root.xpath("//w:t/text()", namespaces=NS))
    return " ".join(chunks).strip()


def _visible_name_from_header(header_text: str, config: dict) -> str | None:
    candidates: list[str] = []
    for binding in config.get("quotationEntityBindings", {}).values():
        candidates.append(binding["headerName"])
    for party in config["parties"]:
        candidates.append(party["name"])
        candidates.extend(party.get("aliases", []))
    for candidate in sorted(set(candidates), key=len, reverse=True):
        if candidate in header_text:
            return candidate
    return None


def _perceptual_distance(first: bytes, second: bytes) -> float | None:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(first)) as a, Image.open(io.BytesIO(second)) as b:
            if abs((a.width / a.height) - (b.width / b.height)) > 0.05:
                return None
            a = a.convert("L").resize((64, 16))
            b = b.convert("L").resize((64, 16))
            ap = list(a.getdata())
            bp = list(b.getdata())
        return sum(abs(x - y) for x, y in zip(ap, bp)) / len(ap)
    except Exception:
        return None


def identify_entity(quotation: Path) -> tuple[str, dict]:
    """Identify the quotation entity without reading contract fields from it."""
    entities = load_entities()
    party_config = load_parties()
    bindings = party_config.get("quotationEntityBindings", {})
    eligible = {
        key: cfg for key, cfg in entities.items()
        if key in bindings and isinstance(cfg, dict) and cfg.get("header_image", {}).get("file")
    }
    if not eligible:
        raise ValueError("报价主体桥接配置为空；请检查 parties.json → quotationEntityBindings。")

    visible_name = _visible_name_from_header(_header_text(quotation), party_config)
    if visible_name:
        for key, binding in bindings.items():
            if key in eligible and _normalized(binding["headerName"]) == _normalized(visible_name):
                return key, eligible[key]

    configured_hashes: dict[str, tuple[str, dict]] = {}
    configured_images: list[tuple[str, dict, bytes]] = []
    for key, cfg in eligible.items():
        image = quotation_skill_root() / "assets" / cfg["header_image"]["file"]
        data = image.read_bytes()
        configured_hashes[_sha256(data)] = (key, cfg)
        configured_images.append((key, cfg, data))

    quote_images: list[bytes] = []
    with zipfile.ZipFile(quotation) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/"):
                data = zf.read(name)
                exact = configured_hashes.get(_sha256(data))
                if exact:
                    return exact
                quote_images.append(data)

    scored: list[tuple[float, str, dict]] = []
    for quote_image in quote_images:
        for key, cfg, configured_image in configured_images:
            distance = _perceptual_distance(quote_image, configured_image)
            if distance is not None:
                scored.append((distance, key, cfg))
    scored.sort(key=lambda item: item[0])
    if scored:
        best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 999.0
        if best[0] <= 4.0 and runner_up - best[0] >= 1.0:
            return best[1], best[2]
    raise ValueError("无法从可见页眉文本或页眉图片可靠识别签约主体；请用户确认页眉主体。")


def _party_by_canonical_name(config: dict, name: str) -> dict:
    matches = [party for party in config["parties"] if party["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"parties.json 主体映射必须唯一: {name}")
    return matches[0]


def resolve_party_snapshot(quotation: Path) -> dict[str, object]:
    entity_key, _ = identify_entity(quotation)
    config = load_parties()
    binding = config.get("quotationEntityBindings", {}).get(entity_key)
    if not binding:
        raise ValueError(f"parties.json 缺少报价主体绑定: {entity_key}")
    party = _party_by_canonical_name(config, binding["party"])
    address_spec = party["address"]
    if address_spec["source"] == "fixed":
        address = config["fixedAddresses"][address_spec["ref"]]
    elif address_spec["source"] == "header":
        address = binding.get("headerAddress", "").strip()
        if not address:
            raise ValueError(f"中国主体页眉地址未配置: {entity_key}")
    else:
        raise ValueError(f"未知地址来源: {address_spec['source']}")
    company = binding["headerName"]
    snapshot: dict[str, object] = {
        "entity": entity_key,
        "country": party["country"],
        "company": company,
        "address": address,
        "representative": party["representative"],
        "title": party["title"],
        "email": party["email"],
    }
    if party["country"] == "ID":
        snapshot.update(config["disputeDefaults"]["ID"]["default"])
    else:
        city = "上海" if company.startswith("上海") or "上海分公司" in company else "default"
        defaults = config["disputeDefaults"]["CN"][city]
        snapshot["dispute_place"] = defaults.get("location") or ""
        snapshot["arbitration"] = defaults.get("institution") or ""
    return snapshot


def document_text(docx: Path) -> etree._Element:
    with zipfile.ZipFile(docx) as zf:
        return etree.fromstring(zf.read("word/document.xml"))


def extract_quotation_fields(quotation: Path) -> dict[str, str]:
    root = document_text(quotation)
    result = {"customer_name": "", "contact_name": "", "contact_info": "", "contract_no": ""}
    labels = {"公司名称": "customer_name", "联系人": "contact_name", "联系方式": "contact_info", "合同号": "contract_no"}
    for p in root.xpath("//w:body//w:p", namespaces=NS):
        text = "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
        for label, key in labels.items():
            match = re.match(rf"^{label}\s*[：:]\s*(.*)$", text)
            if match:
                result[key] = match.group(1).strip()
    result["contract_no"] = result["contract_no"].upper()
    return result


def parse_contact(value: str) -> dict[str, list[str]]:
    value = value.strip()
    result = {"phones": [], "emails": [], "wechats": [], "unclassified": []}
    if not value:
        return result
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
    result["emails"] = list(dict.fromkeys(emails))
    remainder = value
    for email in emails:
        remainder = remainder.replace(email, " ")
    phone_pattern = r"(?<!\w)(?:\+?\d[\d\s()\-]{4,}\d)(?!\w)"
    for match in re.findall(phone_pattern, remainder):
        compact = re.sub(r"\D", "", match)
        if 6 <= len(compact) <= 20:
            result["phones"].append(match.strip())
            remainder = remainder.replace(match, " ")
    for fragment in re.split(r"[,;/|，；\n]+", remainder):
        fragment = fragment.strip(" :-（）()")
        if not fragment:
            continue
        tagged = re.match(r"(?i)^(?:微信号?|wechat|wx)\s*[：:]?\s*(.+)$", fragment)
        if tagged:
            result["wechats"].append(tagged.group(1).strip())
        elif re.search(r"[A-Za-z0-9_]", fragment):
            result["wechats"].append(fragment)
        else:
            result["unclassified"].append(fragment)
    for key in result:
        result[key] = list(dict.fromkeys(result[key]))
    return result


def split_contact(value: str) -> tuple[str, str]:
    parsed = parse_contact(value)
    email_or_wechat = " / ".join(parsed["emails"] + parsed["wechats"])
    phone = " / ".join(parsed["phones"])
    if parsed["unclassified"]:
        email_or_wechat = " / ".join(filter(None, (email_or_wechat, " / ".join(parsed["unclassified"]))))
    return email_or_wechat, phone


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
