"""Offline floor-plan label pipeline for Beike crm_connector archives."""

from __future__ import annotations

import argparse
import base64
import email.utils
import hashlib
import json
import logging
import os
import re
import signal
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, fields, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.infrastructure.archive_vision_store import ArchiveVisionStore

logger = logging.getLogger(__name__)

PROMPT_VERSION = "archive-print-labels-flat-v3"
MODEL_ID = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
SCHEMA_VERSION = "1.0"
RULES_VERSION = "2026-11-11-v2"

PROMPT_TEXT = (
    "你是户型图文字提取器。只输出一个JSON对象，恰好包含image_kind、readability、labels三个字段。\n"
    "image_kind只能是\"户型图\"、\"实拍\"、\"其他\"、\"unknown\"。车位平面图属于户型图。readability只能是\"readable\"、\"partial\"、\"unreadable\"、\"no_text\"。\n"
    "labels为原始印字字符串数组，只提取房间、设施、用途、楼层文字。包括阳台(保留A/B等编号及否定词)、花园、院子、露台、书房,、卧室、主卧、次卧、客厅、餐厅、厨房、卫生间、WC、商铺、店铺、门面、营业厅、办公室、办公区、工位、前台、会议室、车位，以及一层、首层、1F、二层、屋面、屋顶、临街等。\n"
    "逐字保留原文。不要改写、归一化或补字；\"无阳台\"不能改成\"阳台\"；\"入户花园\"保留完整短语。房间名编号A/B/C/D要保留。\n"
    "不要输出尺寸/面积数字、指北针、小区名称、水印、广告语。不要从线条、家具、植物和飘窗推测设施，不猜实际用途、产权或楼层。\n"
    "实拍或其他图片的labels一律为[]。未提取到目标印字用[]，不使用false或0表示设施不存在。\n"
    "示例：{\"image_kind\":\"户型图\",\"readability\":\"readable\",\"labels\":[\"小阳台\",\"卧室\",\"厨房\",\"客厅\"]}"
)


@dataclass(frozen=True)
class ImageRef:
    biz: str
    listing_id: str
    source: str
    image_url: str


@dataclass(frozen=True)
class CrmHints:
    layout: str | None = None
    floor_desc: str | None = None
    stat_function: str | None = None
    house_usage: str | None = None
    floor_type: str | None = None
    property_purpose: str | None = None


@dataclass(frozen=True)
class Derived:
    image_kind: str
    readability: str
    labels: list[str]
    status: str
    has_balcony: int | None = None
    has_garden: int | None = None
    has_terrace: int | None = None
    has_study: int | None = None
    is_shop: int | None = None
    is_office: int | None = None
    is_residential: int | None = None
    is_parking: int | None = None
    is_ground_shop: int | None = None
    shop_level: str | None = None
    garden_position: str | None = None
    balcony_count: int | None = None
    balcony_kinds: str | None = None
    garden_kind: str | None = None
    room_counts: dict[str, int | None] | None = None
    rooms_conflict: int | None = None
    use_conflict: int | None = None
    use_kind: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ListingVision:
    biz: str
    listing_id: str
    floor_plan_status: str
    vision_status: str
    has_balcony: int | None = None
    has_garden: int | None = None
    has_terrace: int | None = None
    has_study: int | None = None
    is_shop: int | None = None
    is_office: int | None = None
    is_residential: int | None = None
    is_parking: int | None = None
    is_ground_shop: int | None = None
    is_photo_not_plan: int | None = None
    rooms_conflict: int | None = None
    use_conflict: int | None = None
    use_kind: str | None = None
    balcony_count: int | None = None
    balcony_kinds: str | None = None
    garden_kind: str | None = None
    garden_position: str | None = None
    shop_level: str | None = None
    source_url: str | None = None
    image_set_sha256: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    labeled_at: str | None = None
    schema_version: str | None = None
    rules_version: str | None = None


def normalize_image_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith(("http://", "https://")):
        if url.startswith("/"):
            url = "https://img.ljcdn.com" + url
        else:
            url = "https://img.ljcdn.com/" + url
    if "!" in url:
        url = url.split("!")[0]
    url = re.sub(r'(?:[.!]\d+x(?:\.jpg)?)+$', '', url, flags=re.IGNORECASE)
    if url.endswith("!"):
        url = url[:-1]
    if url.endswith("."):
        url = url[:-1]
    return url


def extract_image_refs(payload: dict, biz: str) -> list[ImageRef]:
    refs = []
    if not isinstance(payload, dict):
        return refs
    listing_id = str(
        payload.get("listing_id")
        or payload.get("listingId")
        or payload.get("del_code")
        or payload.get("delCode")
        or payload.get("house_del_code")
        or payload.get("houseDelCode")
        or payload.get("id")
        or ""
    )

    list_data = payload.get("list") if isinstance(payload.get("list"), dict) else payload
    detail_data = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}

    if biz == "rent":
        fp_url = list_data.get("floor_plan_image_url") or list_data.get("floorPlanImage")
        if fp_url:
            norm = normalize_image_url(str(fp_url))
            if norm:
                refs.append(ImageRef(biz="rent", listing_id=listing_id, source="list_floor_plan", image_url=norm))

        prospect = detail_data.get("prospect") if isinstance(detail_data.get("prospect"), dict) else {}
        p_url = prospect.get("floor_plan_url") or prospect.get("floorPlanUrl")
        if p_url:
            norm = normalize_image_url(str(p_url))
            if norm:
                refs.append(ImageRef(biz="rent", listing_id=listing_id, source="prospect_floor_plan", image_url=norm))

        head = detail_data.get("head") if isinstance(detail_data.get("head"), dict) else {}
        hti = head.get("house_type_images") or head.get("houseTypeImages") or detail_data.get("house_type_images") or []
        if isinstance(hti, (list, tuple)):
            for img in hti:
                img_url = img.get("url") if isinstance(img, dict) else (img if isinstance(img, str) else None)
                if img_url:
                    norm = normalize_image_url(str(img_url))
                    if norm:
                        refs.append(ImageRef(biz="rent", listing_id=listing_id, source="trusteeship_house_type", image_url=norm))
    elif biz == "sale":
        fp_url = list_data.get("floor_plan_image_url") or list_data.get("floorPlanImage")
        if fp_url:
            norm = normalize_image_url(str(fp_url))
            if norm:
                refs.append(ImageRef(biz="sale", listing_id=listing_id, source="sale_list_floor_plan", image_url=norm))

    return refs


