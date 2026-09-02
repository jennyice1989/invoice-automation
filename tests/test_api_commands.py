from __future__ import annotations

from app.main import _api_command_definitions, _parse_price_update_lines


def test_parse_price_update_lines_accepts_script_style_mapping():
    updates, errors = _parse_price_update_lines(
        '''
        PRICE_UPDATES = {
            "11468": 29.99,   # Seaside Aquatics Cooling Fan 2
            "11469": 45.99,
            "11479": 24.99,
        }
        '''
    )

    assert updates == {
        "11468": 29.99,
        "11469": 45.99,
        "11479": 24.99,
    }
    assert errors == []


def test_parse_price_update_lines_accepts_csv_and_colon_lines():
    updates, errors = _parse_price_update_lines(
        """
        11468,29.99
        11469: 45.99
        bad row
        """
    )

    assert updates == {"11468": 29.99, "11469": 45.99}
    assert errors == ["Line 4: expected SKU,price or \"SKU\": price"]


def test_api_command_definitions_include_bulk_price_update():
    command_ids = {command["id"] for command in _api_command_definitions()}

    assert "bulk_price_update" in command_ids
