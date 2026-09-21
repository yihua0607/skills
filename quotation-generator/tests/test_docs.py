"""SKILL.md 与 references/、docs/ 之间的引用检查。

文档之间的引用是纯文本：改章节名、删章节、重命名或搬移文件，脚本一律不报错，
只会在真实报价任务里把 Agent 指到一个不存在的地方。三类漂移靠测试拦住：

1. Markdown 链接 `](references/x.md)` 的目标必须存在。
2. 章节引用必须写成 `文件.md『章节名』`，且章节名能在目标文件里找到对应标题。
   不写文件名的裸『章节名』一律判失败——先前的 `『汇率与换算』` 就是这么烂掉的，
   当时 SKILL.md 里根本没有这个名字的标题，而没人发现。
3. references/ 和 docs/ 下的每个 .md 都必须能从 SKILL.md 链到，不留孤儿文件。
"""
import os
import re
import subprocess
import sys
import unittest

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFERENCES_DIR = os.path.join(SKILL_ROOT, 'references')
DOCS_DIR = os.path.join(SKILL_ROOT, 'docs')
SCRIPTS_DIR = os.path.join(SKILL_ROOT, 'scripts')

DOC_DIRS = ('references', 'docs')

MD_LINK = re.compile(r'\]\(([^)\s#]+\.md)(?:#[^)\s]*)?\)')
SECTION_REF = re.compile(r'『([^』]+)』')
FILE_PREFIX = re.compile(r'([\w\-./]+\.md)\s*$')
HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*$', re.MULTILINE)


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def _md_files_in(dirname):
    directory = os.path.join(SKILL_ROOT, dirname)
    if not os.path.isdir(directory):
        return []
    return [
        os.path.join(directory, name)
        for name in sorted(os.listdir(directory))
        if name.endswith('.md')
    ]


def _doc_files():
    """SKILL.md 和 references/、docs/ 下的所有 Markdown。"""
    return [os.path.join(SKILL_ROOT, 'SKILL.md')] + [
        path for dirname in DOC_DIRS for path in _md_files_in(dirname)
    ]


def _scanned_files():
    """文档 + 脚本：脚本注释里同样会引用章节。"""
    scripts = [
        os.path.join(SCRIPTS_DIR, name)
        for name in sorted(os.listdir(SCRIPTS_DIR))
        if name.endswith('.py')
    ]
    return _doc_files() + scripts


def _resolve_link(target, source_path):
    """按 Markdown 相对路径语义解析链接：只认相对源文件和相对 skill 根两种。

    刻意不做 basename 兜底——`references/entity-bank-info.md` 在文件搬到
    `docs/` 之后必须判失败，而不是被兜底成 `docs/entity-bank-info.md` 蒙混过关。
    """
    for candidate in (
        os.path.join(os.path.dirname(source_path), target),
        os.path.join(SKILL_ROOT, target),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _resolve_doc(ref, source_path):
    """解析正文里的章节引用，允许只写文件名（`x.md『标题』`）。"""
    resolved = _resolve_link(ref, source_path)
    if resolved:
        return resolved
    for candidate in (
        os.path.join(REFERENCES_DIR, os.path.basename(ref)),
        os.path.join(DOCS_DIR, os.path.basename(ref)),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _normalize(text):
    return text.replace('`', '').strip()


def _headings(path):
    return {_normalize(h) for h in HEADING.findall(_read(path))}


class TestDocs(unittest.TestCase):
    def test_generated_docs_are_current(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, 'generate_docs.py'), '--check'],
            cwd=SKILL_ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_markdown_links_resolve(self):
        for path in _doc_files():
            rel = os.path.relpath(path, SKILL_ROOT)
            for target in MD_LINK.findall(_read(path)):
                with self.subTest(source=rel, target=target):
                    self.assertIsNotNone(
                        _resolve_link(target, path),
                        f'{rel} 链接到不存在的文件: {target}',
                    )

    def test_every_document_is_reachable_from_skill_md(self):
        linked = {
            os.path.normpath(target)
            for target in MD_LINK.findall(_read(os.path.join(SKILL_ROOT, 'SKILL.md')))
        }
        for dirname in DOC_DIRS:
            for path in _md_files_in(dirname):
                rel = os.path.relpath(path, SKILL_ROOT)
                with self.subTest(document=rel):
                    self.assertIn(
                        os.path.normpath(rel), linked,
                        f'{rel} 没有被 SKILL.md 引用',
                    )

    def test_section_references_resolve(self):
        checked = 0
        for path in _scanned_files():
            rel = os.path.relpath(path, SKILL_ROOT)
            text = _read(path)
            for match in SECTION_REF.finditer(text):
                section = _normalize(match.group(1))
                prefix = text[max(0, match.start() - 60):match.start()]
                file_match = FILE_PREFIX.search(prefix)
                with self.subTest(source=rel, section=section):
                    self.assertIsNotNone(
                        file_match,
                        f'{rel}:『{section}』没有写文件名，无法校验——'
                        f'改成 `文件.md『{section}』`',
                    )
                    target = _resolve_doc(file_match.group(1), path)
                    self.assertIsNotNone(
                        target, f'{rel}:『{section}』指向的文件不存在: {file_match.group(1)}',
                    )
                    headings = _headings(target)
                    self.assertTrue(
                        any(section == h or section in h for h in headings),
                        f'{rel}:『{section}』在 {os.path.relpath(target, SKILL_ROOT)} '
                        f'里找不到对应标题，现有标题: {sorted(headings)}',
                    )
                    checked += 1
        self.assertGreater(checked, 0, '一条章节引用都没扫到，检查规则可能已失效')

    def test_markdown_file_mentions_are_links_or_checkable_section_refs(self):
        """防止用反引号写一个看似引用、实际无法校验的过期文件名。"""
        token = re.compile(r'[\w./-]+\.md')
        for path in _doc_files():
            rel = os.path.relpath(path, SKILL_ROOT)
            for line_no, line in enumerate(_read(path).splitlines(), 1):
                valid = set(MD_LINK.findall(line))
                for name in token.findall(line):
                    if name in valid or f'{name}『' in line:
                        continue
                    with self.subTest(source=rel, line=line_no, target=name):
                        self.fail(
                            f'{rel}:{line_no} 的 {name} 必须写成 Markdown 链接，'
                            '或写成 文件.md『章节名』')


if __name__ == '__main__':
    unittest.main()