def image_set_fingerprint(urls: list[str]) -> str:
    sorted_urls = sorted(set(urls))
    content = "\n".join(sorted_urls).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def derive_labels(image_kind: str, readability: str, labels: list[str]) -> Derived:
    image_kind = image_kind.strip() if image_kind else "unknown"
    readability = readability.strip() if readability else "no_text"
    if not isinstance(labels, list):
        labels = []

    if image_kind in ("实拍", "其他", "unknown") or readability in ("unreadable", "no_text") or image_kind != "户型图":
        return Derived(
            image_kind=image_kind,
            readability=readability,
            labels=labels,
            status="miss",
        )

    if not labels:
        return Derived(image_kind=image_kind, readability=readability, labels=labels, status="miss")

    balcony_pos = {"阳台", "小阳台", "生活阳台", "景观阳台"}
    balcony_suffixes = {"A", "B", "C", "D"}
    
    garden_pos = {"花园", "入户花园", "私家花园", "屋顶花园", "屋面花园", "一层花园", "首层花园", "宅前", "院子", "庭院"}
    terrace_pos = {"露台"}
    terrace_suffixes = {"A", "B", "C", "D"}
    study_pos = {"书房"}
    shop_pos = {"商铺", "店铺", "门面", "营业厅"}
    office_pos = {"办公室", "办公区", "工位", "前台", "会议室"}
    office_suffixes = {"A", "B", "C", "D"}
    
    core_residential_pos = {"卧室", "主卧", "次卧", "客厅", "餐厅", "厨房"}
    auxiliary_residential_pos = {"卫生间", "WC"}
    parking_pos = {"车位"}
    
    ground_level_tokens = {"一层", "首层", "1F"}
    upper_level_tokens = {"二层", "三层", "四层", "五层", "六层", "七层", "八层", "九层", "十层", "2F", "3F", "4F", "5F", "6F", "7F", "8F", "9F"}
    roof_level_tokens = {"屋面", "屋顶", "楼顶"}

    ignored_tokens = {"幸福花园", "门厅", "衣帽间", "多功能间", "飘窗"}

    matched_balconies = set()
    neg_balconies = set()
    
    matched_gardens = set()
    neg_gardens = set()
    
    matched_terraces = set()
    neg_terraces = set()
    
    matched_studies = set()
    neg_studies = set()
    
    matched_shops = set()
    neg_shops = set()
    
    matched_offices = set()
    neg_offices = set()
    
    matched_core_residential = set()
    matched_aux_residential = set()
    neg_residential = set()
    
    matched_parking = set()
    neg_parking = set()
    
    matched_ground = set()
    matched_upper = set()
    matched_roof = set()

    bed_tokens_found = set()
    living_tokens_found = set()
    bath_tokens_found = set()
    kitchen_tokens_found = set()

    has_positive = False
    has_negation = False

    for token in labels:
        if not isinstance(token, str):
            continue
        token = token.strip()
        if not token or token in ignored_tokens or "²" in token or "m" in token.lower():
            continue

        negated = None
        for prefix in ("没有", "无", "非"):
            if token.startswith(prefix) and len(token) > len(prefix):
                negated = token[len(prefix):].strip()
                break

        target = negated if negated is not None else token
        is_pos = False

        b_matched = False
        for bp in balcony_pos:
            if target == bp:
                b_matched = True
                break
            for suf in balcony_suffixes:
                if target == f"{bp}{suf}":
                    b_matched = True
                    break
            if b_matched:
                break
        if b_matched:
            if negated is not None:
                neg_balconies.add(target)
                has_negation = True
            else:
                matched_balconies.add(target)
                is_pos = True

        g_matched = any(target == gp for gp in garden_pos)
        if g_matched:
            if negated is not None:
                neg_gardens.add(target)
                has_negation = True
            else:
                matched_gardens.add(target)
                is_pos = True

        t_matched = False
        for tp in terrace_pos:
            if target == tp:
                t_matched = True
                break
            for suf in terrace_suffixes:
                if target == f"{tp}{suf}":
                    t_matched = True
                    break
            if t_matched:
                break
        if t_matched:
            if negated is not None:
                neg_terraces.add(target)
                has_negation = True
            else:
                matched_terraces.add(target)
                is_pos = True

        if target == "书房":
            if negated is not None:
                neg_studies.add(target)
                has_negation = True
            else:
                matched_studies.add(target)
                is_pos = True

        if target in shop_pos:
            if negated is not None:
                neg_shops.add(target)
                has_negation = True
            else:
                matched_shops.add(target)
                is_pos = True

        o_matched = False
        for op in office_pos:
            if target == op:
                o_matched = True
                break
            for suf in office_suffixes:
                if target == f"{op}{suf}":
                    o_matched = True
                    break
            if o_matched:
                break
        if o_matched:
            if negated is not None:
                neg_offices.add(target)
                has_negation = True
            else:
                matched_offices.add(target)
                is_pos = True

        r_matched = False
        for rp in core_residential_pos:
            if target == rp:
                r_matched = True
                break
            if target.startswith(rp) and len(target) > len(rp):
                suf = target[len(rp):]
                if suf.isalnum():
                    r_matched = True
                    break
            if r_matched:
                break
        if r_matched:
            if negated is not None:
                neg_residential.add(target)
                has_negation = True
            else:
                matched_core_residential.add(target)
                is_pos = True
                if any(target.startswith(b) for b in ("卧室", "主卧", "次卧")):
                    bed_tokens_found.add(target)
                elif target.startswith("客厅") or target.startswith("餐厅"):
                    living_tokens_found.add(target)
                elif target.startswith("厨房"):
                    kitchen_tokens_found.add(target)

        aux_matched = False
        for ap in auxiliary_residential_pos:
            if target == ap or target.startswith(ap):
                aux_matched = True
                break
        if aux_matched:
            if negated is not None:
                has_negation = True
            else:
                matched_aux_residential.add(target)
                is_pos = True
                if target.startswith("卫生间") or target.startswith("WC"):
                    bath_tokens_found.add(target)

        if target == "车位":
            if negated is not None:
                neg_parking.add(target)
                has_negation = True
            else:
                matched_parking.add(target)
                is_pos = True

        if target in ground_level_tokens:
            matched_ground.add(target)
            is_pos = True
        if target in upper_level_tokens:
            matched_upper.add(target)
            is_pos = True
        if target in roof_level_tokens:
            matched_roof.add(target)
            is_pos = True

        if is_pos:
            has_positive = True

    if not has_positive and not has_negation:
        return Derived(image_kind=image_kind, readability=readability, labels=labels, status="miss")

    has_balcony = 1 if (matched_balconies and not neg_balconies) else (0 if (neg_balconies and not matched_balconies) else None)
    has_garden = 1 if (matched_gardens and not neg_gardens) else (0 if (neg_gardens and not matched_gardens) else None)
    has_terrace = 1 if (matched_terraces and not neg_terraces) else (0 if (neg_terraces and not matched_terraces) else None)
    has_study = 1 if (matched_studies and not neg_studies) else (0 if (neg_studies and not matched_studies) else None)
    is_shop = 1 if (matched_shops and not neg_shops) else (0 if (neg_shops and not matched_shops) else None)
    is_office = 1 if (matched_offices and not neg_offices) else (0 if (neg_offices and not matched_offices) else None)
    is_residential = 1 if (matched_core_residential and not neg_residential) else (0 if (neg_residential and not matched_core_residential) else None)
    is_parking = 1 if (matched_parking and not neg_parking) else (0 if (neg_parking and not matched_parking) else None)

    shop_level = None
    if matched_ground and not matched_upper:
        shop_level = "ground"
    elif matched_upper and not matched_ground:
        shop_level = "upper"
    elif matched_ground and matched_upper:
        shop_level = None  # conflict

    is_ground_shop = 1 if (is_shop == 1 and shop_level == "ground") else None

    garden_positions_matched = set()
    if matched_gardens:
        if any("入户花园" in g for g in matched_gardens):
            garden_positions_matched.add("entry")
        if any("私家花园" in g or g in ("院子", "庭院", "一层花园", "首层花园", "宅前") for g in matched_gardens):
            garden_positions_matched.add("ground_yard")
        if any("屋顶花园" in g or "屋面花园" in g for g in matched_gardens):
            garden_positions_matched.add("roof")

    garden_position = None
    if len(garden_positions_matched) == 1:
        garden_position = list(garden_positions_matched)[0]
    elif len(garden_positions_matched) > 1:
        garden_position = None  # conflict

    garden_kind = None
    if garden_position == "entry":
        garden_kind = "入户花园"
    elif garden_position == "ground_yard":
        garden_kind = next((g for g in matched_gardens if g in ("院子", "庭院", "私家花园", "宅前", "一层花园", "首层花园")), "花园")
    elif garden_position == "roof":
        garden_kind = next((g for g in matched_gardens if "屋顶" in g or "屋面" in g), "屋顶花园")
    elif matched_gardens and garden_position is None and len(garden_positions_matched) == 0:
        garden_kind = list(matched_gardens)[0]

    balcony_count = len(matched_balconies) if matched_balconies else None
    balcony_kinds = json.dumps(sorted(list(matched_balconies)), ensure_ascii=False) if matched_balconies else None

    use_kind = None
    if is_parking == 1 and is_shop != 1 and is_residential != 1 and is_office != 1:
        use_kind = "车位"
    elif is_shop == 1 and bed_tokens_found:
        use_kind = "商住"
    elif is_shop == 1 and is_residential != 1 and is_parking != 1:
        use_kind = "商铺"
    elif is_office == 1 and is_shop != 1 and is_residential != 1 and is_parking != 1:
        use_kind = "写字楼"
    elif is_residential == 1 and is_shop != 1 and is_office != 1 and is_parking != 1:
        use_kind = "住宅"

    room_counts = {
        "bedroom": len(bed_tokens_found) if bed_tokens_found else None,
        "living": len(living_tokens_found) if living_tokens_found else None,
        "bathroom": len(bath_tokens_found) if bath_tokens_found else None,
        "kitchen": len(kitchen_tokens_found) if kitchen_tokens_found else None,
    }
    if all(v is None for v in room_counts.values()):
        room_counts = None

    return Derived(
        image_kind=image_kind,
        readability=readability,
        labels=labels,
        status="ok",
        has_balcony=has_balcony,
        has_garden=has_garden,
        has_terrace=has_terrace,
        has_study=has_study,
        is_shop=is_shop,
        is_office=is_office,
        is_residential=is_residential,
        is_parking=is_parking,
        is_ground_shop=is_ground_shop,
        shop_level=shop_level,
        garden_position=garden_position,
        balcony_count=balcony_count,
        balcony_kinds=balcony_kinds,
        garden_kind=garden_kind,
        room_counts=room_counts,
        use_kind=use_kind,
    )


