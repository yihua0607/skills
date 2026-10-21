#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出单前强制核对：quotation.json 里的服务内容 是否与「本次接口返回的服务内容」逐条一致。

背景（2026-10-21 定）：报价单的服务内容（所需资料 / 交付文件 / 办理时间 / 费用项）**唯一来源是本次
fetch_services.py 拿到的服务内容**，不得复用历史 quotation.json 里的服务块——旧块可能被截断、
丢层级或与官网现状不符（真实事故：同事单的所需资料每项服务都少了几条，客户按单准备会漏件）。

用法：
    python3 check_service_content.py --data <工作目录>/quotation.json
    python3 check_service_content.py --data ... --strict     # 费用项差异也算失败

判定：
    ❌ 失败（会阻断出单）：所需资料 / 交付文件 有条目缺失或多余，或办理时间不一致
    ⚠️  警告：费用包含/不含 条目差异（通用项允许移到底部备注）、编号写法差异
退出码：0 通过（可含警告）；1 失败；2 用法/文件问题
"""
import argparse
import json
import pathlib
import re
import sys

# 小节标题本身不算条目（历史块有时把标题也当一条资料）
HEADINGS = {'依据部分税务局要求', '备注', '费用说明', '付款方式', '交付文件', '所需资料', '所需资料及信息',
            '基本信息', '办理时间及流程', '费用包含', '费用不含', '费用不包含', '服务完成后交付文件'}

NUM = re.compile(r'^\s*(?:\(?\d+\)?[.、）)]|（\d+）|[a-zA-Z][.、）)]|第[一二三四五六七八九十]+步[：:])\s*')


def norm(t):
    """归一化：去编号、去全角/半角空格、去行尾反斜杠、去标点差异。"""
    t = t.replace('\\', ' ').replace('\u3000', ' ')
    t = NUM.sub('', t)
    t = re.sub(r'\s+', '', t)
    return t


def section(content, titles, stop_titles):
    """取 content 中某个小节的正文（从 titles 任一命中处，到 stop_titles 任一命中处为止）。"""
    for title in titles:
        pat = re.compile(r'\*{0,4}\s*' + title + r'\s*[：:]?\s*\*{0,4}')
        m = pat.search(content)
        if not m:
            continue
        rest = content[m.end():]
        end = len(rest)
        for st in stop_titles:
            sm = re.search(r'\*{0,4}\s*' + st + r'\s*[：:]?\s*\*{0,4}', rest)
            if sm and sm.start() < end:
                end = sm.start()
        return rest[:end]
    return ''


def items(block):
    """把一段正文切成条目：按换行和行尾反斜杠切，保留有内容的行。"""
    out = []
    for raw in re.split(r'\\|\n', block or ''):
        t = raw.strip().strip('*').strip()
        if not t:
            continue
        out.append(t)
    return out


ALL_STOPS = ['基本信息', '办理时间及流程', '办理时间', r'1\.\s*办理时间', r'2\.\s*办理流程', '办理流程',
             '所需资料', '所需资料及信息', '交付文件', '服务完成后交付文件', '费用说明', '费用包含',
             '费用不含', '费用不包含', '付款方式', '备注', '依据部分税务局要求']


def parse(content):
    d = {}
    m = re.search(r'办理时间\s*[：:]\s*\*{0,4}\s*([^\n\\*]+)', content)
    d['days'] = re.sub(r'\s+', '', m.group(1).strip().rstrip('。')) if m else ''
    flow = section(content, [r'2\.\s*办理流程', '办理流程'], [s for s in ALL_STOPS if '流程' not in s])
    d['process'] = [x for x in items(flow) if re.match(r'第[一二三四五六七八九十]+步', x)]
    docs = items(section(content, ['所需资料及信息', '所需资料'],
                         [s for s in ALL_STOPS if s not in ('所需资料', '所需资料及信息')]))
    # 「依据部分税务局要求」属于所需资料的补充章节（税务局额外要求），一并计入
    docs += items(section(content, ['依据部分税务局要求'],
                          [s for s in ALL_STOPS if s != '依据部分税务局要求']))
    d['documents'] = docs
    d['deliverables'] = items(section(content, ['服务完成后交付文件', '交付文件'],
                                      [s for s in ALL_STOPS if '交付' not in s]))
    d['include'] = items(section(content, [r'费用包含[：:]?'], ['费用不含', '费用不包含', '付款方式', '备注']))
    d['exclude'] = items(section(content, [r'费用不包含[：:]?', r'费用不含[：:]?'], ['付款方式', '备注', '费用包含']))
    return d


def diff(expected, actual, drop=()):
    """返回 (缺失, 多余)，均为归一化后的文本。"""
    skip = {norm(h) for h in HEADINGS}
    e = [norm(x) for x in expected if norm(x) and norm(x) not in drop and norm(x) not in skip]
    a = [norm(x) for x in actual if norm(x) and norm(x) not in skip]
    missing = [x for x in e if x not in a]
    extra = [x for x in a if x not in e]
    return missing, extra


def show(title, arr, limit=8):
    for x in arr[:limit]:
        print(f'      - {x}')
    if len(arr) > limit:
        print(f'      … 共 {len(arr)} 项')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='quotation.json 路径')
    ap.add_argument('--strict', action='store_true', help='费用项差异也算失败')
    ap.add_argument('--max-age-hours', type=float, default=24.0,
                    help='查询结果允许的最大时长（小时，默认 24）；官网内容常更新，超时须重新 fetch')
    args = ap.parse_args()

    p = pathlib.Path(args.data)
    if not p.exists():
        print(f'❌ 找不到 {p}')
        return 2
    data = json.load(open(p))
    meta = data.get('_meta') or {}
    qrf = meta.get('query_result_file')
    if not qrf:
        print('❌ _meta.query_result_file 缺失，无法核对本次接口内容')
        return 2
    qp = p.parent / qrf
    if not qp.exists():
        print(f'❌ 找不到接口结果文件 {qp}（应当与 quotation.json 同目录）')
        return 2
    api = {s['服务编码']: s for s in json.load(open(qp)).get('services', [])}

    # ── 新鲜度：官网内容经常更新，旧查询结果不得用来出单 ──
    import time
    age_h = (time.time() - qp.stat().st_mtime) / 3600.0
    fresh_fail = age_h > args.max_age_hours
    stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(qp.stat().st_mtime))
    if fresh_fail:
        print(f'❌ 查询结果不是本次拉取的：{qp.name} 生成于 {stamp}（{age_h:.1f} 小时前，'
              f'超过 {args.max_age_hours:g} 小时上限）')
        print('   官网内容经常更新，请重新执行 fetch_services.py 拉取最新服务内容后再出单。\n')
    else:
        print(f'查询结果拉取时间 {stamp}（{age_h:.1f} 小时前，在 {args.max_age_hours:g} 小时内）✅\n')

    fails, warns = [], []
    if fresh_fail:
        fails.append(f'查询结果过期（{age_h:.1f} 小时前拉的，需重新 fetch）')
    print(f'核对 {len(data.get("services") or [])} 项服务 与本次接口内容 …\n')
    for s in data.get('services') or []:
        code = str(s.get('code'))
        src = api.get(code)
        print(f"[{code}] {s.get('name')}")
        if not src:
            fails.append(f'{code} 在本次接口结果里找不到该编码')
            print('    ❌ 本次接口结果里没有这个编码，无法核对')
            continue
        exp = parse(src['服务内容'])

        if exp['days'] and norm(exp['days']) != norm(s.get('days') or ''):
            warns.append(f"{code} 办理时间：接口「{exp['days']}」 vs 报价单「{s.get('days')}」")
            print(f"    ⚠️  办理时间不一致：接口「{exp['days']}」/ 报价单「{s.get('days')}」")

        drop = {norm(x) for x in (s['fees'].get('note') and [] or [])}
        for key, label, level in (('documents', '所需资料', 'fail'), ('deliverables', '交付文件', 'fail')):
            missing, extra = diff(exp[key], s.get(key) or [], drop)
            if missing or extra:
                msg = f'{code} {label}：缺 {len(missing)} 条 / 多 {len(extra)} 条'
                (fails if level == 'fail' else warns).append(msg)
                print(f'    ❌ {label}与接口内容不一致：')
                if missing:
                    print(f'      接口有、报价单缺（{len(missing)} 条）：')
                    show(label, missing)
                if extra:
                    print(f'      报价单有、接口没有（{len(extra)} 条）：')
                    show(label, extra)
            else:
                print(f'    ✅ {label}与接口一致（{len(exp[key])} 条）')

        for key, label in (('include', '费用包含'), ('exclude', '费用不含')):
            missing, extra = diff(exp[key], s['fees'].get(key) or [])
            if missing or extra:
                msg = f'{code} {label}：接口 {len(exp[key])} 条 / 报价单 {len(s["fees"].get(key) or [])} 条'
                (fails if args.strict else warns).append(msg)
                print(f"    {'❌' if args.strict else '⚠️ '} {label}条目有差异（通用项允许移到底部备注）："
                      f"接口 {len(exp[key])} / 报价单 {len(s['fees'].get(key) or [])}")
                if missing:
                    print('      接口有、报价单没有：'); show(label, missing, 4)
                if extra:
                    print('      报价单有、接口没有：'); show(label, extra, 4)

    print('\n' + '=' * 60)
    if fails:
        print(f'❌ 核对未通过：{len(fails)} 项必须修正后再出单')
        for f in fails:
            print('   -', f)
        if warns:
            print(f'（另有 {len(warns)} 项提示）')
        return 1
    if warns:
        print(f'✅ 核对通过（{len(warns)} 项提示，不影响出单）')
        for w in warns:
            print('   -', w)
        return 0
    print('✅ 核对通过：服务内容与本次接口完全一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
