"""业务件号 / 证件编码值对象的单元测试：首尾空白视为无意义。"""
from __future__ import annotations

import pytest

from app.domain.value_objects.business_number import (
    BusinessNumber, is_valid_business_number)
from app.domain.value_objects.certificate_number import (
    CertificateNumber, is_valid_certificate_number)


# ---- 既有合法样例仍应通过 ----------------------------------------------

def test_existing_business_number_valids_still_pass():
    assert is_valid_business_number("2025112701F90697")
    assert is_valid_business_number("2016092042F1234")
    assert is_valid_business_number("2022062001P90498")
    assert is_valid_business_number("权1234")


def test_existing_certificate_number_valids_still_pass():
    assert is_valid_certificate_number("监证1234567")
    assert is_valid_certificate_number("川（2025）成都市不动产权第0373259号")


# ---- 既有非法样例仍应被拒绝 --------------------------------------------

def test_existing_business_number_invalids_still_fail():
    assert not is_valid_business_number("12345")
    assert not is_valid_business_number("权")
    assert not is_valid_business_number("123")
    assert not is_valid_business_number("")


def test_existing_certificate_number_invalids_still_fail():
    assert not is_valid_certificate_number("123")
    assert not is_valid_certificate_number("")


# ---- 首尾空白：合法值包裹空白应被接受 ----------------------------------

def test_business_number_padded_is_accepted_and_stored_stripped():
    bn = BusinessNumber(" 权1234 ")
    assert bn.value == "权1234"

    bn2 = BusinessNumber("\t2025112701F90697\n")
    assert bn2.value == "2025112701F90697"


def test_certificate_number_padded_is_accepted_and_stored_stripped():
    cn = CertificateNumber(" 监证1234567 ")
    assert cn.value == "监证1234567"

    cn2 = CertificateNumber(" 川（2025）成都市不动产权第0373259号 \n")
    assert cn2.value == "川（2025）成都市不动产权第0373259号"


def test_is_valid_helpers_accept_padded_legitimate_values():
    assert is_valid_business_number(" 权1234 ")
    assert is_valid_business_number("\t2025112701F90697\n")
    assert is_valid_certificate_number(" 监证1234567 ")
    assert is_valid_certificate_number(" 川（2025）成都市不动产权第0373259号 \n")


# ---- 纯空白仍应被拒绝 --------------------------------------------------

def test_whitespace_only_is_rejected():
    assert not is_valid_business_number("   ")
    assert not is_valid_business_number("\t\n")
    assert not is_valid_certificate_number("   ")
    assert not is_valid_certificate_number("\t\n")

    with pytest.raises(ValueError):
        BusinessNumber("   ")
    with pytest.raises(ValueError):
        CertificateNumber("   ")


# ---- 非字符串（含 None）应被 is_valid_* 拒绝 ----------------------------

def test_non_string_inputs_rejected_by_is_valid():
    assert not is_valid_business_number(None)
    assert not is_valid_certificate_number(None)
    assert not is_valid_business_number(1234567)
    assert not is_valid_business_number(["权1234"])
