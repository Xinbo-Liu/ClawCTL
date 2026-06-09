#!/usr/bin/env bash
# 用途：收口 private ingress 的 bind IP 与访问主机名，写入 deploy/site.env；默认仅打印 Windows 访问端命令，可通过 --platform 切换到 Linux、macOS 或 all。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=scripts/setup/lib/tls_hostname_contract.sh
source "$ROOT_DIR/scripts/setup/lib/tls_hostname_contract.sh"
TARGET_FILE="$ROOT_DIR/deploy/site.env"
DEFAULT_TLS_CN='openclaw.internal.example'
DEFAULT_LISTEN_IP=''
LISTEN_IP=''
TLS_CN="$DEFAULT_TLS_CN"
PLATFORM='windows'

print_help() {
  cat <<'HELP'
用法：
  bash ./scripts/setup/init_private_ingress.sh
  bash ./scripts/setup/init_private_ingress.sh --platform linux --
  bash ./scripts/setup/init_private_ingress.sh --platform macos --
  bash ./scripts/setup/init_private_ingress.sh --platform all --
  bash ./scripts/setup/init_private_ingress.sh --platform windows -- <listen_ip>
  bash ./scripts/setup/init_private_ingress.sh --platform windows -- <listen_ip> <tls_cn>

默认行为：
  1. 自动从 `hostname -I` 中选择首个 RFC1918 私网 IPv4，作为 `OPENCLAW_INGRESS_LISTEN_IP`
  2. 默认把 `OPENCLAW_TLS_CN` 写为 `openclaw.internal.example`
  3. 默认 `--platform windows`，只打印 Windows PowerShell 访问端 DNS / hosts 命令
  4. 统一回填 deploy/site.env；目标文件不存在时自动从 deploy/site.env.example 初始化

显式覆盖：
  - 通过 `--platform <windows|linux|macos|all>` 切换访问端输出平台
  - 通过 `-- <listen_ip> <tls_cn>` 显式指定
  - 只想覆盖 IP 时可用 `-- <listen_ip>`；主机名仍默认 `openclaw.internal.example`
HELP
}

fail() {
  echo "[init_private_ingress][FAIL] $*" >&2
  exit 2
}

is_private_or_loopback_ipv4() {
  local ip="$1"
  local o1='' o2='' o3='' o4='' extra=''
  IFS=. read -r o1 o2 o3 o4 extra <<< "$ip"
  [[ -n "$o1" && -n "$o2" && -n "$o3" && -n "$o4" && -z "$extra" ]] || return 1
  [[ "$o1" =~ ^[0-9]+$ && "$o2" =~ ^[0-9]+$ && "$o3" =~ ^[0-9]+$ && "$o4" =~ ^[0-9]+$ ]] || return 1
  local octet=''
  for octet in "$o1" "$o2" "$o3" "$o4"; do
    ((octet >= 0 && octet <= 255)) || return 1
  done
  if ((o1 == 10)); then return 0; fi
  if ((o1 == 127)); then return 0; fi
  if ((o1 == 192 && o2 == 168)); then return 0; fi
  if ((o1 == 172 && o2 >= 16 && o2 <= 31)); then return 0; fi
  return 1
}

