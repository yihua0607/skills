"""报价人（签名栏）姓名解析。

规则（2026-09-24 定）：
* 在**企业微信会话**里生成报价单 → 签名栏「报价人」用当前使用者的**姓名**（通讯录姓名）；
* **不在企业微信环境下**运行 → 留空（保持模板原样）。

优先级：`--quoter` 显式参数 > 环境变量 `HERMES_QUOTER_NAME` > 企业微信会话解析 > 空串。

企业微信会话判定：存在 `HERMES_SESSION_USER_ID`，且 `HERMES_SESSION_PLATFORM` 为空、`wecom` 或 `wecom_callback`
（网关会设置这两个变量；普通命令行/CI 环境两者都没有 → 留空）。

姓名来源（逐级回落，任何一步失败都不抛异常）：
1. 映射缓存 `HERMES_WECOM_NAME_MAP`（默认 `/opt/data/huaweicloud/reports/wecom-userid-name-map.json`）；
2. 缓存里查不到 → 用 `HERMES_ENV_FILE`（默认 `/opt/data/.env`）里的自建应用凭据调 `user/get` 现查一次；
3. 都拿不到 → 空串。**宁缺勿错**：绝不把 userid 印到客户可见的文档上。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

DEFAULT_MAP = "/opt/data/huaweicloud/reports/wecom-userid-name-map.json"
DEFAULT_ENV_FILE = "/opt/data/.env"
WECOM_PLATFORMS = {"", "wecom", "wecom_callback", "wecom-bot"}
_TIMEOUT = 8
_token: str | None = None


def _wecom_userid() -> str:
    """当前企业微信使用者 userid；不在企业微信环境则返回空串。"""
    uid = (os.environ.get("HERMES_SESSION_USER_ID") or "").strip()
    if not uid:
        return ""
    platform = (os.environ.get("HERMES_SESSION_PLATFORM") or "").strip().lower()
    if platform not in WECOM_PLATFORMS:
        return ""
    return uid


def _from_cache(userid: str) -> str:
    path = os.environ.get("HERMES_WECOM_NAME_MAP", DEFAULT_MAP)
    try:
        with open(path, encoding="utf-8") as fh:
            mapping = json.load(fh).get("mapping") or {}
    except Exception:  # noqa: BLE001
        return ""
    v = mapping.get(userid)
    if isinstance(v, dict):
        return (v.get("name") or "").strip()
    return (v or "").strip() if isinstance(v, str) else ""


def _env_creds() -> tuple[str, str]:
    path = os.environ.get("HERMES_ENV_FILE", DEFAULT_ENV_FILE)
    vals: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
                if m:
                    vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        return "", ""
    return vals.get("WECOM_CALLBACK_CORP_ID", ""), vals.get("WECOM_CALLBACK_CORP_SECRET", "")


def _live(userid: str) -> str:
    """缓存里没有时现查一次企业微信通讯录（拿不到就返回空串）。"""
    global _token
    cid, secret = _env_creds()
    if not cid or not secret:
        return ""
    api = "https://qyapi.weixin.qq.com/cgi-bin"
    try:
        if not _token:
            with urllib.request.urlopen(
                    f"{api}/gettoken?corpid={urllib.parse.quote(cid)}&corpsecret={urllib.parse.quote(secret)}",
                    timeout=_TIMEOUT) as r:
                tok = json.loads(r.read().decode("utf-8", "ignore"))
            _token = tok.get("access_token") or ""
        if not _token:
            return ""
        with urllib.request.urlopen(
                f"{api}/user/get?access_token={_token}&userid={urllib.parse.quote(userid)}",
                timeout=_TIMEOUT) as r:
            res = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001 — 网络/权限问题一律不打断出单
        return ""
    if res.get("errcode") == 0:
        return (res.get("name") or "").strip()
    return ""


UNSET = object()


def resolve(explicit: str | None = None, data_value=UNSET) -> tuple[str, str]:
    """返回 (报价人姓名, 来源说明)。

    优先级：`--quoter` > `quote_meta.quoter_name`（数据文件里写了就用它，**空串 = 强制留空**）
    > `HERMES_QUOTER_NAME` > 企业微信会话姓名（默认值） > 空串。
    `data_value` 用默认值 `UNSET` 表示「数据文件里没有该字段」，此时才回落到环境变量/企微会话。
    """
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip(), '--quoter 参数'
    if data_value is not UNSET:
        v = data_value.strip() if isinstance(data_value, str) else ''
        return v, ('quotation.json quote_meta.quoter_name'
                   if v else 'quotation.json quote_meta.quoter_name 显式留空')
    env_name = (os.environ.get("HERMES_QUOTER_NAME") or "").strip()
    if env_name:
        return env_name, "环境变量 HERMES_QUOTER_NAME"

    userid = _wecom_userid()
    if not userid:
        return "", "非企业微信环境，留空"
    name = _from_cache(userid) or _live(userid)
    if name:
        return name, f"企业微信会话 {userid}（默认值，可用 --quoter / quote_meta.quoter_name 覆盖）"
    return "", f"企业微信会话 {userid}，但通讯录里取不到姓名（留空）"


if __name__ == "__main__":
    import sys
    print(resolve(sys.argv[1] if len(sys.argv) > 1 else None))
