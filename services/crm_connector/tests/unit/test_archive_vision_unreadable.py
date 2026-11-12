from dataclasses import asdict
import pytest
from app.application.archive_vision import derive_labels, rollup_listing, ImageRef

@pytest.mark.parametrize("kind,readability,labels",[
    ("户型图","unreadable",[]),
    ("户型图","no_text",[]),
    ("户型图","no_text",["阳台"]),
    ("其他","no_text",[]),
    ("实拍","readable",[]),
])
def test_no_print_or_unreadable_is_miss_with_unknown_features(kind,readability,labels):
    d=derive_labels(kind,readability,labels)
    assert d.status=="miss"
    for field in ["has_balcony","has_garden","has_terrace","has_study","is_shop","is_office","is_residential","is_parking","is_ground_shop"]:
        assert getattr(d,field) is None

def test_photo_slot_keeps_positive_kind_flag_when_miss():
    d=derive_labels("实拍","readable",[])
    r=ImageRef("rent","synthetic","list_floor_plan","fixture://photo")
    assert rollup_listing([(r,d)],crm=None).is_photo_not_plan==1
