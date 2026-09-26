import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from contract_common import NS, controls_by_tag, extract_service_name, split_contact


class ContractSkillTests(unittest.TestCase):
    def test_template_has_all_stable_tags(self):
        template = ROOT / "assets" / "中国销售合同-模板.docx"
        with zipfile.ZipFile(template) as zf:
            root = etree.fromstring(zf.read("word/document.xml"))
        tags = set(controls_by_tag(root))
        expected = {
            "contract_no", "service_name", "sales_name", "sales_email", "sales_wechat",
            "客户名称", "客户地址", "客户授权代表人", "代表人职务", "客户邮箱", "客户电话号",
            "山海图单位名称", "地址", "山海图授权代表人", "山海图授权代表人的职务",
            "party_b_email", "dispute_place", "arbitration", "party_a_signature_name",
            "party_b_signature_name", "party_a_signature_rep", "party_b_signature_rep",
            "party_a_date", "party_b_date",
        }
        self.assertTrue(expected <= tags, expected - tags)

    def test_all_chinese_entities_have_contract_config(self):
        entities = json.loads((ROOT.parent / "quotation-generator" / "config" / "entities.json").read_text(encoding="utf-8"))
        for key in ("beijing", "xian", "shenzhen", "shanghai", "shanghai_new"):
            contract = entities[key]["contract"]
            self.assertEqual(set(contract), {"address", "representative", "title", "email", "dispute_place", "arbitration"})
            self.assertTrue(all(contract.values()))

    def test_contact_classification(self):
        self.assertEqual(split_contact("skdf@163.com"), ("skdf@163.com", ""))
        self.assertEqual(split_contact("138-0013-8000"), ("", "138-0013-8000"))
        self.assertEqual(split_contact("wechat_id"), ("wechat_id", ""))

    def test_service_name_is_only_extracted_on_demand(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as temp_dir:
            quotation = Path(temp_dir) / "service-title.docx"
            doc = Document()
            doc.add_paragraph("注册及许可服务方案")
            doc.save(quotation)
            self.assertEqual(extract_service_name(quotation), "注册及许可")


if __name__ == "__main__":
    unittest.main()
