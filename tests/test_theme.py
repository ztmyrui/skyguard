"""Verify appearance tokens and the standalone local preference."""
import pytest

from skyguard.theme import DEFAULT_THEME, THEMES, load_theme, save_theme, stylesheet


def test_themes_have_the_same_tokens():
    assert THEMES['night'].keys() == THEMES['klein'].keys()


def test_default_theme_handles_absent_and_invalid_preferences(tmp_path):
    assert load_theme(tmp_path) == DEFAULT_THEME == 'klein'
    (tmp_path / 'ui-settings.json').write_text('{"theme":"invalid"}', encoding='utf8')
    assert load_theme(tmp_path) == 'klein'


@pytest.mark.parametrize('name', ['klein', 'night'])
def test_theme_preference_round_trips(tmp_path, name):
    save_theme(tmp_path, name)
    assert load_theme(tmp_path) == name
    assert sorted(item.name for item in tmp_path.iterdir()) == ['ui-settings.json']


@pytest.mark.parametrize('name', ['night', 'klein'])
def test_stylesheet_contains_theme_values_and_no_placeholders(name):
    qss = stylesheet(name)
    assert all(THEMES[name][token] in qss for token in ('ground', 'pri', 'on_pri', 'demo'))
    assert '@' not in qss