def parse_crm_layout(layout_str: str) -> dict[str, int | None]:
    if not layout_str:
        return {}
    layout_str = layout_str.strip()
    res: dict[str, int | None] = {}
    m_chi = re.match(r'^(\d+)室(?:(\d+)厅)?(?:(\d+)厨)?(?:(\d+)卫)?$', layout_str)
    if m_chi:
        res["bedroom"] = int(m_chi.group(1)) if m_chi.group(1) is not None else None
        res["living"] = int(m_chi.group(2)) if m_chi.group(2) is not None else None
        res["kitchen"] = int(m_chi.group(3)) if m_chi.group(3) is not None else None
        res["bathroom"] = int(m_chi.group(4)) if m_chi.group(4) is not None else None
        return res
    parts = layout_str.split('-')
    if len(parts) >= 3 and all(p.strip().isdigit() for p in parts if p.strip()):
        digits = [int(p.strip()) for p in parts]
        res["bedroom"] = digits[0]
        res["living"] = digits[1]
        if len(digits) == 4:
            res["kitchen"] = digits[2]
            res["bathroom"] = digits[3]
        elif len(digits) == 3:
            res["kitchen"] = None
            res["bathroom"] = digits[2]
        return res
    return {}


def _parse_retry_after(headers: Any, default_sleep: float) -> float:
    if not headers:
        return default_sleep
    retry_after = None
    if hasattr(headers, "get"):
        retry_after = headers.get("Retry-After")
    elif isinstance(headers, dict):
        retry_after = headers.get("Retry-After")
    if not retry_after:
        return default_sleep
    sleep_time = default_sleep
    try:
        sleep_time = float(retry_after)
    except Exception:
        try:
            dt = email.utils.parsedate_to_datetime(retry_after)
            if dt:
                diff = (dt - datetime.now(dt.tzinfo or UTC)).total_seconds()
                if diff > 0:
                    sleep_time = diff
        except Exception:
            pass
    if sleep_time < 0:
        sleep_time = 0.0
    if sleep_time > 120.0:
        sleep_time = 120.0
    return sleep_time


