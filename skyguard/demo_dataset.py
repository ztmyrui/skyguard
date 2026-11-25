"""Snapshot only application database files, keeping credentials out of the archive."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

DATABASES = ('business.sqlite3', 'civic.sqlite3', 'training.sqlite3', 'checkpoints.sqlite3')


def _backup(source, target):
    if source.is_file():
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(target)) as copy:
                original.backup(copy)


def create_demo(root):
    demo = root / 'demo'
    if not demo.exists():
        with TemporaryDirectory(dir=root) as temporary:
            staged = Path(temporary) / 'demo'
            staged.mkdir()
            for name in DATABASES:
                _backup(root / name, staged / name)
            staged.rename(demo)
    return demo


def save_demo(demo, archive, when):
    with TemporaryDirectory(dir=archive.parent) as temporary:
        staged = Path(temporary)
        with ZipFile(staged / 'snapshot.zip', 'w', ZIP_DEFLATED) as package:
            package.writestr('clock.txt', when.isoformat())
            for name in DATABASES:
                _backup(demo / name, staged / name)
                if (staged / name).exists():
                    package.write(staged / name, name)
        (staged / 'snapshot.zip').replace(archive)


def load_demo(root, archive):
    demo = root / 'demo'
    with TemporaryDirectory(dir=root) as temporary:
        old, new = Path(temporary) / 'old', Path(temporary) / 'new'
        new.mkdir()
        with ZipFile(archive) as package:
            names = package.namelist()
            if len(names) != len(set(names)) or set(names) - {*DATABASES, 'clock.txt'} or 'clock.txt' not in names:
                raise ValueError('请选择低空守望演示存档')
            when = datetime.fromisoformat(package.read('clock.txt').decode())
            if when.utcoffset() is None:
                raise ValueError('存档时间缺少时区')
            for name in DATABASES:
                if name in names:
                    package.extract(name, new)
                    with closing(sqlite3.connect((new / name).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('存档数据库校验失败')
        if demo.exists():
            demo.rename(old)
        try:
            new.rename(demo)
        except OSError:
            if old.exists():
                old.rename(demo)
            raise
    return demo, when
