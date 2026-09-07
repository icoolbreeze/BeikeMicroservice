from app.infrastructure.district_catalog import (
    STATUS_AMBIGUOUS,
    STATUS_UNIQUE,
    STATUS_UNKNOWN,
    UNKNOWN_DISTRICT,
    classify_bizcircle,
    parse_district_catalog,
)


def test_parse_maps_unique_and_ambiguous_bizcircles() -> None:
    catalog = parse_district_catalog([
        {"key": "fitment", "name": "装修", "children": []},
        {
            "key": "districtId",
            "name": "商圈",
            "children": [
                {"name": "不限", "value": None, "children": []},
                {
                    "name": "成华",
                    "value": "510108",
                    "children": [
                        {"name": "B", "value": None, "children": [
                            {"name": "八里小区", "value": "1100000211", "children": []},
                        ]},
                        {"name": "J", "value": None, "children": [
                            {"name": "建设路", "value": "1100000622", "children": []},
                        ]},
                    ],
                },
                {
                    "name": "双流",
                    "value": "510122",
                    "children": [
                        {"name": "N", "value": None, "children": [
                            {"name": "南湖", "value": "n1", "children": []},
                        ]},
                    ],
                },
                {
                    "name": "天府新区",
                    "value": "23008850",
                    "children": [
                        {"name": "N", "value": None, "children": [
                            {"name": "南湖", "value": "n2", "children": []},
                        ]},
                    ],
                },
            ],
        },
    ])
    assert catalog.districts == ("成华", "双流", "天府新区")
    assert catalog.label("建设路") == "成华"
    assert catalog.label("南湖") == "双流/天府新区"
    assert catalog.districts_for("建设路") == ("成华",)
    assert catalog.matches("建设路", "成华")
    assert not catalog.matches("建设路", "双流")
    assert catalog.matches("南湖", "双流")
    assert catalog.matches("南湖", "天府新区")
    assert catalog.matches("没有这个商圈", UNKNOWN_DISTRICT)
    assert not catalog.matches("建设路", UNKNOWN_DISTRICT)
    assert catalog.matches("建设路", None)
    assert catalog.label("没有这个商圈") is None
    assert classify_bizcircle(catalog, "建设路") == ("成华", ("成华",), STATUS_UNIQUE)
    assert classify_bizcircle(catalog, "南湖")[2] == STATUS_AMBIGUOUS
    assert classify_bizcircle(catalog, None) == (None, (), STATUS_UNKNOWN)
