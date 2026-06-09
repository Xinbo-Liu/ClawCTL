#!/usr/bin/env python3
"""TLS access hostname validation shared by deploy inputs and ingress rendering."""
from __future__ import annotations

import ipaddress
import re

_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

TLS_HOSTNAME_DOC = (
    "必须是 ASCII DNS 主机名；只允许字母、数字、点与短横线，"
    "拒绝 IP、IPv4 dotted-quad 形态、通配符、下划线、空白、尾随点、空 label、超长 label 和非 DNS label 字符。"
)


def validate_tls_hostname(value: str) -> str:
    text = str(value or "")
    if not text:
        return "不能为空"
    if text != text.strip():
        return "不能包含首尾空白"
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in text):
        return TLS_HOSTNAME_DOC
    if text.endswith("."):
        return "不能以点结尾"
    if len(text) > 253:
        return "长度不能超过 253 个字符"
    if re.fullmatch(r"\d+(?:\.\d+){3}", text):
        return "不能是 IPv4 dotted-quad 形态"
    try:
        ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        return "不能是 IP 字面量"
    labels = text.split(".")
    if not labels or any(not label for label in labels):
        return "DNS label 不能为空"
    for label in labels:
        if len(label) > 63:
            return "单个 DNS label 长度不能超过 63 个字符"
        if not _DNS_LABEL_RE.fullmatch(label):
            return TLS_HOSTNAME_DOC
    return ""
