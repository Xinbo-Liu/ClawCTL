from __future__ import annotations

from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


class ShellCertificateInputContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        bash = resolve_bash_executable()
        if not bash:
            raise unittest.SkipTest('未找到可用 bash；跳过证书输入契约测试')
        cls.bash = Path(bash)

    def test_install_provided_cert_rejects_non_strict_certificate_inputs(self) -> None:
        openssl_probe = subprocess.run(
            [str(self.bash), '-lc', 'command -v openssl >/dev/null 2>&1'],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        if openssl_probe.returncode != 0:
            raise unittest.SkipTest('缺少 openssl；跳过 provided_files 证书契约测试')

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
work="$root/work"
mkdir -p "$work"

make_san_cert() {
  local cert="$1"
  local key="$2"
  local dns="$3"
  cat > "$work/san.cnf" <<EOF
[ req ]
default_bits = 256
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req
[ dn ]
CN = $host
[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[ alt_names ]
DNS.1 = $dns
EOF
  if [[ -f "$work/$key" ]]; then
    openssl req -x509 -nodes -days 30 \
      -key "$work/$key" \
      -out "$work/$cert" \
      -config "$work/san.cnf" \
      -extensions v3_req >/dev/null 2>&1
  else
    openssl ecparam -name prime256v1 -genkey -noout -out "$work/$key" >/dev/null 2>&1
    openssl req -x509 -nodes -days 30 \
      -key "$work/$key" \
      -out "$work/$cert" \
      -config "$work/san.cnf" \
      -extensions v3_req >/dev/null 2>&1
  fi
}

expect_fail() {
  local cert="$1"
  local key="$2"
  local expected="$3"
  local out="$work/out.txt"
  if bash "$root/deploy/nginx/install-provided-cert.sh" "$cert" "$key" "$host" >"$out" 2>&1; then
    cat "$out"
    exit 1
  fi
  grep -F "$expected" "$out" >/dev/null || { cat "$out"; exit 1; }
}

make_san_cert exact.crt exact.key "$host"
if ! bash "$root/deploy/nginx/install-provided-cert.sh" "$work/exact.crt" "$work/exact.key" "$host" >"$work/exact.out" 2>&1; then
  cat "$work/exact.out"
  exit 1
fi

MSYS_NO_PATHCONV=1 openssl req -x509 -nodes -days 30 \
  -subj "/CN=$host" \
  -key "$work/exact.key" \
  -out "$work/cn-only.crt" >/dev/null 2>&1
expect_fail "$work/cn-only.crt" "$work/exact.key" '必须包含精确 dNSName SAN'
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_install_provided_cert_rejects_wildcard_san_input(self) -> None:
        openssl_probe = subprocess.run(
            [str(self.bash), '-lc', 'command -v openssl >/dev/null 2>&1'],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        if openssl_probe.returncode != 0:
            raise unittest.SkipTest('缺少 openssl；跳过 wildcard SAN 证书契约测试')

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
work="$root/work"
mkdir -p "$work"
cat > "$work/san.cnf" <<EOF
[ req ]
default_bits = 256
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req
[ dn ]
CN = $host
[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[ alt_names ]
DNS.1 = *.internal.example
EOF

openssl ecparam -name prime256v1 -genkey -noout -out "$work/exact.key" >/dev/null 2>&1
openssl req -x509 -nodes -days 30 \
  -key "$work/exact.key" \
  -out "$work/wildcard.crt" \
  -config "$work/san.cnf" \
  -extensions v3_req >/dev/null 2>&1

out="$work/out.txt"
if bash "$root/deploy/nginx/install-provided-cert.sh" "$work/wildcard.crt" "$work/exact.key" "$host" >"$out" 2>&1; then
  cat "$out"
  exit 1
fi
grep -F '必须包含精确 dNSName SAN' "$out" >/dev/null || { cat "$out"; exit 1; }
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-wildcard-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


    def test_install_provided_cert_rejects_expired_and_encrypted_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
work="$root/work"
mkdir -p "$work"
cat > "$work/san.cnf" <<EOF
[ req ]
default_bits = 256
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req
[ dn ]
CN = $host
[ v3_req ]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[ alt_names ]
DNS.1 = $host
EOF
openssl ecparam -name prime256v1 -genkey -noout -out "$work/exact.key" >/dev/null 2>&1
openssl req -x509 -nodes -days 30 \
  -key "$work/exact.key" \
  -out "$work/exact.crt" \
  -config "$work/san.cnf" \
  -extensions v3_req >/dev/null 2>&1

expect_fail() {
  local cert="$1"
  local key="$2"
  local expected="$3"
  local out="$work/out.txt"
  if bash "$root/deploy/nginx/install-provided-cert.sh" "$cert" "$key" "$host" >"$out" 2>&1; then
    cat "$out"
    exit 1
  fi
  grep -F "$expected" "$out" >/dev/null || { cat "$out"; exit 1; }
}

openssl req -new \
  -key "$work/exact.key" \
  -config "$work/san.cnf" \
  -out "$work/expired.csr" >/dev/null 2>&1
openssl x509 -req \
  -in "$work/expired.csr" \
  -signkey "$work/exact.key" \
  -days 0 \
  -out "$work/expired.crt" >/dev/null 2>&1
expect_fail "$work/expired.crt" "$work/exact.key" '证书已经过期'

openssl pkey -aes256 -in "$work/exact.key" -out "$work/encrypted.key" -passout pass:secret >/dev/null 2>&1
expect_fail "$work/exact.crt" "$work/encrypted.key" '未加密 PEM 私钥'
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-expiry-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


    def test_install_provided_cert_rejects_output_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            nginx_source = ROOT_DIR / 'deploy' / 'nginx'
            shutil.copytree(nginx_source, temp_root / 'deploy' / 'nginx', ignore=shutil.ignore_patterns('certs'))
            (temp_root / 'scripts' / 'setup' / 'lib').mkdir(parents=True)
            shutil.copy2(
                ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
                temp_root / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh',
            )
            script = r'''
set -euo pipefail
root="$1"
host='openclaw.internal.example'
cert_dir="$root/deploy/nginx/certs"
work="$root/work"
mkdir -p "$cert_dir" "$work"
printf 'placeholder-cert' > "$cert_dir/openclaw.crt"
printf 'placeholder-key' > "$cert_dir/openclaw.key"

expect_fail() {
  local cert="$1"
  local key="$2"
  local expected="$3"
  local out="$work/out.txt"
  if bash "$root/deploy/nginx/install-provided-cert.sh" "$cert" "$key" "$host" >"$out" 2>&1; then
    cat "$out"
    exit 1
  fi
  grep -F "$expected" "$out" >/dev/null || { cat "$out"; exit 1; }
}

expect_fail "$cert_dir/openclaw.crt" "$cert_dir/openclaw.key" '不得位于输出证书目录'

if ln -s "$cert_dir/openclaw.crt" "$work/link-output.crt" 2>/dev/null \
  && ln -s "$cert_dir/openclaw.key" "$work/link-output.key" 2>/dev/null \
  && [[ -L "$work/link-output.crt" && -L "$work/link-output.key" ]]; then
  expect_fail "$work/link-output.crt" "$work/link-output.key" '不得指向输出证书目录'
fi
'''
            result = subprocess.run(
                [str(self.bash), '-lc', script, 'provided-files-path-contract', str(temp_root).replace('\\', '/')],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
