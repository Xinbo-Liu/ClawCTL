from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from unittest.mock import patch


class HermeticGit:
    """进程内最小 Git 双端，用于覆盖 release 来源校验而不依赖容器安装 git。"""

    STATE_NAME = 'openclaw_fake_git'
    BARE_NAME = 'openclaw_fake_bare'
    IGNORED_TREE_PARTS = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache'}

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        check: bool = False,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(item) for item in command]
        if not argv or Path(argv[0]).name != 'git':
            raise AssertionError(f'hermetic git 只接受 git 命令：{argv!r}')
        code, stdout, stderr = self._dispatch(Path(cwd or '.').resolve(), argv[1:])
        completed = subprocess.CompletedProcess(argv, code, stdout, stderr)
        if check and code != 0:
            raise subprocess.CalledProcessError(code, argv, output=stdout, stderr=stderr)
        return completed

    def git(self, cwd: Path, *args: str) -> str:
        code, stdout, stderr = self._dispatch(cwd.resolve(), [str(item) for item in args])
        if code != 0:
            raise AssertionError(f'git {" ".join(args)} failed\n{stdout}\n{stderr}')
        return stdout.strip()

    def _state_dir(self, repo_root: Path) -> Path:
        return repo_root / '.git' / self.STATE_NAME

    def _bare_state_dir(self, repo_root: Path) -> Path:
        return repo_root / self.BARE_NAME

    def _find_repo_root(self, cwd: Path) -> Path | None:
        path = cwd.resolve()
        while True:
            if (self._state_dir(path) / 'state.json').is_file():
                return path
            if path.parent == path:
                return None
            path = path.parent

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True) + '\n', encoding='utf-8')

    def _load_state(self, repo_root: Path) -> dict[str, object]:
        return self._read_json(self._state_dir(repo_root) / 'state.json')

    def _save_state(self, repo_root: Path, payload: dict[str, object]) -> None:
        self._write_json(self._state_dir(repo_root) / 'state.json', payload)

    def _load_bare_state(self, repo_root: Path) -> dict[str, object]:
        return self._read_json(self._bare_state_dir(repo_root) / 'state.json')

    def _save_bare_state(self, repo_root: Path, payload: dict[str, object]) -> None:
        self._write_json(self._bare_state_dir(repo_root) / 'state.json', payload)

    @staticmethod
    def _initial_state() -> dict[str, object]:
        return {'head': '', 'branch': 'main', 'branches': {}, 'remote_origin': '', 'commits': {}}

    def _iter_files(self, root: Path, *, base_diff: bool = False) -> list[Path]:
        paths: list[Path] = []
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            parts = rel.parts
            if any(part in self.IGNORED_TREE_PARTS for part in parts):
                continue
            if base_diff and self._base_diff_excludes(rel):
                continue
            paths.append(path)
        return sorted(paths, key=lambda item: item.relative_to(root).as_posix())

    @staticmethod
    def _base_diff_excludes(rel_path: Path) -> bool:
        parts = rel_path.parts
        rel_posix = rel_path.as_posix()
        if parts and parts[0] in {'state', 'tmp', 'artifacts', 'release'}:
            return True
        if rel_posix in {'openclaw-stack.lock.json', 'config/control_plane/profile_registry.tsv'}:
            return True
        if len(parts) >= 2 and parts[0] == 'agent' and parts[1] == 'extensions':
            return True
        return rel_path.suffix in {'.pyc', '.pyo'}

    def _tree_hash(self, root: Path, *, base_diff: bool = False) -> str:
        hasher = hashlib.sha256()
        for path in self._iter_files(root, base_diff=base_diff):
            rel = path.relative_to(root).as_posix()
            hasher.update(rel.encode('utf-8'))
            hasher.update(b'\0')
            hasher.update(path.read_bytes())
            hasher.update(b'\0')
        return hasher.hexdigest()

    def _snapshot_tree(self, root: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for path in self._iter_files(root):
            rel = path.relative_to(root)
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)

    @staticmethod
    def _replace_worktree(repo_root: Path, source: Path) -> None:
        for child in repo_root.iterdir():
            if child.name == '.git':
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        shutil.copytree(source, repo_root, dirs_exist_ok=True)

    def _dispatch(self, cwd: Path, args: list[str]) -> tuple[int, str, str]:
        if not args:
            return 1, '', 'missing command'
        command = args[0]
        if command == 'init':
            return self._cmd_init(cwd, args)
        if command == 'clone':
            return self._cmd_clone(args)
        repo_root = self._find_repo_root(cwd)
        if repo_root is None:
            return 1, '', 'not a fake git repository'
        if command == 'config':
            return self._cmd_config(repo_root, args)
        if command == 'add':
            return 0, '', ''
        if command == 'commit':
            return self._cmd_commit(repo_root, args)
        if command == 'rev-parse':
            return self._cmd_rev_parse(repo_root, args)
        if command == 'describe':
            return 1, '', ''
        if command == 'checkout':
            return self._cmd_checkout(repo_root, args)
        if command == 'remote' and len(args) >= 4 and args[1:3] == ['add', 'origin']:
            state = self._load_state(repo_root)
            state['remote_origin'] = str(Path(args[3]).resolve())
            self._save_state(repo_root, state)
            return 0, '', ''
        if command == 'push':
            return self._cmd_push(repo_root, args)
        if command == 'status':
            return 0, '', ''
        if command == 'diff' and len(args) >= 3 and args[1] == '--quiet':
            return self._cmd_diff(repo_root, args)
        return 1, '', 'unsupported fake git command: ' + ' '.join(args)

    def _cmd_init(self, cwd: Path, args: list[str]) -> tuple[int, str, str]:
        if '--bare' in args:
            target = self._target_arg(cwd, args[1:])
            self._bare_state_dir(target).mkdir(parents=True, exist_ok=True)
            self._save_bare_state(target, self._initial_state())
            return 0, '', ''
        target = self._target_arg(cwd, args[1:])
        self._state_dir(target).mkdir(parents=True, exist_ok=True)
        self._save_state(target, self._initial_state())
        return 0, '', ''

    @staticmethod
    def _target_arg(cwd: Path, args: list[str]) -> Path:
        candidates = [item for item in args if not item.startswith('-')]
        return Path(candidates[-1]).resolve() if candidates else cwd.resolve()

    def _cmd_config(self, repo_root: Path, args: list[str]) -> tuple[int, str, str]:
        if args[1:3] == ['--get', 'remote.origin.url']:
            remote = str(self._load_state(repo_root).get('remote_origin') or '')
            return (0, remote + '\n', '') if remote else (1, '', '')
        return 0, '', ''

    def _cmd_commit(self, repo_root: Path, args: list[str]) -> tuple[int, str, str]:
        message = ''
        if '-m' in args and args.index('-m') + 1 < len(args):
            message = args[args.index('-m') + 1]
        state = self._load_state(repo_root)
        full_hash = self._tree_hash(repo_root)
        base_hash = self._tree_hash(repo_root, base_diff=True)
        previous = str(state.get('head') or '')
        commit = hashlib.sha1(f'{previous}\0{message}\0{full_hash}'.encode('utf-8')).hexdigest()
        commit_dir = self._state_dir(repo_root) / 'commits' / commit
        self._snapshot_tree(repo_root, commit_dir / 'tree')
        commits = state.get('commits') if isinstance(state.get('commits'), dict) else {}
        commits[commit] = {'fullHash': full_hash, 'baseHash': base_hash}
        branches = state.get('branches') if isinstance(state.get('branches'), dict) else {}
        branch = str(state.get('branch') or 'main')
        branches[branch] = commit
        state.update({'head': commit, 'branches': branches, 'commits': commits})
        self._save_state(repo_root, state)
        return 0, commit + '\n', ''

    def _cmd_rev_parse(self, repo_root: Path, args: list[str]) -> tuple[int, str, str]:
        if len(args) >= 2 and args[1] == '--show-toplevel':
            return 0, str(repo_root) + '\n', ''
        if len(args) >= 2:
            commit = self._resolve_ref(self._load_state(repo_root), args[1])
            if commit:
                return 0, commit + '\n', ''
        return 1, '', ''

    @staticmethod
    def _resolve_ref(state: dict[str, object], ref: str) -> str:
        if ref == 'HEAD':
            return str(state.get('head') or '')
        branches = state.get('branches') if isinstance(state.get('branches'), dict) else {}
        if ref in branches:
            return str(branches[ref] or '')
        commits = state.get('commits') if isinstance(state.get('commits'), dict) else {}
        if len(ref) == 40 and ref in commits:
            return ref
        return ''

    def _cmd_checkout(self, repo_root: Path, args: list[str]) -> tuple[int, str, str]:
        state = self._load_state(repo_root)
        if len(args) >= 3 and args[1] == '-B':
            branch = args[2]
            branches = state.get('branches') if isinstance(state.get('branches'), dict) else {}
            if state.get('head'):
                branches[branch] = str(state.get('head') or '')
            state.update({'branch': branch, 'branches': branches})
            self._save_state(repo_root, state)
            return 0, '', ''
        if len(args) < 2:
            return 1, '', 'checkout requires a ref'
        commit = self._resolve_ref(state, args[1])
        if not commit:
            return 1, '', f'unknown ref: {args[1]}'
        tree = self._state_dir(repo_root) / 'commits' / commit / 'tree'
        if not tree.is_dir():
            return 1, '', f'missing commit tree: {commit}'
        self._replace_worktree(repo_root, tree)
        state['head'] = commit
        self._save_state(repo_root, state)
        return 0, '', ''

    def _cmd_clone(self, args: list[str]) -> tuple[int, str, str]:
        filtered = [item for item in args[1:] if item != '--no-checkout']
        if len(filtered) < 2:
            return 1, '', 'clone requires repo and destination'
        remote = Path(filtered[-2]).resolve()
        dest = Path(filtered[-1]).resolve()
        remote_state = self._load_bare_state(remote)
        if not remote_state:
            return 1, '', f'unknown fake remote: {remote}'
        dest.mkdir(parents=True, exist_ok=True)
        self._state_dir(dest).mkdir(parents=True, exist_ok=True)
        remote_commits = self._bare_state_dir(remote) / 'commits'
        local_commits = self._state_dir(dest) / 'commits'
        if remote_commits.is_dir():
            shutil.copytree(remote_commits, local_commits, dirs_exist_ok=True)
        state = dict(remote_state)
        state['remote_origin'] = str(remote)
        self._save_state(dest, state)
        head = str(state.get('head') or '')
        if head:
            tree = local_commits / head / 'tree'
            if tree.is_dir():
                self._replace_worktree(dest, tree)
        return 0, '', ''

    def _cmd_push(self, repo_root: Path, args: list[str]) -> tuple[int, str, str]:
        state = self._load_state(repo_root)
        remote_value = str(state.get('remote_origin') or '')
        if not remote_value:
            return 1, '', 'missing origin remote'
        remote = Path(remote_value).resolve()
        branch = args[-1] if args else str(state.get('branch') or 'main')
        head = str(state.get('head') or '')
        if not head:
            return 1, '', 'nothing to push'
        remote_state = self._load_bare_state(remote) or self._initial_state()
        source_commit = self._state_dir(repo_root) / 'commits' / head
        target_commit = self._bare_state_dir(remote) / 'commits' / head
        if target_commit.exists():
            shutil.rmtree(target_commit)
        shutil.copytree(source_commit, target_commit)
        commits = remote_state.get('commits') if isinstance(remote_state.get('commits'), dict) else {}
        local_commits = state.get('commits') if isinstance(state.get('commits'), dict) else {}
        commits[head] = local_commits.get(head, {})
        branches = remote_state.get('branches') if isinstance(remote_state.get('branches'), dict) else {}
        branches[branch] = head
        remote_state.update({'head': head, 'branch': branch, 'branches': branches, 'commits': commits})
        self._save_bare_state(remote, remote_state)
        return 0, '', ''

    def _cmd_diff(self, repo_root: Path, args: list[str]) -> tuple[int, str, str]:
        state = self._load_state(repo_root)
        commits = state.get('commits') if isinstance(state.get('commits'), dict) else {}
        commit_info = commits.get(args[2]) if isinstance(commits.get(args[2]), dict) else None
        if not commit_info:
            return 2, '', ''
        current_hash = self._tree_hash(repo_root, base_diff=True)
        return (0, '', '') if str(commit_info.get('baseHash') or '') == current_hash else (1, '', '')


@contextlib.contextmanager
def git_test_environment() -> Iterator[HermeticGit]:
    """补齐 Git 行为并拦截 release 模块的 subprocess.run，避免服务器缺 git 时跳过安全用例。"""
    fake = HermeticGit()
    with patch('openclaw.control_plane.stack.release.subprocess.run', side_effect=fake.run):
        yield fake
