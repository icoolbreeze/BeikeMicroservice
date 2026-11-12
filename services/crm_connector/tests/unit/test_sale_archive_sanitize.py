from __future__ import annotations

from app.infrastructure.sale_archive_sanitize import (
    as_archive_dict,
    sanitize_sale_archive_payload,
)


def test_owner_reserve_and_purchase_price_keys_are_dropped() -> None:
    payload = {
        "owner_reserve_price": "120万",
        "ownerReservePrice": "x",
        "purchase_price": "80万",
        "purchasePrice": "y",
        "display_price": "120",
        "nested": {"ownerreserveprice": "z", "keep": 1},
    }
    cleaned = sanitize_sale_archive_payload(payload)
    assert "owner_reserve_price" not in cleaned
    assert "ownerReservePrice" not in cleaned
    assert "purchase_price" not in cleaned
    assert "purchasePrice" not in cleaned
    assert cleaned["display_price"] == "120"
    assert "ownerreserveprice" not in cleaned["nested"]
    assert cleaned["nested"]["keep"] == 1


def test_rental_sensitive_keys_still_dropped_after_sale_pass() -> None:
    payload = {
        "phone": "13800001111",
        "manager_phone": "x",
        "address": "x",
        "owner_reserve_price": "x",
    }
    cleaned = sanitize_sale_archive_payload(payload)
    assert cleaned == {}


def test_mobile_numbers_redacted_from_strings() -> None:
    payload = {
        "creator_name": "张三 13912345678",
        "content": "带看备注 13700001111，业主在家",
        "list": ["电话 13511112222", "无号码文本"],
    }
    cleaned = sanitize_sale_archive_payload(payload)
    assert cleaned["creator_name"] == "张三 "
    assert cleaned["content"] == "带看备注 ，业主在家"
    assert cleaned["list"] == ["电话 ", "无号码文本"]


def test_multiple_mobile_numbers_in_one_string() -> None:
    assert sanitize_sale_archive_payload(
        "13800001111 与 13999998888"
    ) == " 与 "


def test_number_like_strings_that_are_not_mobiles_are_kept() -> None:
    # 11 digits but wrong prefix / digit run.
    assert sanitize_sale_archive_payload("12800001111") == "12800001111"
    assert sanitize_sale_archive_payload("10 13800001111") == "10 "


def test_maintain_rows_with_phone_labels_are_dropped() -> None:
    fields = [
        {"key": "ownerPhone", "name": "业主电话", "value": "13800001111"},
        {"key": "visit_time", "name": "看房时间", "value": "周末"},
    ]
    cleaned = sanitize_sale_archive_payload(fields)
    assert cleaned == [{"key": "visit_time", "name": "看房时间", "value": "周末"}]


def test_comment_phone_is_removed_and_owner_willingness_survives() -> None:
    payload = {
        "agent_comment": {
            "sections": [
                {
                    "title": "核心卖点",
                    "comment_type": "sellPoint",
                    "content": "联系 13800001111 看房",
                },
            ],
        },
        "score": {
            "sections": [
                {"title": "业主意愿", "lines": ["期望出售周期 1个月"]},
            ],
        },
        "maintain_rows": [
            {"key": "willingness", "name": "业主意愿", "value": "急售"},
            {"key": "expect", "name": "业主预期价", "value": "68万"},
        ],
    }
    cleaned = sanitize_sale_archive_payload(payload)
    assert cleaned["agent_comment"]["sections"][0]["content"] == "联系  看房"
    assert cleaned["score"]["sections"][0]["title"] == "业主意愿"
    assert cleaned["score"]["sections"][0]["lines"] == ["期望出售周期 1个月"]
    assert cleaned["maintain_rows"] == [
        {"key": "willingness", "name": "业主意愿", "value": "急售"},
    ]


def test_rows_dropped_on_each_sensitive_label_or_key() -> None:
    rows = [
        {"key": "a", "name": "联系手机", "value": "1"},
        {"key": "b", "name": "证件类型", "value": "1"},
        {"key": "c", "name": "身份证", "value": "1"},
        {"key": "d", "name": "业主预期价", "value": "1"},
        {"key": "e", "name": "原购价格", "value": "1"},
        {"key": "f", "name": "具体楼层", "value": "1"},
        {"key": "g", "name": "详细地址", "value": "1"},
        {"key": "h", "name": "看房备注", "value": "1"},
    ]
    cleaned = sanitize_sale_archive_payload(rows)
    assert cleaned == [{"key": "h", "name": "看房备注", "value": "1"}]


def test_rows_dropped_on_exact_sensitive_key_even_with_plain_name() -> None:
    rows = [
        {"key": "ownerMobile", "name": "字段一", "value": "1"},
        {"key": "mobile", "name": "字段二", "value": "1"},
        {"key": "telephone", "name": "字段三", "value": "1"},
        {"key": "credential", "name": "字段四", "value": "1"},
        {"key": "idCard", "name": "字段五", "value": "1"},
        {"key": "idNo", "name": "字段六", "value": "1"},
        {"key": "normal", "name": "字段七", "value": "1"},
    ]
    cleaned = sanitize_sale_archive_payload(rows)
    assert cleaned == [{"key": "normal", "name": "字段七", "value": "1"}]


def test_coarse_floor_text_is_kept() -> None:
    payload = {
        "floor_desc": "低/6",
        "floor_type": "底层",
        "display_floor": "中楼层(共18层)",
        "head": {"display_floor": "高/32"},
    }
    cleaned = sanitize_sale_archive_payload(payload)
    assert cleaned["floor_desc"] == "低/6"
    assert cleaned["floor_type"] == "底层"
    assert cleaned["display_floor"] == "中楼层(共18层)"
    assert cleaned["head"]["display_floor"] == "高/32"


def test_other_follow_content_survives_redaction() -> None:
    follow = {
        "follow_id": "f1",
        "creator_name": "经纪人13800001111",
        "content": "客户有意向，13611112222 回访",
        "remarks": "保持跟进",
    }
    cleaned = sanitize_sale_archive_payload(follow)
    assert cleaned["follow_id"] == "f1"
    assert cleaned["creator_name"] == "经纪人"
    assert cleaned["content"] == "客户有意向， 回访"
    assert cleaned["remarks"] == "保持跟进"


def test_as_archive_dict_serializes_dataclasses() -> None:
    from app.domain.models import SaleFollowRecord

    record = SaleFollowRecord(
        follow_id="f1",
        content="带看 13800001111",
        creator_name="张三13800001111",
        create_time=None,
        on_top=False,
        remarks=None,
        follow_label=None,
        video_url=None,
    )
    converted = as_archive_dict(record)
    assert converted["follow_id"] == "f1"
    assert converted["creator_name"] == "张三13800001111"  # asdict alone: no redaction
    sanitized = sanitize_sale_archive_payload(converted)
    assert sanitized["creator_name"] == "张三"
    assert sanitized["content"] == "带看 "