def rollup_listing(refs_and_derived: list, *, crm: CrmHints, observations: list[dict | None] | None = None) -> ListingVision:
    model_id = MODEL_ID
    prompt_version = PROMPT_VERSION
    schema_version = SCHEMA_VERSION
    rules_version = RULES_VERSION

    if observations is not None:
        prov_tuples = set()
        for o in observations:
            if o:
                prov_tuples.add((o.get("model_id"), o.get("prompt_version"), o.get("schema_version"), o.get("rules_version")))
            else:
                prov_tuples.add((None, None, None, None))
        if len(prov_tuples) == 1:
            t_mi, t_pv, t_sv, t_rv = list(prov_tuples)[0]
            model_id = t_mi
            prompt_version = t_pv
            schema_version = t_sv
            rules_version = t_rv
        else:
            model_id = None
            prompt_version = None
            schema_version = None
            rules_version = None

    if not refs_and_derived:
        return ListingVision(
            biz="",
            listing_id="",
            floor_plan_status="absent",
            vision_status="none",
            model_id=model_id,
            prompt_version=prompt_version,
            schema_version=schema_version or SCHEMA_VERSION,
            rules_version=rules_version or RULES_VERSION,
        )

    biz = refs_and_derived[0][0].biz if isinstance(refs_and_derived[0], (tuple, list)) else refs_and_derived[0].get("biz", "")
    listing_id = refs_and_derived[0][0].listing_id if isinstance(refs_and_derived[0], (tuple, list)) else refs_and_derived[0].get("listing_id", "")

    all_errored = True
    has_ok = False
    has_miss = False
    ok_derived = []
    has_err = any(
        (d.status == "error") for item in refs_and_derived for _, d in [item if isinstance(item, (tuple, list)) else (item["ref"], item["derived"])]
    )

    is_photo_not_plan = None
    any_parking = False

    for item in refs_and_derived:
        ref, derived = item if isinstance(item, (tuple, list)) else (item["ref"], item["derived"])
        if derived.status == "error":
            continue
        all_errored = False
        if derived.status == "ok":
            has_ok = True
            ok_derived.append(derived)
        elif derived.status == "miss":
            has_miss = True

        if ref.source in ("list_floor_plan", "prospect_floor_plan", "trusteeship_house_type", "sale_list_floor_plan"):
            if derived.image_kind == "实拍":
                is_photo_not_plan = 1
        if derived.is_parking == 1:
            any_parking = True

    if all_errored and refs_and_derived:
        return ListingVision(
            biz=biz,
            listing_id=listing_id,
            floor_plan_status="error",
            vision_status="partial",
            is_photo_not_plan=is_photo_not_plan,
            model_id=model_id,
            prompt_version=prompt_version,
            schema_version=schema_version or SCHEMA_VERSION,
            rules_version=rules_version or RULES_VERSION,
        )

    if not has_ok and has_miss:
        return ListingVision(
            biz=biz,
            listing_id=listing_id,
            floor_plan_status="miss",
            vision_status="partial",
            is_photo_not_plan=is_photo_not_plan,
            model_id=model_id,
            prompt_version=prompt_version,
            schema_version=schema_version or SCHEMA_VERSION,
            rules_version=rules_version or RULES_VERSION,
        )

    if not has_ok and not has_miss:
        return ListingVision(
            biz=biz,
            listing_id=listing_id,
            floor_plan_status="absent",
            vision_status="none",
            model_id=model_id,
            prompt_version=prompt_version,
            schema_version=schema_version or SCHEMA_VERSION,
            rules_version=rules_version or RULES_VERSION,
        )

    has_conflict = False

    def rollup_tri_state(attr: str) -> int | None:
        nonlocal has_conflict
        vals = [getattr(d, attr) for d in ok_derived]
        has_1 = any(v == 1 for v in vals)
        has_0 = any(v == 0 for v in vals)
        has_none = any(v is None for v in vals)

        if has_1 and has_0:
            has_conflict = True
            return None
        if has_1:
            return 1
        if has_0 and has_err:
            return None
        if has_none:
            return None
        if has_0:
            return 0
        return None

    has_balcony = rollup_tri_state("has_balcony")
    has_garden = rollup_tri_state("has_garden")
    has_terrace = rollup_tri_state("has_terrace")
    has_study = rollup_tri_state("has_study")
    is_shop = rollup_tri_state("is_shop")
    is_office = rollup_tri_state("is_office")
    is_residential = rollup_tri_state("is_residential")
    is_parking = 1 if any_parking or rollup_tri_state("is_parking") == 1 else rollup_tri_state("is_parking")
    is_ground_shop = rollup_tri_state("is_ground_shop")

    if any_parking:
        is_parking = 1

    use_kinds = {d.use_kind for d in ok_derived if d.use_kind}
    use_conflict = None
    use_kind = None
    floor_plan_status = "ok"

    if len(use_kinds) > 1 or has_conflict:
        use_conflict = 1 if len(use_kinds) > 1 else None
        use_kind = None
        floor_plan_status = "conflict"
    elif len(use_kinds) == 1:
        use_kind = list(use_kinds)[0]

    # CRM use comparison
    if crm and use_kind:
        use_texts = [crm.property_purpose or "", crm.stat_function or "", crm.house_usage or ""]
        for ut in use_texts:
            ut = ut.strip()
            if not ut or ut.isdigit():
                continue
            chinese_words = {"住宅", "商铺", "写字楼", "办公", "车位"}
            present_words = {w for w in chinese_words if w in ut}
            if present_words:
                if "住宅" in present_words and use_kind != "住宅" and use_kind != "商住":
                    use_conflict = 1
                elif "商铺" in present_words and use_kind != "商铺" and use_kind != "商住":
                    use_conflict = 1
                elif ("写字楼" in present_words or "办公" in present_words) and use_kind != "写字楼":
                    use_conflict = 1
                elif "车位" in present_words and use_kind != "车位":
                    use_conflict = 1

    if use_conflict == 1:
        use_kind = None
        is_ground_shop = None
        floor_plan_status = "conflict"

    # Rooms conflict
    rooms_conflict = None
    if crm and crm.layout:
        crm_rooms = parse_crm_layout(crm.layout)
        if crm_rooms:
            vis_bed = max([d.room_counts.get("bedroom") or 0 for d in ok_derived if d.room_counts and d.room_counts.get("bedroom")], default=0) if ok_derived else 0
            vis_liv = max([d.room_counts.get("living") or 0 for d in ok_derived if d.room_counts and d.room_counts.get("living")], default=0) if ok_derived else 0
            vis_kit = max([d.room_counts.get("kitchen") or 0 for d in ok_derived if d.room_counts and d.room_counts.get("kitchen")], default=0) if ok_derived else 0
            vis_bath = max([d.room_counts.get("bathroom") or 0 for d in ok_derived if d.room_counts and d.room_counts.get("bathroom")], default=0) if ok_derived else 0

            c_bed = crm_rooms.get("bedroom")
            c_liv = crm_rooms.get("living")
            c_kit = crm_rooms.get("kitchen")
            c_bath = crm_rooms.get("bathroom")

            conflict = False
            if c_bed is not None and vis_bed > 0 and c_bed != vis_bed:
                conflict = True
            if c_liv is not None and vis_liv > 0 and c_liv != vis_liv:
                conflict = True
            if c_bath is not None and vis_bath > 0 and c_bath != vis_bath:
                conflict = True
            if c_kit is not None and vis_kit is not None and vis_kit > 0 and c_kit != vis_kit:
                conflict = True
            if conflict:
                rooms_conflict = 1
                floor_plan_status = "conflict"

    balcony_counts = [d.balcony_count for d in ok_derived if d.balcony_count is not None]
    balcony_count = max(balcony_counts) if balcony_counts else None

    all_balcony_tokens = set()
    for d in ok_derived:
        if d.balcony_kinds:
            try:
                bk = json.loads(d.balcony_kinds)
                if isinstance(bk, list):
                    all_balcony_tokens.update(bk)
            except Exception:
                pass
    balcony_kinds = json.dumps(sorted(list(all_balcony_tokens)), ensure_ascii=False) if all_balcony_tokens else None

    garden_kinds = {d.garden_kind for d in ok_derived if d.garden_kind}
    garden_kind = list(garden_kinds)[0] if len(garden_kinds) == 1 else None

    garden_pos_set = {d.garden_position for d in ok_derived if d.garden_position}
    garden_position = list(garden_pos_set)[0] if len(garden_pos_set) == 1 else None

    shop_levels = [d.shop_level for d in ok_derived if d.shop_level is not None]
    shop_level = shop_levels[0] if shop_levels else None

    source_url = refs_and_derived[0][0].image_url if refs_and_derived else None
    urls = [r.image_url for r, _ in refs_and_derived] if isinstance(refs_and_derived[0], (tuple, list)) else [item["ref"].image_url for item in refs_and_derived]
    image_set_sha256 = image_set_fingerprint(urls)

    vision_status = "ready" if floor_plan_status in ("ok", "conflict") else "partial"
    if vision_status == "conflict":
        vision_status = "ready"

    return ListingVision(
        biz=biz,
        listing_id=listing_id,
        floor_plan_status=floor_plan_status,
        vision_status=vision_status,
        has_balcony=has_balcony,
        has_garden=has_garden,
        has_terrace=has_terrace,
        has_study=has_study,
        is_shop=is_shop,
        is_office=is_office,
        is_residential=is_residential,
        is_parking=is_parking,
        is_ground_shop=is_ground_shop,
        is_photo_not_plan=is_photo_not_plan,
        rooms_conflict=rooms_conflict,
        use_conflict=use_conflict,
        use_kind=use_kind,
        balcony_count=balcony_count,
        balcony_kinds=balcony_kinds,
        garden_kind=garden_kind,
        garden_position=garden_position,
        shop_level=shop_level,
        source_url=source_url,
        image_set_sha256=image_set_sha256,
        model_id=model_id,
        prompt_version=prompt_version,
        labeled_at=datetime.now(UTC).isoformat(),
        schema_version=schema_version or SCHEMA_VERSION,
        rules_version=rules_version or RULES_VERSION,
    )


