"""为生成文档提供只读模式和带文件身份校验的受控原子写入。"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from openclaw.lib.io.state import with_lock_dir, write_text_atomic


@dataclass(frozen=True)
class DocumentSnapshot:
    """记录生成前的文档内容和文件身份，供提交前检查文件变化。"""

    text: str | None
    identity: tuple[int, int, int, int, str] | None


def _checked_target(root_dir: Path, target: Path) -> Path:
    """验证目标留在仓库内且整条路径不经过链接或 Windows reparse point。

    参数：
        root_dir（Path）：受控仓库根目录。
        target（Path）：需要读取或写入的文档路径。

    返回：
        Path：通过包含关系和逐级路径检查的绝对目标。

    异常：
        ValueError：目标越界、父目录缺失、路径为链接，或目标不是单链接普通文件。
    """
    root = root_dir.absolute()
    path = target.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError('生成文档目标必须留在仓库目录内') from exc
    if '..' in relative.parts or not relative.parts:
        raise ValueError('生成文档目标不是合法仓库文件')
    cursor = root
    for index, part in enumerate(('', *relative.parts)):
        if part:
            cursor /= part
        final = index == len(relative.parts)
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            if final:
                continue
            raise ValueError('生成文档父目录不存在') from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('生成文档路径不得经过链接或 reparse point')
        if final:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('生成文档目标必须是单链接普通文件')
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError('生成文档父路径必须是目录')
    return path


def read_document(root_dir: Path, target: Path) -> DocumentSnapshot:
    """检查生成目标的路径与文件身份，读取内容快照；系统支持时以 no-follow 打开文件。

    参数：
        root_dir（Path）：限制读取范围的仓库根目录。
        target（Path）：生成文档目标，允许尚不存在。

    返回：
        DocumentSnapshot：原内容和设备、inode、大小、修改时间、内容摘要；不存在时均为空。

    异常：
        ValueError：路径不安全或文件在读取过程中变化。
        OSError：目标或父目录不可访问，或目标在读取过程中被删除。
        UnicodeDecodeError：已有文档不是 UTF-8。

    副作用：
        打开并关闭只读文件描述符，访问目标内容和文件元信息以构建读取快照。
    """
    path = _checked_target(root_dir, target)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    except FileNotFoundError:
        return DocumentSnapshot(None, None)
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        opened_target = _checked_target(root_dir, path).stat(follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (opened_target.st_dev, opened_target.st_ino):
            raise ValueError('生成文档在打开过程中变化，请重新生成')
        data = source.read()
        after = os.fstat(source.fileno())
    current = _checked_target(root_dir, path).stat(follow_symlinks=False)
    before_fields = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_fields = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    current_fields = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    if before_fields != after_fields or after_fields != current_fields:
        raise ValueError('生成文档在读取过程中变化，请重新生成')
    return DocumentSnapshot(data.decode('utf-8'), (*after_fields, hashlib.sha256(data).hexdigest()))


def _open_parent_directory(root_dir: Path, target: Path) -> int:
    """逐级以 no-follow 方式打开仓内父目录，返回锚定暂存和替换的目录描述符。

    参数：
        root_dir（Path）：已经校验的仓库根目录。
        target（Path）：已经校验的生成目标绝对路径。
    返回：
        int：调用方负责关闭的最终父目录描述符。
    异常：
        RuntimeError：平台缺少目录 FD 或 no-follow 能力，无法锚定安全写入目录。
        OSError：目录在打开时缺失、被替换成链接或无法访问。
    副作用：
        只打开目录描述符，不创建文件；中途失败时关闭已打开的描述符。
    """
    if not hasattr(os, 'O_DIRECTORY') or not hasattr(os, 'O_NOFOLLOW') or any(function not in os.supports_dir_fd for function in (os.open, os.rename, os.unlink)):
        raise RuntimeError('生成文档写入要求 Linux 控制面容器的 no-follow 目录 FD；当前平台可使用 --check 或 --stdout')
    root = root_dir.absolute()
    relative = target.relative_to(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in relative.parent.parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_document(root_dir: Path, target: Path, content: str, expected: DocumentSnapshot) -> None:
    """持有协作生成锁并锚定父目录，在实际提交点比较身份和内容后替换文档。

    参数：
        root_dir（Path）：限制写入范围的仓库根目录。
        target（Path）：经过读取快照验证的文档目标。
        content（str）：新生成的 UTF-8 内容。
        expected（DocumentSnapshot）：渲染开始前读取的快照。

    返回：
        None：提交成功后返回。

    异常：
        ValueError：路径不安全或文档被其他写入者修改。
        RuntimeError：无法取得生成目标锁，或平台不支持目录 FD 安全写入。
        OSError：路径不可访问，或暂存、身份读取及替换失败。

    副作用：
        创建短生命周期锁与暂存文件，校验通过后替换目标文档，关闭目录描述符并清理暂存文件。
        拒绝渲染和暂存期间的内容或目录变化；提交前比较之后仍不是非协作编辑器的 OS 级 CAS。
    """
    path = _checked_target(root_dir, target)
    lock_path = path.with_name(f'.{path.name}.generated.lock')
    if lock_path.exists() or lock_path.is_symlink():
        lock_info = lock_path.lstat()
        if not stat.S_ISDIR(lock_info.st_mode) or getattr(lock_info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('生成文档锁必须是普通目录')
    with with_lock_dir(lock_path):
        descriptor = _open_parent_directory(root_dir, path)
        identity = os.fstat(descriptor)

        def commit_guard() -> None:
            """在暂存完成后的提交点验证原父目录和目标文档快照。

            返回：
                None：父目录仍为原目录且文档与生成前快照相同。
            异常：
                ValueError：目录、目标路径或文档内容发生变化，拒绝提交。
                OSError：提交前无法读取目标身份。
            副作用：
                只读路径属性和目标内容，不创建文件或探测运行 state。
            """
            checked = _checked_target(root_dir, path)
            current_parent = checked.parent.stat(follow_symlinks=False)
            if (current_parent.st_dev, current_parent.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('生成文档父目录已变化，请重新生成')
            if read_document(root_dir, checked) != expected:
                raise ValueError('生成文档已被其他写入者修改，请重新生成')

        try:
            commit_guard()
            write_text_atomic(path, content, dir_fd=descriptor, commit_guard=commit_guard)
        finally:
            os.close(descriptor)


def add_output_modes(parser: argparse.ArgumentParser) -> None:
    """注册互斥的只检查和标准输出模式；两者均未指定时执行写入模式。

    参数：
        parser（argparse.ArgumentParser）：生成文档命令解析器。

    返回：
        None：直接修改解析器参数组。
    """
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='只检查生成文档是否同步')
    modes.add_argument('--stdout', action='store_true', help='只输出生成内容')


def publish_document(root_dir: Path, target: Path, content: str, snapshot: DocumentSnapshot, *, check: bool, stdout: bool, label: str) -> int:
    """按命令模式输出、核对或提交生成文档，报告稳定的退出码。

    参数：
        root_dir（Path）：仓库根目录。
        target（Path）：输出文档路径。
        content（str）：确定性生成内容。
        snapshot（DocumentSnapshot）：生成前文档快照。
        check（bool）：是否只检查同步。
        stdout（bool）：是否只向标准输出打印。
        label（str）：提示中的命令标签。

    返回：
        int：同步或成功输出/写入返回 0，检查发现漂移返回 1。

    异常：
        ValueError：写入安全检查或并发比较失败。
        RuntimeError：无法取得生成锁，或平台不支持目录 FD 安全写入。
        OSError：文档输出或文件写入失败。

    副作用：
        stdout/check 只输出文本；默认模式通过受控原子写入提交文档。
    """
    if stdout:
        sys.stdout.write(content)
        return 0
    relative = target.relative_to(root_dir).as_posix()
    if check:
        if snapshot.text == content:
            sys.stdout.write(f'[{label}] 已同步\n')
            return 0
        sys.stderr.write(f'[{label}] 文档未同步：{relative}\n')
        return 1
    write_document(root_dir, target, content, snapshot)
    sys.stdout.write(f'[{label}] 已写入 {relative}\n')
    return 0