is_ula_or_loopback_ipv6() {
  local ip="${1,,}"
  [[ "$ip" == "::1" ]] && return 0
  [[ "$ip" == *:* ]] || return 1
  [[ "$ip" =~ ^[0-9a-f:]+$ ]] || return 1
  [[ "$ip" != *:::* ]] || return 1
  local compact="${ip//::/}"
  local double_count=$(( (${#ip} - ${#compact}) / 2 ))
  ((double_count <= 1)) || return 1
  local first_group="${ip%%:*}"
  [[ "$first_group" =~ ^f[c-d][0-9a-f]{2}$ ]] || return 1
  local group='' nonempty_groups=0
  local -a groups=()
  IFS=':' read -r -a groups <<< "$ip"
  for group in "${groups[@]}"; do
    [[ -n "$group" ]] || continue
    [[ ${#group} -le 4 ]] || return 1
    [[ "$group" =~ ^[0-9a-f]{1,4}$ ]] || return 1
    ((nonempty_groups += 1))
  done
  if ((double_count == 1)); then
    ((nonempty_groups < 8)) || return 1
  else
    ((nonempty_groups == 8)) || return 1
  fi
  return 0
}

is_private_or_loopback_ip() {
  is_private_or_loopback_ipv4 "$1" || is_ula_or_loopback_ipv6 "$1"
}

detect_first_private_ipv4() {
  local value=''
  while IFS= read -r value; do
    [[ -n "$value" ]] || continue
    if is_private_or_loopback_ipv4 "$value"; then
      printf '%s' "$value"
      return 0
    fi
  done < <(hostname -I 2>/dev/null | tr ' ' '\n')
  return 1
}

print_linux_commands() {
  cat <<'EOF' | sed \
    -e "s|__LISTEN_IP__|${LISTEN_IP}|g" \
    -e "s|__TLS_CN__|${TLS_CN}|g"
[init_private_ingress] Linux 访问端 DNS 预判命令：
export OPENCLAW_INGRESS_LISTEN_IP='__LISTEN_IP__'
export OPENCLAW_TLS_CN='__TLS_CN__'
getent hosts "$OPENCLAW_TLS_CN" || true
nslookup "$OPENCLAW_TLS_CN" || true
dig +short "$OPENCLAW_TLS_CN" || true

[init_private_ingress] Linux 访问端已有 DNS 时，继续核对解析结果：
export OPENCLAW_INGRESS_LISTEN_IP='__LISTEN_IP__'
export OPENCLAW_TLS_CN='__TLS_CN__'
getent hosts "$OPENCLAW_TLS_CN"

[init_private_ingress] Linux 访问端没有可用 DNS 时，重写 hosts（可整段复制粘贴；会先备份 hosts 并打印回滚命令）：
export OPENCLAW_INGRESS_LISTEN_IP='__LISTEN_IP__'
export OPENCLAW_TLS_CN='__TLS_CN__'
sudo OPENCLAW_INGRESS_LISTEN_IP="$OPENCLAW_INGRESS_LISTEN_IP" OPENCLAW_TLS_CN="$OPENCLAW_TLS_CN" bash <<'INNER_EOF'
set -euo pipefail
HostsPath='/etc/hosts'
BackupPath="${HostsPath}.openclaw.$(date +%Y%m%d%H%M%S).bak"
TmpFile="$(mktemp)"
if [[ -f "$HostsPath" ]]; then
  cp -a "$HostsPath" "$BackupPath"
  awk -v cn="$OPENCLAW_TLS_CN" '
    {
      line = $0
      sub(/#.*/, "", line)
      keep = 1
      n = split(line, fields, /[[:space:]]+/)
      for (i = 1; i <= n; i++) {
        if (fields[i] == cn) {
          keep = 0
          break
        }
      }
      if (keep) print $0
    }
  ' "$HostsPath" > "$TmpFile"
fi
printf '%s\t%s\n' "$OPENCLAW_INGRESS_LISTEN_IP" "$OPENCLAW_TLS_CN" >> "$TmpFile"
cat "$TmpFile" > "$HostsPath"
rm -f "$TmpFile"
printf '[init_private_ingress] hosts 备份：%s\n' "$BackupPath"
printf '[init_private_ingress] 如需回滚：sudo cp %q %q\n' "$BackupPath" "$HostsPath"
INNER_EOF
getent hosts "$OPENCLAW_TLS_CN"
EOF
}

print_macos_commands() {
  cat <<'EOF' | sed \
    -e "s|__LISTEN_IP__|${LISTEN_IP}|g" \
    -e "s|__TLS_CN__|${TLS_CN}|g"
[init_private_ingress] macOS 访问端 DNS 预判命令：
export OPENCLAW_INGRESS_LISTEN_IP='__LISTEN_IP__'
export OPENCLAW_TLS_CN='__TLS_CN__'
dscacheutil -q host -a name "$OPENCLAW_TLS_CN" || true
nslookup "$OPENCLAW_TLS_CN" || true
dig +short "$OPENCLAW_TLS_CN" || true

[init_private_ingress] macOS 访问端已有 DNS 时，继续核对解析结果：
export OPENCLAW_INGRESS_LISTEN_IP='__LISTEN_IP__'
export OPENCLAW_TLS_CN='__TLS_CN__'
dscacheutil -q host -a name "$OPENCLAW_TLS_CN"

[init_private_ingress] macOS 访问端没有可用 DNS 时，重写 hosts（可整段复制粘贴；会先备份 hosts 并打印回滚命令）：
export OPENCLAW_INGRESS_LISTEN_IP='__LISTEN_IP__'
export OPENCLAW_TLS_CN='__TLS_CN__'
sudo OPENCLAW_INGRESS_LISTEN_IP="$OPENCLAW_INGRESS_LISTEN_IP" OPENCLAW_TLS_CN="$OPENCLAW_TLS_CN" bash <<'INNER_EOF'
set -euo pipefail
HostsPath='/etc/hosts'
BackupPath="${HostsPath}.openclaw.$(date +%Y%m%d%H%M%S).bak"
TmpFile="$(mktemp)"
if [[ -f "$HostsPath" ]]; then
  cp -a "$HostsPath" "$BackupPath"
  awk -v cn="$OPENCLAW_TLS_CN" '
    {
      line = $0
      sub(/#.*/, "", line)
      keep = 1
      n = split(line, fields, /[[:space:]]+/)
      for (i = 1; i <= n; i++) {
        if (fields[i] == cn) {
          keep = 0
          break
        }
      }
      if (keep) print $0
    }
  ' "$HostsPath" > "$TmpFile"
fi
printf '%s\t%s\n' "$OPENCLAW_INGRESS_LISTEN_IP" "$OPENCLAW_TLS_CN" >> "$TmpFile"
cat "$TmpFile" > "$HostsPath"
rm -f "$TmpFile"
printf '[init_private_ingress] hosts 备份：%s\n' "$BackupPath"
printf '[init_private_ingress] 如需回滚：sudo cp %q %q\n' "$BackupPath" "$HostsPath"
INNER_EOF
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
dscacheutil -q host -a name "$OPENCLAW_TLS_CN"
EOF
}

print_windows_commands() {
  cat <<'EOF' | sed \
    -e "s|__LISTEN_IP__|${LISTEN_IP}|g" \
    -e "s|__TLS_CN__|${TLS_CN}|g"
[init_private_ingress] Windows PowerShell 访问端 DNS 预判命令：
$OpenClawIngressListenIp = '__LISTEN_IP__'
$OpenClawTlsCn = '__TLS_CN__'
Resolve-DnsName -Name $OpenClawTlsCn -ErrorAction SilentlyContinue
nslookup $OpenClawTlsCn

[init_private_ingress] Windows PowerShell 访问端已有 DNS 时，继续核对解析结果：
$OpenClawIngressListenIp = '__LISTEN_IP__'
$OpenClawTlsCn = '__TLS_CN__'
Resolve-DnsName -Name $OpenClawTlsCn -ErrorAction Stop | Where-Object { ($_.Type -eq 'A' -or $_.Type -eq 'AAAA') -and $_.IPAddress -eq $OpenClawIngressListenIp }

[init_private_ingress] Windows PowerShell 访问端没有可用 DNS 时，重写 hosts（必须以管理员身份运行 PowerShell；该段可整段复制粘贴；会先备份 hosts 并打印回滚命令；第 1 步只验证主机名到目标 IP 的对齐，不检查 443/TCP）：
$OpenClawIngressListenIp = '__LISTEN_IP__'
$OpenClawTlsCn = '__TLS_CN__'
$HostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$BackupPath = "$HostsPath.openclaw.$(Get-Date -Format 'yyyyMMddHHmmss').bak"
$ExistingLines = @()
if (Test-Path $HostsPath) {
  Copy-Item -Path $HostsPath -Destination $BackupPath -Force
  $ExistingLines = Get-Content -Path $HostsPath -Encoding ascii | Where-Object { $_ -notmatch "(^|\s)$([regex]::Escape($OpenClawTlsCn))(\s|$)" }
}
$ExistingLines += "$OpenClawIngressListenIp`t$OpenClawTlsCn"
$NewContent = (($ExistingLines -join "`r`n").TrimEnd() + "`r`n")
[System.IO.File]::WriteAllText($HostsPath, $NewContent, [System.Text.Encoding]::ASCII)
Write-Host "[init_private_ingress] hosts 备份：$BackupPath"
Write-Host "[init_private_ingress] 如需回滚：Copy-Item -Path '$BackupPath' -Destination '$HostsPath' -Force"
ipconfig /flushdns | Out-Null
Get-Content -Path $HostsPath | Select-String -Pattern "(^|\s)$([regex]::Escape($OpenClawTlsCn))(\s|$)"
ping $OpenClawTlsCn
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --platform)
      shift
      [[ $# -gt 0 ]] || fail '--platform 缺少取值'
      case "$1" in
        windows|linux|macos|all) PLATFORM="$1" ;;
        *) fail "--platform 只允许 windows|linux|macos|all：$1" ;;
      esac
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      fail "未知参数：$1；使用 --help 查看用法"
      ;;
  esac
done

if [[ $# -gt 0 ]]; then
  LISTEN_IP="$1"
  shift
fi
if [[ $# -gt 0 ]]; then
  TLS_CN="$1"
  shift
fi
[[ $# -eq 0 ]] || fail '显式值最多只允许 <listen_ip> <tls_cn> 两个参数；若需覆盖请使用 -- <listen_ip> <tls_cn>'

if [[ -z "$LISTEN_IP" ]]; then
  DEFAULT_LISTEN_IP="$(detect_first_private_ipv4 || true)"
  [[ -n "$DEFAULT_LISTEN_IP" ]] || fail '未能从 hostname -I 探测到首个 RFC1918 私网 IPv4；请使用 bash ./scripts/setup/init_private_ingress.sh --platform windows -- <listen_ip> [tls_cn]'
  LISTEN_IP="$DEFAULT_LISTEN_IP"
fi

is_private_or_loopback_ip "$LISTEN_IP" || fail "OPENCLAW_INGRESS_LISTEN_IP 必须是 RFC1918/loopback IPv4 或 ULA/loopback IPv6 字面量：$LISTEN_IP"
openclaw_tls_hostname_require "$TLS_CN" 'OPENCLAW_TLS_CN' || fail "OPENCLAW_TLS_CN 不符合 TLS 主机名合同"

bash "$ROOT_DIR/scripts/setup/apply_site_env_values.sh" \
  --file "$TARGET_FILE" \
  --init-from-example \
  --set "OPENCLAW_INGRESS_LISTEN_IP=$LISTEN_IP" \
  --set "OPENCLAW_TLS_CN=$TLS_CN"

cat <<EOF
[init_private_ingress] private ingress 输入：
[init_private_ingress] OPENCLAW_INGRESS_LISTEN_IP=${LISTEN_IP}
[init_private_ingress] OPENCLAW_TLS_CN=${TLS_CN}
[init_private_ingress] 访问端输出平台=${PLATFORM}
[init_private_ingress] deploy/site.env 已同步更新；继续在目标机补齐其余必填项后，再执行 one_click_config.sh

EOF

case "$PLATFORM" in
  windows)
    print_windows_commands
    ;;
  linux)
    print_linux_commands
    ;;
  macos)
    print_macos_commands
    ;;
  all)
    print_windows_commands
    printf '\n'
    print_linux_commands
    printf '\n'
    print_macos_commands
    ;;
esac