def default_download_image(url: str) -> bytes | None:
    if not url:
        return None
    norm = normalize_image_url(url)
    req_url = norm.rstrip('/') + '.800x.jpg'
    try:
        req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            content = resp.read(5 * 1024 * 1024 + 1)
            if len(content) > 5 * 1024 * 1024:
                return None
            if not content.startswith(b"\xff\xd8"):
                return None
            return content
    except Exception:
        return None


_MODEL_SEMAPHORE = threading.Semaphore(3)
_MODEL_RATE_LOCK = threading.Lock()
_MODEL_LAST_REQUEST = 0.0


def default_call_model(img_bytes: bytes, *, stop_event=None, min_request_interval=0.) -> tuple[str, str, list[str], dict] | None:
    global _MODEL_LAST_REQUEST
    def pause(seconds):
        if stop_event is not None:
            return stop_event.wait(seconds)
        time.sleep(seconds)
        return False
    api_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NIM_API_KEY")
    if not api_key:
        return None
    api_base = os.environ.get("NVIDIA_API_BASE", "https://integrate.api.nvidia.com/v1/chat/completions")

    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": PROMPT_TEXT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64.b64encode(img_bytes).decode('utf-8')}"
                        }
                    }
                ]
            }
        ],
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 1024,
        "temperature": 0.0,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_base,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "BeikeMicroservice-ArchiveVision/1.0",
        },
        method="POST",
    )

    with _MODEL_SEMAPHORE:
        for attempt in range(4):
            if stop_event is not None and stop_event.is_set():
                return None
            if min_request_interval > 0:
                with _MODEL_RATE_LOCK:
                    wait = max(0., _MODEL_LAST_REQUEST + min_request_interval - time.monotonic())
                    if wait and pause(wait):
                        return None
                    _MODEL_LAST_REQUEST = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=22.0) as resp:
                    if resp.status == 429:
                        sleep_time = _parse_retry_after(resp.headers, 2.0 ** attempt)
                        if pause(sleep_time):
                            return None
                        continue
                    body = resp.read().decode("utf-8")
                    res_json = json.loads(body)
                    choices = res_json.get("choices")
                    if not choices:
                        return None
                    choice = choices[0]
                    if choice.get("finish_reason") != "stop":
                        return None
                    message = choice.get("message", {})
                    content = message.get("content")
                    if not content:
                        return None
                    parsed = json.loads(content)
                    if not isinstance(parsed, dict):
                        return None
                    if set(parsed.keys()) != {"image_kind", "readability", "labels"}:
                        return None
                    kind = parsed.get("image_kind")
                    readability = parsed.get("readability")
                    labels = parsed.get("labels")
                    if kind not in ("户型图", "实拍", "其他", "unknown"):
                        return None
                    if readability not in ("readable", "partial", "unreadable", "no_text"):
                        return None
                    if not isinstance(labels, list) or not all(isinstance(l, str) for l in labels):
                        return None
                    return str(kind), str(readability), list(labels), parsed
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    sleep_time = _parse_retry_after(e.headers, 2.0 ** attempt)
                    if pause(sleep_time):
                        return None
                    continue
                return None
            except Exception:
                if attempt < 3:
                    if pause(2.0 ** attempt):
                        return None
                    continue
                return None
    return None


