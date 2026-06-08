# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.ui.widgets.tag_card import TagCard


def _emoji_tag(**overrides):
    tag = {
        'id': 'test01', 'name': 'Fire',
        'icon_type': 'emoji', 'icon_value': '🔥',
        'builtin': False, 'order': 1, 'releases': [1, 2, 3],
    }
    tag.update(overrides)
    return tag


def _color_tag(**overrides):
    tag = {
        'id': 'test02', 'name': 'Blue Tag',
        'icon_type': 'color', 'icon_value': 'blue',
        'builtin': False, 'order': 2, 'releases': [10],
    }
    tag.update(overrides)
    return tag


def test_emoji_icon_rendering():
    tag = _emoji_tag()
    card = TagCard(tag)
    assert card.icon_label.get_label() == '🔥'
    assert card.icon_label.get_visible() is True


def test_color_icon_rendering():
    tag = _color_tag()
    card = TagCard(tag)
    assert card.icon_label.get_visible() is False


def test_count_label_visible():
    tag = _emoji_tag(releases=[1, 2, 3])
    card = TagCard(tag)
    assert card.count_label.get_visible() is True
    assert card.count_label.get_label() == '3'


def test_count_label_zero_hidden():
    tag = _emoji_tag(releases=[])
    card = TagCard(tag)
    assert card.count_label.get_visible() is False


def test_title_from_tag():
    tag = _emoji_tag(name='My Tag')
    card = TagCard(tag)
    assert card.title_label.get_label() == 'My Tag'
