"""签名栏「报价人」取值契约。

业务口径（2026-09-24）：
  * 默认：企业微信会话里生成 → 当前使用者的通讯录姓名；非企业微信环境 → 留空；
  * **默认值可被覆盖**（这是本次的重点）：`--quoter` > `quote_meta.quoter_name`
    > `HERMES_QUOTER_NAME` > 企微会话姓名 > 空串；
  * `quote_meta.quoter_name` 显式填空串 = 强制留空（用于「这份单不写报价人」）；
  * 通讯录查不到姓名时留空 —— **绝不把 userid 印到客户可见文档上**。

这些用例不联网：缓存走临时文件，凭据文件指向不存在的路径。
"""
import json
import os
import tempfile
import unittest

from scripts.quoter_name import resolve, UNSET

WATCHED = (
    'HERMES_SESSION_USER_ID', 'HERMES_SESSION_PLATFORM',
    'HERMES_QUOTER_NAME', 'HERMES_WECOM_NAME_MAP', 'HERMES_ENV_FILE',
)


class TestQuoterName(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in WATCHED}
        for k in WATCHED:
            os.environ.pop(k, None)
        # 缓存：只有 ZhangZhenNi 一个已知用户
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self.tmp.name, 'map.json')
        with open(self.cache, 'w', encoding='utf-8') as fh:
            json.dump({'mapping': {'ZhangZhenNi': {'name': '张珍妮'}}}, fh, ensure_ascii=False)
        os.environ['HERMES_WECOM_NAME_MAP'] = self.cache
        os.environ['HERMES_ENV_FILE'] = os.path.join(self.tmp.name, 'nope.env')  # 禁掉现场兜底

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _wecom(self, userid='ZhangZhenNi'):
        os.environ['HERMES_SESSION_USER_ID'] = userid
        os.environ['HERMES_SESSION_PLATFORM'] = 'wecom'

    # ── 默认行为 ───────────────────────────────────────────────
    def test_wecom_session_defaults_to_contact_name(self):
        self._wecom()
        name, src = resolve()
        self.assertEqual(name, '张珍妮')
        self.assertIn('默认值', src)

    def test_outside_wecom_is_blank(self):
        name, src = resolve()
        self.assertEqual(name, '')
        self.assertIn('非企业微信环境', src)

    def test_other_platform_is_blank(self):
        os.environ['HERMES_SESSION_USER_ID'] = 'ZhangZhenNi'
        os.environ['HERMES_SESSION_PLATFORM'] = 'slack'
        name, _ = resolve()
        self.assertEqual(name, '')

    def test_unknown_userid_leaves_blank(self):
        self._wecom('Xian_005')          # 通讯录里没有这个人
        name, src = resolve()
        self.assertEqual(name, '')
        self.assertIn('留空', src)

    # ── 可覆盖 ─────────────────────────────────────────────────
    def test_explicit_overrides_data_and_wecom(self):
        self._wecom()
        name, src = resolve('王五', '张三')          # 显式参数 > 数据字段
        self.assertEqual(name, '王五')
        self.assertIn('--quoter', src)

    def test_data_field_overrides_wecom_default(self):
        self._wecom()
        name, src = resolve(None, '李四')
        self.assertEqual(name, '李四')
        self.assertIn('quoter_name', src)

    def test_data_field_empty_string_forces_blank(self):
        self._wecom()
        name, src = resolve(None, '')
        self.assertEqual(name, '')
        self.assertIn('显式留空', src)

    def test_env_var_overrides_wecom_but_not_data(self):
        self._wecom()
        os.environ['HERMES_QUOTER_NAME'] = '赵六'
        self.assertEqual(resolve()[0], '赵六')
        self.assertEqual(resolve(None, '李四')[0], '李四')   # 数据字段仍然更优先

    def test_data_value_not_passed_means_absent(self):
        self._wecom()
        self.assertEqual(resolve(None, UNSET)[0], '张珍妮')


if __name__ == '__main__':
    unittest.main()