def _preflight_source_db(db_path: str | None) -> None:
    if not db_path:
        return
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"Archive database not found: {db_path}")
    try:
        resolved = p.resolve().as_posix()
        uri = f"file:{resolved}?mode=ro&query_only=true"
        with sqlite3.connect(uri, uri=True) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='listing_current'")
            if not cursor.fetchone():
                raise sqlite3.DatabaseError(f"Table listing_current missing in source DB: {db_path}")
    except Exception as e:
        if isinstance(e, (FileNotFoundError, sqlite3.DatabaseError)):
            raise
        raise sqlite3.DatabaseError(f"Failed to read source DB {db_path}: {e}") from e


class ArchiveVisionLock:
    """Nonblocking OS lock; never remove the inode while another process waits."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fd = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b'0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.release()
            return False

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _source_rows(path, after='', upper=None, size=500, listing_id=None):
    """Close the read connection before any network call or vision write."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(listing_current)')}
        active = 'is_active' if 'is_active' in columns else '1 AS is_active'
        if listing_id is not None:
            sql = f'SELECT listing_id,payload_json,{active} FROM listing_current WHERE listing_id=?'
            return [dict(r) for r in conn.execute(sql, (listing_id,))]
        if upper is None:
            return conn.execute('SELECT MAX(listing_id) FROM listing_current').fetchone()[0] or ''
        sql = f'SELECT listing_id,payload_json,{active} FROM listing_current WHERE listing_id>? AND listing_id<=? ORDER BY listing_id LIMIT ?'
        return [dict(r) for r in conn.execute(sql, (after, upper, size))]
    finally:
        conn.close()


def _payload(row):
    try:
        value = json.loads(row['payload_json'])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _current_observation(obs):
    return bool(obs and obs.get('model_id') == MODEL_ID
                and obs.get('prompt_version') == PROMPT_VERSION
                and obs.get('schema_version') in (None, SCHEMA_VERSION))


def _publish_listing(store, biz, listing_id, payload, force_stale=False):
    refs = extract_image_refs(payload, biz)
    store.update_image_links(biz, listing_id, [(r.image_url, r.source) for r in refs])
    observations, pairs = [], []
    unsettled = force_stale
    for ref in refs:
        obs = store.get_observation(ref.image_url)
        observations.append(obs)
        if not _current_observation(obs) or obs.get('status') not in ('ok', 'miss', 'error'):
            unsettled = True
        if not obs or obs.get('status') == 'error':
            derived = Derived('unknown', 'no_text', [], 'error')
        else:
            labels = json.loads(obs.get('labels_json') or '[]')
            derived = derive_labels(obs.get('image_kind') or 'unknown', obs.get('readability') or 'no_text', labels)
        pairs.append((ref, derived))
    result = asdict(rollup_listing(pairs, crm=extract_crm_hints(payload, biz), observations=observations))
    result.pop('biz', None)
    result.pop('listing_id', None)
    result['image_set_sha256'] = image_set_fingerprint([r.image_url for r in refs])
    if unsettled:
        result['vision_status'] = 'stale'
    store.upsert_listing_vision(biz, listing_id, result)


def _inactive(store, biz, listing_id):
    with store._connect() as conn:
        conn.execute("UPDATE listing_vision SET vision_status='inactive' WHERE biz=? AND listing_id=?", (biz, listing_id))


def _reconcile_page(store, biz, path, size=64):
    """Check old vision IDs explicitly; a missing scan-page ID proves nothing."""
    after = store.get_meta('reconcile:' + biz) or ''
    with store._connect() as conn:
        ids = [r[0] for r in conn.execute('SELECT listing_id FROM listing_vision WHERE biz=? AND listing_id>? ORDER BY listing_id LIMIT ?', (biz, after, size))]
    for listing_id in ids:
        current = _source_rows(path, listing_id=listing_id)
        if not current or not current[0]['is_active']:
            _inactive(store, biz, listing_id)
        else:
            refs = extract_image_refs(_payload(current[0]), biz)
            store.update_image_links(biz, listing_id, [(r.image_url, r.source) for r in refs])
    store.set_meta('reconcile:' + biz, ids[-1] if len(ids) == size else '')


def _process_image(store, ref, metrics, downloader, model_caller, offline=False):
    obs = store.get_observation(ref.image_url)
    if _current_observation(obs) and obs.get('status') in ('ok', 'miss'):
        metrics['cache_hit'] += 1
        return True
    if offline:
        return True
    if _current_observation(obs) and obs.get('status') == 'error':
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(obs['labeled_at'])).total_seconds()
            if age < 900:
                metrics['cooldown'] += 1
                return True
        except (ValueError, TypeError, KeyError):
            pass
    if metrics['budget'] is not None and metrics['attempted'] >= metrics['budget']:
        metrics['pending'] += 1
        return False
    metrics['attempted'] += 1
    values = dict(model_id=MODEL_ID, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                  rules_version=RULES_VERSION, labeled_at=datetime.now(UTC).isoformat(), error=None)
    try:
        blob = downloader(ref.image_url)
        if not blob or not blob.startswith(b'\xff\xd8'):
            raise ValueError('download_failed_or_not_jpeg')
        values['image_sha256'] = hashlib.sha256(blob).hexdigest()
        with store._connect() as conn:
            cached = conn.execute("SELECT * FROM image_observation WHERE image_sha256=? AND model_id=? AND prompt_version=? AND (schema_version=? OR schema_version IS NULL) AND status IN ('ok','miss') LIMIT 1",
                                  (values['image_sha256'], MODEL_ID, PROMPT_VERSION, SCHEMA_VERSION)).fetchone()
        if cached:
            kind, readability, labels = cached['image_kind'], cached['readability'], json.loads(cached['labels_json'] or '[]')
            metrics['cache_hit'] += 1
        else:
            response = model_caller(blob)
            if response is None:
                raise ValueError('model_call_failed')
            kind, readability, labels, _ = response
        derived = derive_labels(kind, readability, labels)
        values.update(image_kind=kind, readability=readability, labels_json=json.dumps(labels, ensure_ascii=False),
                      derived_json=json.dumps(asdict(derived), ensure_ascii=False), status=derived.status)
        metrics['success' if derived.status == 'ok' else 'miss'] += 1
    except Exception:
        # Exceptions may contain keys, full response bodies or URLs. Persist only a fixed code.
        values.update(status='error', error='image_processing_failed', image_kind=None, readability=None,
                      labels_json=None, derived_json=None)
        metrics['error'] += 1
        metrics['has_failure'] = True
    store.upsert_observation(ref.image_url, **values)
    return True


