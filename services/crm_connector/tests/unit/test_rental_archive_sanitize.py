from __future__ import annotations

from app.infrastructure.rental_archive_sanitize import sanitize_archive_payload


def test_strips_quota_sensitive_keys() -> None:
    follows = [
        {
            "content": "客户已看房",
            "follow_type": "普通跟进",
            "creator_name": "李四",
            "on_top": True,
        }
    ]
    payload = sanitize_archive_payload(
        {
            "listing_id": "106126181022",
            "具体楼层": "12层",
            "详细地址": "1栋2单元303",
            "signalFloor": "12",
            "signal_floor": "12",
            "address": "锦江区某路1号",
            "houseAddress": "1栋2单元303",
            "follows": follows,
            "follow_total": 12,
            "follow_last_7d": 3,
            "followNum7Days": 3,
            "owner_lowest_price": 1800,
            "phone": "13800000000",
            "manager_phone": "13800000001",
            "upload_user": "张三",
        }
    )

    # Follow records/counts/authors survive; floor / address / phone /
    # owner price / upload_user do not.
    assert payload == {
        "listing_id": "106126181022",
        "follows": follows,
        "follow_total": 12,
        "follow_last_7d": 3,
        "followNum7Days": 3,
    }


def test_keeps_coarse_floor_and_business_fields() -> None:
    payload = sanitize_archive_payload(
        {
            "listing_id": "106126181022",
            "community": "万象城一期",
            "monthly_rent_yuan": 2600.0,
            "floor_desc": "中楼层",
            "total_floors": 18,
            "floor_type": "中",
            "total_floor": 18,
        }
    )

    assert payload == {
        "listing_id": "106126181022",
        "community": "万象城一期",
        "monthly_rent_yuan": 2600.0,
        "floor_desc": "中楼层",
        "total_floors": 18,
        "floor_type": "中",
        "total_floor": 18,
    }


def test_drops_sensitive_maintain_field_rows() -> None:
    payload = sanitize_archive_payload(
        {
            "maintain": {
                "modules": [
                    {
                        "title": "房源信息",
                        "fields": [
                            {"name": "具体楼层", "display_value": "12层", "complete": True},
                            {"name": "详细地址", "display_value": "1栋2单元303"},
                            {"name": "楼层", "display_value": "中楼层/18层"},
                        ],
                    }
                ]
            }
        }
    )

    fields = payload["maintain"]["modules"][0]["fields"]
    assert [field["name"] for field in fields] == ["楼层"]
    assert "具体楼层" not in str(payload)
    assert "详细地址" not in str(payload)


def test_sanitize_is_recursive_and_preserves_containers() -> None:
    payload = sanitize_archive_payload(
        {
            "items": [
                {"listing_id": "a", "signalFloor": "5"},
                {"listing_id": "b", "详细地址": "x"},
            ],
            "nested": {"deep": {"follow_last_7d": 2, "community": "望江橡树林"}},
            "tuple_field": ({"listing_id": "c", "phone": "1380000"},),
        }
    )

    assert payload["items"] == [{"listing_id": "a"}, {"listing_id": "b"}]
    assert payload["nested"] == {
        "deep": {"follow_last_7d": 2, "community": "望江橡树林"}
    }
    assert payload["tuple_field"] == ({"listing_id": "c"},)


def test_follow_free_text_is_not_redacted() -> None:
    payload = sanitize_archive_payload(
        {
            "follows": [
                {"content": "钥匙在门店，可随时带看", "remarks": "业主底价面谈"},
            ],
        }
    )

    assert payload["follows"] == [
        {"content": "钥匙在门店，可随时带看", "remarks": "业主底价面谈"}
    ]
