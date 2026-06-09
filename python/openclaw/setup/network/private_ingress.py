#!/usr/bin/env python3
"""private ingress 绑定约束真源。"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

RFC1918_V4_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
LOOPBACK_V4_NETWORK = IPv4Network("127.0.0.0/8")
ULA_V6_NETWORK = IPv6Network("fc00::/7")
LOOPBACK_V6 = IPv6Address("::1")

PRIVATE_INGRESS_BIND_IP_DOC = "仅接受 RFC1918/loopback IPv4 或 ULA/loopback IPv6 字面量；拒绝 hostname、0.0.0.0/:: 与公网地址。"


def _is_allowed_private_ingress_ip(candidate: IPv4Address | IPv6Address) -> bool:
    if isinstance(candidate, IPv4Address):
        return any(candidate in network for network in RFC1918_V4_NETWORKS) or candidate in LOOPBACK_V4_NETWORK
    return candidate == LOOPBACK_V6 or candidate in ULA_V6_NETWORK


def validate_private_ingress_bind_ip(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "必须为非空 IP 字面量"
    try:
        candidate = ip_address(text)
    except ValueError:
        return "必须为 IP 字面量，不接受 hostname"
    if candidate.is_unspecified:
        return "不允许使用 0.0.0.0 / :: 这类通配绑定"
    if not _is_allowed_private_ingress_ip(candidate):
        return PRIVATE_INGRESS_BIND_IP_DOC
    return ""