def _new_metrics(budget=None):
    return dict(success=0, miss=0, error=0, cache_hit=0, pending=0, cooldown=0,
                attempted=0, scanned=0, has_failure=False, budget=budget, pass_complete=False)


def _batch(rental_db, sale_db, vision_db, budget, scan_size, downloader, model_caller, stop_event=None, offline=False):
    store = ArchiveVisionStore(vision_db)
    metrics = _new_metrics(budget)
    sources = [(b, p) for b, p in [('rent', rental_db), ('sale', sale_db)] if p]
    state_key = 'scan-v2:' + hashlib.sha256(json.dumps(sources).encode()).hexdigest()[:16]
    raw = store.get_meta(state_key)
    state = json.loads(raw) if raw else None
    if not state or all(s['done'] for s in state['sources'].values()):
        state = {'next': 0, 'sources': {b: {'after': '', 'upper': _source_rows(p), 'done': False} for b, p in sources}}
    pages = {}
    for biz, path in sources:
        _reconcile_page(store, biz, path)
        item = state['sources'][biz]
        if item['done']:
            continue
        page = _source_rows(path, item['after'], item['upper'], scan_size)
        pages[biz] = iter(page)
        if not page:
            item['done'] = True
    while metrics['scanned'] < scan_size:
        if stop_event and stop_event.is_set():
            break
        available = [i for i, (b, _) in enumerate(sources) if b in pages]
        if not available:
            break
        index = next((i for i in available if i >= state['next']), available[0])
        biz, path = sources[index]
        state['next'] = (index + 1) % len(sources)
        item = state['sources'][biz]
        row = next(pages[biz], None)
        if row is None:
            pages.pop(biz)
            # A short page is exhausted; its last ID is our current cursor.
            if item['after'] >= item['upper']:
                item['done'] = True
            continue
        listing_id = row['listing_id']
        if not row['is_active']:
            _inactive(store, biz, listing_id)
            item['after'] = listing_id
            metrics['scanned'] += 1
            continue
        payload = _payload(row)
        refs = extract_image_refs(payload, biz)
        store.update_image_links(biz, listing_id, [(r.image_url, r.source) for r in refs])
        complete = True
        for ref in refs:
            if stop_event and stop_event.is_set():
                complete = False
                break
            if not _process_image(store, ref, metrics, downloader, model_caller, offline):
                complete = False
        # Never publish the pre-request source snapshot after a long inference.
        fresh = _source_rows(path, listing_id=listing_id)
        if not fresh or not fresh[0]['is_active']:
            _inactive(store, biz, listing_id)
        else:
            fresh_payload = _payload(fresh[0])
            fresh_refs = extract_image_refs(fresh_payload, biz)
            changed = {r.image_url for r in refs} != {r.image_url for r in fresh_refs}
            _publish_listing(store, biz, listing_id, fresh_payload, force_stale=changed or not complete)
            if changed:
                complete = False
        metrics['scanned'] += 1
        if complete:
            item['after'] = listing_id
            if listing_id >= item['upper']:
                item['done'] = True
        store.set_meta(state_key, json.dumps(state))
        if not complete or (budget is not None and metrics['attempted'] >= budget and not offline):
            if metrics['pending'] == 0 and not all(s['done'] for s in state['sources'].values()):
                metrics['pending'] = 1
            break
    metrics['pass_complete'] = all(s['done'] for s in state['sources'].values())
    store.set_meta(state_key, json.dumps(state))
    metrics.pop('budget')
    return metrics


def _validate(rental_db, sale_db, vision_db, model_caller, offline=False):
    if not rental_db and not sale_db:
        raise ValueError('At least one archive database is required')
    _preflight_source_db(rental_db)
    _preflight_source_db(sale_db)
    if any(p and Path(p).resolve() == Path(vision_db).resolve() for p in (rental_db, sale_db)):
        raise ValueError('Vision database must differ from archive databases')
    if not offline and (model_caller is None or model_caller is default_call_model):
        if not (os.environ.get('NVIDIA_API_KEY') or os.environ.get('NIM_API_KEY')):
            raise ValueError('API key required (NVIDIA_API_KEY or NIM_API_KEY)')


class ArchiveVisionRunner:
    """Serial, bounded scanner. Lock spans the complete watch lifetime."""

    def __init__(self, rental_db, sale_db, vision_db, batch_size=20, poll_interval=60.,
                 downloader=None, model_caller=None, scan_size=500, min_free_gib=2., limit=None):
        if batch_size < 1 or scan_size < 1 or scan_size > 1000 or poll_interval <= 0 or min_free_gib < 0 or (limit is not None and limit < 0):
            raise ValueError('Invalid worker limits')
        self.rental_db, self.sale_db, self.vision_db = rental_db, sale_db, vision_db
        self.batch_size, self.poll_interval, self.scan_size = batch_size, poll_interval, scan_size
        self.min_free_gib, self.limit = min_free_gib, limit
        self.downloader, self.model_caller = downloader, model_caller
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = None
        self.last_metrics = None

    def _acquire(self):
        _validate(self.rental_db, self.sale_db, self.vision_db, self.model_caller)
        lock = ArchiveVisionLock(Path(str(self.vision_db) + '.lock'))
        if not lock.acquire():
            raise RuntimeError('Another archive vision process is already running')
        self._lock = lock

    def _storage_ok(self):
        parent = Path(self.vision_db).resolve().parent
        while not parent.exists():
            parent = parent.parent
        return shutil.disk_usage(parent).free >= self.min_free_gib * 1024**3

    def _run(self, budget):
        if not self._storage_ok():
            metrics = _new_metrics()
            metrics.pop('budget')
            metrics['status'] = 'paused_storage'
        else:
            caller = self.model_caller or (lambda blob: default_call_model(blob, stop_event=self._stop_event, min_request_interval=4.))
            metrics = _batch(self.rental_db, self.sale_db, self.vision_db, budget, self.scan_size,
                             self.downloader or default_download_image, caller,
                             self._stop_event)
            metrics['status'] = 'ok'
        self.last_metrics = metrics
        logger.info('archive_vision %s', json.dumps(metrics))
        return metrics

    def run_once(self):
        self._acquire()
        try:
            return self._run(min(self.batch_size, self.limit) if self.limit is not None else self.batch_size)
        finally:
            self._lock.release()
            self._lock = None

    def run(self, watch=False):
        if self._lock is None:
            self._acquire()
        total = _new_metrics()
        total.pop('budget')
        remaining = self.limit
        try:
            while not self._stop_event.is_set():
                budget = self.batch_size if remaining is None else min(self.batch_size, remaining)
                result = self._run(budget)
                for key in ('success', 'miss', 'error', 'cache_hit', 'pending', 'cooldown', 'attempted', 'scanned'):
                    total[key] += result[key]
                total['has_failure'] |= result['has_failure']
                total.update(status=result['status'], pass_complete=result['pass_complete'])
                if remaining is not None:
                    remaining -= result['attempted']
                if result['status'] == 'paused_storage':
                    if not watch:
                        break
                    self._stop_event.wait(max(60., self.poll_interval))
                    continue
                if remaining == 0 or (result['pass_complete'] and not watch):
                    break
                if watch:
                    self._stop_event.wait(self.poll_interval)
                elif result['scanned'] == 0 and not result['pass_complete']:
                    raise RuntimeError('Scanner made no progress')
            return total
        finally:
            self._lock.release()
            self._lock = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        # Validate and acquire in the calling thread: startup errors are synchronous.
        self._acquire()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, kwargs={'watch': True}, daemon=False)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=40.)


