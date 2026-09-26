import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from contract_common import NS, controls_by_tag, extract_quotation_fields, resolve_party_snapshot, split_contact


COMMON_TAGS = {
    "contract_no", "sales_name", "sales_email", "sales_wechat",
    "客户名称", "客户地址", "客户授权代表人", "代表人职务", "客户邮箱", "客户电话号",
    "山海图单位名称", "地址", "山海图授权代表人", "山海图授权代表人的职务",
    "party_b_email", "party_a_signature_name", "party_b_signature_name",
    "party_a_signature_rep", "party_b_signature_rep", "party_a_date", "party_b_date",
}
TRILINGUAL_TAGS = {
    "termination_completed", "termination_date_enabled", "termination_early",
    "law_id", "law_cn", "law_en", "language_id", "language_cn", "language_en",
    "institution_id", "institution_cn", "institution_en", "location_id", "location_cn", "location_en",
    "copies_id", "copies_each_id", "copies_cn", "copies_each_cn", "copies_en", "copies_each_en",
}


def document_root(path: Path):
    with zipfile.ZipFile(path) as zf:
        return etree.fromstring(zf.read("word/document.xml"))


class ContractSkillTests(unittest.TestCase):
    def test_contract_number_controls_force_uppercase_display(self):
        for filename in ("印尼服务合同-中文版本.docx", "印尼服务合同--三语版本.docx"):
            root = document_root(ROOT / "assets" / filename)
            control = controls_by_tag(root)["contract_no"]
            self.assertIsNotNone(control.find("w:sdtPr/w:rPr/w:caps", NS), filename)
            runs = control.xpath(".//w:sdtContent//w:r", namespaces=NS)
            self.assertTrue(runs, filename)
            self.assertTrue(all(run.find("w:rPr/w:caps", NS) is not None for run in runs), filename)

    def test_both_templates_have_stable_tags(self):
        cases = {
            "印尼服务合同-中文版本.docx": COMMON_TAGS | {"dispute_place", "arbitration"},
            "印尼服务合同--三语版本.docx": COMMON_TAGS | TRILINGUAL_TAGS,
        }
        for filename, expected in cases.items():
            tags = set(controls_by_tag(document_root(ROOT / "assets" / filename)))
            self.assertTrue(expected <= tags, f"{filename}: {sorted(expected - tags)}")

    def test_party_configuration_and_bindings_are_complete(self):
        config = json.loads((ROOT / "references" / "parties.json").read_text(encoding="utf-8"))
        party_names = {party["name"] for party in config["parties"]}
        self.assertTrue(config["quotationEntityBindings"])
        for key, binding in config["quotationEntityBindings"].items():
            self.assertTrue(key)
            self.assertIn(binding["party"], party_names)
            self.assertTrue(binding["headerName"])
        for party in config["parties"]:
            for field in ("name", "matchType", "country", "address", "representative", "title", "email"):
                self.assertIn(field, party)
            if party["address"]["source"] == "fixed":
                self.assertIn(party["address"]["ref"], config["fixedAddresses"])

    def test_removed_dropdown_values_do_not_remain(self):
        forbidden = {"FIRMA HUKUM SEA (SEA LAW FIRM)", "PT SHANHAIMAP MITRA KONSULTAN", "曾薇 Zeng Wei", "zengwei@shanhaimap.com"}
        for filename in ("印尼服务合同-中文版本.docx", "印尼服务合同--三语版本.docx"):
            text = "\n".join(document_root(ROOT / "assets" / filename).xpath("//w:t/text()", namespaces=NS))
            for value in forbidden:
                self.assertNotIn(value, text, filename)

    def test_contact_classification_handles_mixed_input(self):
        email_or_wechat, phone = split_contact("+62 812-3456-7890; test@example.com; 微信: test_wechat")
        self.assertEqual(email_or_wechat, "test@example.com / test_wechat")
        self.assertEqual(phone, "+62 812-3456-7890")

    def test_fixture_identification_uses_current_bindings(self):
        self.assertEqual(resolve_party_snapshot(ROOT / "tests/fixtures/xian-quotation.docx")["entity"], "xian")
        slf = resolve_party_snapshot(ROOT / "tests/fixtures/slf-quotation.docx")
        self.assertEqual(slf["company"], "SEA LAW FIRM")

    def test_chinese_and_trilingual_end_to_end(self):
        for fixture in ("xian-quotation.docx", "slf-quotation.docx"):
            quotation = ROOT / "tests/fixtures" / fixture
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / fixture.replace("quotation", "contract")
                build = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/build_contract.py"), str(quotation), "--output", str(output), "--date", "2026-09-26"],
                    text=True, capture_output=True,
                )
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                verify = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/verify_contract.py"), str(output), "--quotation", str(quotation)],
                    text=True, capture_output=True,
                )
                self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
                qfields = extract_quotation_fields(quotation)
                controls = controls_by_tag(document_root(output))
                for tag, value in {
                    "party_a_signature_name": qfields["customer_name"],
                    "party_a_signature_rep": qfields["contact_name"],
                }.items():
                    control = controls[tag]
                    text = "".join(control.xpath(".//w:sdtContent//w:t/text()", namespaces=NS))
                    placeholder = control.find("w:sdtPr/w:showingPlcHdr", NS)
                    if value:
                        self.assertEqual(text, value, tag)
                        self.assertIsNone(placeholder, tag)
                    else:
                        self.assertEqual(text, "Click or tap here to enter text.", tag)
                        self.assertIsNotNone(placeholder, tag)


if __name__ == "__main__":
    unittest.main()
