"""顺序主备提取策略（重试 + 兜底）与字段格式校验的单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import house_verify as hv  # noqa: E402
from verify_archive import extract_json  # noqa: E402

from app.domain.value_objects.business_number import (  # noqa: E402
    BusinessNumber, is_valid_business_number)
from app.domain.value_objects.certificate_number import (  # noqa: E402
    CertificateNumber, is_valid_certificate_number)


def _task(label: str, ywh: str, zsbm: str,
          fail: str | None = None, fail_times: int = 0) -> dict:
    return {"label": label, "api_key": "k", "model": f"model-{label}",
            "channel": "openrouter", "prompt": "p",
            "_ywh": ywh, "_zsbm": zsbm, "_fail": fail, "_fail_times": fail_times}


def _stub_call(tasks, monkeypatch):
    counters = {t["model"]: 0 for t in tasks}

    def fake(api_key, model, image_path, prompt, **kwargs):
        task = next(t for t in tasks if t["model"] == model)
        counters[model] += 1
        if task["_fail_times"] and counters[model] <= task["_fail_times"]:
            raise RuntimeError(task["_fail"])
        return {"业务件号": task["_ywh"], "证件编码": task["_zsbm"]}

    monkeypatch.setattr(hv, "call_vl_model", fake)
    return counters


def test_primary_succeeds_first_try(monkeypatch, tmp_path):
    tasks = [_task("主识别服务(OpenRouter)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号"),
             _task("备用识别服务(NVIDIA)", "2025112701F99999",
                   "川（2025）成都市不动产权第0373259号")]
    counters = _stub_call(tasks, monkeypatch)
    result = hv.extract_credentials_chained(tasks, tmp_path / "c.jpg", retries=2)
    assert result["status"] == "ok"
    assert result["cred"]["业务件号"] == "2025112701F90697"
    assert result["used_label"] == "主识别服务(OpenRouter)"
    assert counters["model-主识别服务(OpenRouter)"] == 1
    assert counters["model-备用识别服务(NVIDIA)"] == 0  # 主模型成功，兜底不调用


def test_primary_retries_then_succeeds(monkeypatch, tmp_path):
    tasks = [_task("主识别服务(OpenRouter)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号",
                   fail="connection error", fail_times=2),
             _task("备用识别服务(NVIDIA)", "x", "y")]
    counters = _stub_call(tasks, monkeypatch)
    result = hv.extract_credentials_chained(tasks, tmp_path / "c.jpg", retries=2)
    assert result["used_label"] == "主识别服务(OpenRouter)"
    assert counters["model-主识别服务(OpenRouter)"] == 3  # 初次 + 重试 2 次
    assert counters["model-备用识别服务(NVIDIA)"] == 0


def test_primary_exhausted_falls_back(monkeypatch, tmp_path):
    tasks = [_task("主识别服务(OpenRouter)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号",
                   fail="connection error", fail_times=99),
             _task("备用识别服务(NVIDIA)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号")]
    counters = _stub_call(tasks, monkeypatch)
    result = hv.extract_credentials_chained(tasks, tmp_path / "c.jpg", retries=2)
    assert result["used_label"] == "备用识别服务(NVIDIA)"
    assert counters["model-主识别服务(OpenRouter)"] == 3
    assert counters["model-备用识别服务(NVIDIA)"] == 1


def test_invalid_format_triggers_retry(monkeypatch, tmp_path):
    tasks = [_task("主识别服务(OpenRouter)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号",
                   fail="bad", fail_times=1),
             _task("备用识别服务(NVIDIA)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号")]
    counters = _stub_call(tasks, monkeypatch)
    result = hv.extract_credentials_chained(tasks, tmp_path / "c.jpg", retries=1)
    assert result["used_label"] == "主识别服务(OpenRouter)"
    assert counters["model-主识别服务(OpenRouter)"] == 2


def test_all_models_fail_raises(monkeypatch, tmp_path):
    tasks = [_task("主识别服务(OpenRouter)", "", "", fail="boom", fail_times=99),
             _task("备用识别服务(NVIDIA)", "", "", fail="boom", fail_times=99)]
    _stub_call(tasks, monkeypatch)
    with pytest.raises(ValueError, match="均未能提取有效字段"):
        hv.extract_credentials_chained(tasks, tmp_path / "c.jpg", retries=1)


def test_primary_hangs_timeout_then_falls_back(monkeypatch, tmp_path):
    """网络层挂死（requests 超时不生效）时，硬性 deadline 保证兜底模型被执行。"""
    import time

    tasks = [_task("主识别服务(OpenRouter)", "x", "y"),
             _task("备用识别服务(NVIDIA)", "2025112701F90697",
                   "川（2025）成都市不动产权第0373259号")]
    counters = {t["model"]: 0 for t in tasks}

    def fake(api_key, model, image_path, prompt, **kwargs):
        counters[model] += 1
        if model == "model-主识别服务(OpenRouter)":
            time.sleep(120)  # 模拟网络层挂死
        return {"业务件号": "2025112701F90697",
                "证件编码": "川（2025）成都市不动产权第0373259号"}

    monkeypatch.setattr(hv, "call_vl_model", fake)
    result = hv.extract_credentials_chained(tasks, tmp_path / "c.jpg",
                                            retries=0, timeout=0.5)
    assert result["used_label"] == "备用识别服务(NVIDIA)"
    assert counters["model-主识别服务(OpenRouter)"] == 1
    assert counters["model-备用识别服务(NVIDIA)"] == 1


def test_build_result_aligned():
    headers = ["业务件号", "产权证号", "区域", "是否抵押", "是否查封"]
    cells = ["2022062001F90498", "川（2022）成都市不动产权第0164798号",
             "成华区", "抵押", "否"]
    parsed = hv._build_result(headers, cells)
    assert parsed["fields"]["是否查封"] == "否"
    assert parsed["fields"]["是否抵押"] == "抵押"
    assert "结论" not in parsed  # 只返回原始数据，不做总结


def test_build_result_misaligned_keeps_raw_only():
    parsed = hv._build_result(["a", "b", "c"], ["1", "2"])
    assert parsed["fields"] == {}
    assert parsed["headers"] == ["a", "b", "c"]
    assert parsed["row"] == ["1", "2"]


def test_extract_json_reasoning_model():
    """推理模型输出：</think> 思考内容 + 正式 JSON。"""
    raw = ('先看业务件号…"业务号:2022062001F90498"…</think>\n'
           '{"业务件号": "2022062001F90498", "证件编码": "监证1"}')
    parsed = extract_json(raw)
    assert parsed["业务件号"] == "2022062001F90498"


def test_extract_json_reasoning_with_example_json():
    """思考内容中包含示例 JSON（花括号）时，取 </think> 后的正式 JSON。"""
    raw = ('参考示例 {"业务件号": "2025112701F90697"} 输出如下</think>\n'
           '{"业务件号": "权1234", "证件编码": "监证5678"}')
    parsed = extract_json(raw)
    assert parsed["业务件号"] == "权1234"
    assert parsed["证件编码"] == "监证5678"


def test_extract_json_markdown_and_trailing_text():
    raw = '```json\n{"业务件号": "2025112701F90697", "证件编码": "x1"}\n```\n以上为结果'
    parsed = extract_json(raw)
    assert parsed["业务件号"] == "2025112701F90697"


def test_extract_json_malformed_raises():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        extract_json("完全没有 JSON 的文本")


def test_field_validators():
    assert is_valid_business_number("2025112701F90697")
    assert is_valid_business_number("2016092042F1234")
    assert is_valid_business_number("2022062001P90498")  # F/P 视觉易混，两者都接受
    assert is_valid_business_number("权1234")
    assert not is_valid_business_number("12345")
    assert not is_valid_business_number("权")
    assert is_valid_certificate_number("监证1234567")
    assert is_valid_certificate_number("川（2025）成都市不动产权第0373259号")
    assert not is_valid_certificate_number("123")


def test_value_objects_validate():
    assert BusinessNumber("权12").value == "权12"
    with pytest.raises(ValueError):
        BusinessNumber("乱七八糟")
    assert CertificateNumber("监证1").value == "监证1"
    with pytest.raises(ValueError):
        CertificateNumber("乱七八糟")
