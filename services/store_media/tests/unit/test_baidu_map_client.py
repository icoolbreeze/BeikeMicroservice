from app.infrastructure.baidu_map_client import (
    community_query_name,
    normalize_chengdu_district,
    pick_community_poi,
    static_city_map_url,
)


def test_community_query_name_strips_phase_and_zone() -> None:
    assert community_query_name("万科知园A区") == "万科知园"
    assert community_query_name("麓湖生态城二期") == "麓湖生态城"


def test_normalize_chengdu_district() -> None:
    assert normalize_chengdu_district("成华区") == "成华"
    assert normalize_chengdu_district("高新区") == "高新"
    assert normalize_chengdu_district("郫都区") == "郫都区"
    assert normalize_chengdu_district("天府新区") == "天府新区"
    assert normalize_chengdu_district("高新西区") == "高新西"
    assert normalize_chengdu_district("简阳市") == "简阳"
    assert normalize_chengdu_district("大邑县") == "大邑"


def test_pick_community_poi_prefers_name_match_over_first_result() -> None:
    results = [
        {"name": "万科城-臻园", "area": "郫都区", "uid": "1",
         "location": {"lat": 30.8, "lng": 103.9}, "address": "天辰路"},
        {"name": "新希望万科知园", "area": "成华区", "uid": "2",
         "location": {"lat": 30.695, "lng": 104.134}, "address": "仙韵四路"},
        {"name": "万科知园-北门", "area": "成华区", "uid": "3",
         "location": {"lat": 30.69, "lng": 104.13}, "address": "门口"},
    ]
    hit = pick_community_poi("万科知园A区", results)
    assert hit is not None
    assert hit.uid == "2"
    assert hit.district == "成华"
    assert hit.name == "新希望万科知园"


def test_static_city_map_url_is_chengdu_overview() -> None:
    url = static_city_map_url("test-ak", 104.13, 30.69)
    assert "staticimage/v2" in url
    assert "ak=test-ak" in url
    assert "zoom=10" in url
    assert "center=104.07%2C30.67" in url or "center=104.07,30.67" in url
    assert "104.13" in url and "30.69" in url
    assert "0xE74C3C" in url
