from __future__ import annotations

import re
import unittest
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.static_truth import REPO_CONTRACTS, repo_contract_relpath


ROOT_DIR = resolve_repo_root(Path(__file__))
REPO_CONTRACT_IDS = tuple(REPO_CONTRACTS)
ALLOWED_RELATIVE_FILES = {
    Path('python/openclaw/lib/repo/bootstrap.py').as_posix(),
    Path('python/openclaw/lib/repo/contracts.py').as_posix(),
    Path('python/openclaw/lib/repo/static_truth.py').as_posix(),
    Path('scripts/lib/repo_python_env.sh').as_posix(),
    Path('scripts/lib/repo_contracts.sh').as_posix(),
}
ALLOWED_RELATIVE_PREFIXES = (
    'python/openclaw/doctor/agent_governance/',
    'python/openclaw/doctor/agent_modules/',
)
TOP_LEVEL_REPO_CONTRACT_COMMAND_SUBSTITUTION = re.compile(
    r'^[A-Z0-9_]+=.*\$\((?:repo_contract_path|repo_contract_relpath)\b[^)]*\)'
)


class RepoContractLiteralGuardTest(unittest.TestCase):
    def test_repo_contract_paths_do_not_leak_back_into_python_or_shell(self) -> None:
        forbidden_literals = {repo_contract_relpath(contract_id) for contract_id in REPO_CONTRACT_IDS}
        violations: list[str] = []
        for root in ('python', 'scripts'):
            for path in (ROOT_DIR / root).rglob('*'):
                if not path.is_file():
                    continue
                rel_path = path.relative_to(ROOT_DIR).as_posix()
                if rel_path in ALLOWED_RELATIVE_FILES:
                    continue
                if rel_path.startswith(ALLOWED_RELATIVE_PREFIXES):
                    continue
                if path.suffix == '.md':
                    continue
                if path.suffix == '.pyc':
                    continue
                if '__pycache__' in path.parts:
                    continue
                if '/tests/' in f'/{rel_path}/':
                    continue
                content = path.read_text(encoding='utf-8', errors='ignore')
                compact = ''.join(content.split()).replace('"', '').replace("'", '')
                for literal in sorted(forbidden_literals):
                    if literal in content or literal in compact:
                        violations.append(f'{rel_path}: {literal}')
        self.assertEqual(violations, [])

    def test_shell_top_level_contract_constants_use_assignment_helpers(self) -> None:
        violations: list[str] = []
        for path in sorted((ROOT_DIR / 'scripts').rglob('*.sh')):
            rel_path = path.relative_to(ROOT_DIR).as_posix()
            for line_number, raw_line in enumerate(path.read_text(encoding='utf-8', errors='ignore').splitlines(), start=1):
                if not raw_line or raw_line[0] in {' ', '\t', '#'}:
                    continue
                if TOP_LEVEL_REPO_CONTRACT_COMMAND_SUBSTITUTION.search(raw_line):
                    violations.append(f'{rel_path}:{line_number}: {raw_line.strip()}')
        self.assertEqual(violations, [])


if __name__ == '__main__':
    unittest.main()