def run_pipeline(rental_db, sale_db, vision_db, limit=None, downloader=None, model_caller=None):
    runner = ArchiveVisionRunner(rental_db, sale_db, vision_db, limit=limit, downloader=downloader, model_caller=model_caller)
    return runner.run()


def rebuild_derived(rental_db, sale_db, vision_db):
    _validate(rental_db, sale_db, vision_db, None, offline=True)
    lock = ArchiveVisionLock(Path(str(vision_db) + '.lock'))
    if not lock.acquire():
        raise RuntimeError('Another archive vision process is already running')
    try:
        store = ArchiveVisionStore(vision_db)
        after = ''
        while True:
            with store._connect() as conn:
                rows = list(conn.execute("SELECT * FROM image_observation WHERE image_url>? ORDER BY image_url LIMIT 500", (after,)))
            if not rows:
                break
            with store._connect() as conn:
                for row in rows:
                    after = row['image_url']
                    if row['status'] not in ('ok', 'miss') or row['schema_version'] not in (None, SCHEMA_VERSION):
                        continue
                    derived = derive_labels(row['image_kind'] or 'unknown', row['readability'] or 'no_text', json.loads(row['labels_json'] or '[]'))
                    conn.execute('UPDATE image_observation SET status=?,derived_json=?,schema_version=?,rules_version=? WHERE image_url=?',
                                 (derived.status, json.dumps(asdict(derived), ensure_ascii=False), SCHEMA_VERSION, RULES_VERSION, after))
        # Offline iteration uses a separate checkpoint so live progress is preserved.
        # Each source page is fully closed before any vision writes.
        for biz, path in [('rent', rental_db), ('sale', sale_db)]:
            if not path:
                continue
            after, upper = '', _source_rows(path)
            while True:
                rows = _source_rows(path, after, upper)
                if not rows:
                    break
                for row in rows:
                    after = row['listing_id']
                    if row['is_active']:
                        _publish_listing(store, biz, after, _payload(row))
                    else:
                        _inactive(store, biz, after)
            # Deleted IDs are checked explicitly, also in bounded pages.
            cursor = ''
            while True:
                with store._connect() as conn:
                    ids = [r[0] for r in conn.execute('SELECT listing_id FROM listing_vision WHERE biz=? AND listing_id>? ORDER BY listing_id LIMIT 500', (biz, cursor))]
                if not ids:
                    break
                for lid in ids:
                    current = _source_rows(path, listing_id=lid)
                    if not current or not current[0]['is_active']:
                        _inactive(store, biz, lid)
                cursor = ids[-1]
    finally:
        lock.release()


def extract_crm_hints(payload: dict, biz: str) -> CrmHints:
    list_data = payload.get("list") if isinstance(payload.get("list"), dict) else payload
    detail_data = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    head_data = detail_data.get("head") if isinstance(detail_data.get("head"), dict) else {}

    layout = list_data.get("layout") or payload.get("layout")
    floor_desc = list_data.get("floor_desc") or list_data.get("floorDesc") or payload.get("floor_desc")
    stat_function = list_data.get("stat_function") or list_data.get("statFunction") or payload.get("stat_function")
    house_usage = list_data.get("house_usage") or list_data.get("houseUsage") or head_data.get("house_usage") or head_data.get("houseUsage") or list_data.get("house_usage")
    floor_type = list_data.get("floor_type") or list_data.get("floorType") or head_data.get("floor_type") or head_data.get("floorType") or list_data.get("floor_type")
    property_purpose = list_data.get("property_purpose") or list_data.get("propertyPurpose") or payload.get("property_purpose")

    return CrmHints(
        layout=str(layout) if layout else None,
        floor_desc=str(floor_desc) if floor_desc else None,
        stat_function=str(stat_function) if stat_function else None,
        house_usage=str(house_usage) if house_usage else None,
        floor_type=str(floor_type) if floor_type else None,
        property_purpose=str(property_purpose) if property_purpose else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description='Independent archive vision worker (serial NIM requests)')
    parser.add_argument('--rental-db')
    parser.add_argument('--sale-db')
    parser.add_argument('--vision-db', required=True)
    parser.add_argument('--limit', type=int, help='Whole invocation image-attempt cap, including failed downloads')
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--scan-size', type=int, default=500)
    parser.add_argument('--poll-interval', type=float, default=60.)
    parser.add_argument('--min-free-gib', type=float, default=2.)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--backfill', action='store_true', help='Finish one bounded scan pass and exit')
    mode.add_argument('--watch', action='store_true', help='Repeat scan passes until stopped')
    mode.add_argument('--rebuild-derived', action='store_true', help='Offline reconstruction, no network or key required')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        if args.rebuild_derived:
            rebuild_derived(args.rental_db, args.sale_db, args.vision_db)
            return
        runner = ArchiveVisionRunner(args.rental_db, args.sale_db, args.vision_db,
                                     batch_size=args.batch_size, poll_interval=args.poll_interval,
                                     scan_size=args.scan_size, min_free_gib=args.min_free_gib, limit=args.limit)
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda signum, frame: runner._stop_event.set())
        try:
            metrics = runner.run(watch=args.watch)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        print(json.dumps(metrics, ensure_ascii=False))
        if runner._stop_event.is_set():
            return
        if metrics.get('status') == 'paused_storage':
            raise SystemExit(75)
        if metrics.get('has_failure'):
            raise SystemExit(1)
    except (ValueError, RuntimeError, OSError):
        # Do not print exception text: provider bodies and paths can contain secrets.
        logger.error('archive_vision startup_or_runtime_error')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
