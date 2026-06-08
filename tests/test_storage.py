# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.storage import _atomic_write_json


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / 'data.json'
    _atomic_write_json(target, {'key': 'value'})
    assert target.exists()
    assert json.loads(target.read_text()) == {'key': 'value'}


def test_atomic_write_creates_parent_dirs(tmp_path):
    target = tmp_path / 'a' / 'b' / 'data.json'
    _atomic_write_json(target, [1, 2, 3])
    assert json.loads(target.read_text()) == [1, 2, 3]


def test_atomic_write_overwrites(tmp_path):
    target = tmp_path / 'data.json'
    _atomic_write_json(target, {'v': 1})
    _atomic_write_json(target, {'v': 2})
    assert json.loads(target.read_text()) == {'v': 2}


def test_atomic_write_ensure_ascii_false(tmp_path):
    target = tmp_path / 'data.json'
    _atomic_write_json(target, {'name': 'Тест'}, ensure_ascii=False)
    raw = target.read_text()
    assert 'Тест' in raw


def test_atomic_write_no_temp_left_on_success(tmp_path):
    target = tmp_path / 'data.json'
    _atomic_write_json(target, {'ok': True})
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == 'data.json'
