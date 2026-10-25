"""One test per compatibility rule, including the miss that must not say fits."""

import json
from pathlib import Path

from contracts import validate

from market.compat import check

BOARD_PATH = Path(__file__).resolve().parents[1] / "data" / "boards" / "demo-b650.json"
BOARD_SPECS = json.loads(BOARD_PATH.read_text())["specs"]


def _owned(**overrides):
    specs = dict(BOARD_SPECS)
    specs.update(overrides)
    parts = [{
        "category": "board",
        "model": "Demo B650 board",
        "specs": specs,
        "confirmed": True,
    }]
    return parts


def _ram(**overrides):
    specs = {
        "ddr_gen": "DDR5",
        "dimm_format": "DIMM",
        "ecc": False,
        "speed_mts": 6000,
        "capacity_gb": 32,
        "modules": 2,
    }
    specs.update(overrides)
    return specs


def _ssd(**overrides):
    specs = {"m2_key": "M", "pcie_gen": 4}
    specs.update(overrides)
    return specs


def test_ddr_gen_match_fits():
    verdict = check("ram", _ram(), _owned())
    validate("compat", verdict)
    assert verdict["status"] == "fits"
    assert verdict["unknown"] == []
    assert "DDR5 matches your board" in verdict["reasons"]


def test_ddr_gen_mismatch_is_no():
    verdict = check("ram", _ram(ddr_gen="DDR4"), _owned())
    assert verdict["status"] == "no"
    assert "DDR4 does not match your board's DDR5" in verdict["reasons"]


def test_dimm_format_match_fits():
    verdict = check("ram", _ram(), _owned())
    assert verdict["status"] == "fits"
    assert "DIMM matches your board" in verdict["reasons"]


def test_dimm_format_mismatch_is_no():
    verdict = check("ram", _ram(dimm_format="SODIMM"), _owned())
    assert verdict["status"] == "no"
    assert "SODIMM does not match your board's DIMM" in verdict["reasons"]


def test_ecc_false_fits_on_consumer_board():
    offer = _ram()
    offer.pop("buffered", None)
    verdict = check("ram", offer, _owned())
    assert verdict["status"] == "fits"
    assert "Not ECC" in verdict["reasons"]


def test_ecc_true_is_no_on_consumer_board():
    verdict = check("ram", _ram(ecc=True), _owned())
    assert verdict["status"] == "no"
    assert "ECC memory does not fit this consumer board" in verdict["reasons"]


def test_buffered_true_is_no_on_consumer_board():
    verdict = check("ram", _ram(buffered=True), _owned())
    assert verdict["status"] == "no"
    assert "Buffered memory does not fit this consumer board" in verdict["reasons"]


def test_ecc_true_fits_when_board_says_ecc():
    verdict = check("ram", _ram(ecc=True), _owned(ecc=True))
    assert verdict["status"] == "fits"
    assert "ECC matches your board" in verdict["reasons"]


def test_speed_within_max_fits():
    verdict = check("ram", _ram(speed_mts=6400), _owned())
    assert verdict["status"] == "fits"
    assert "6400 MT/s is within your board's 6400 MT/s" in verdict["reasons"]
    assert not any(reason.startswith("will run at") for reason in verdict["reasons"])


def test_speed_above_max_still_fits():
    verdict = check("ram", _ram(speed_mts=7200), _owned())
    assert verdict["status"] == "fits"
    assert verdict["unknown"] == []
    assert "will run at 6400 MT/s" in verdict["reasons"]


def test_capacity_within_max_fits():
    verdict = check("ram", _ram(capacity_gb=192), _owned())
    assert verdict["status"] == "fits"
    assert "192 GB is within your board's 192 GB" in verdict["reasons"]


def test_capacity_over_max_is_no():
    verdict = check("ram", _ram(capacity_gb=256), _owned())
    assert verdict["status"] == "no"
    assert "256 GB is over your board's 192 GB" in verdict["reasons"]


def test_modules_within_slots_fit():
    verdict = check("ram", _ram(modules=4), _owned())
    assert verdict["status"] == "fits"
    assert "4 modules fit your board's 4 slots" in verdict["reasons"]


def test_modules_over_slots_is_no():
    verdict = check("ram", _ram(modules=8), _owned())
    assert verdict["status"] == "no"
    assert "8 modules is more than your board's 4 slots" in verdict["reasons"]


def test_missing_spec_is_unverified_never_fits():
    offer = _ram()
    del offer["speed_mts"]
    verdict = check("ram", offer, _owned())
    validate("compat", verdict)
    assert verdict["status"] == "unverified"
    assert verdict["status"] != "fits"
    assert "speed_mts" in verdict["unknown"]
    assert "DDR5 matches your board" in verdict["reasons"]


def test_missing_board_field_is_unverified():
    specs = dict(BOARD_SPECS)
    del specs["max_capacity_gb"]
    owned = [{"category": "board", "model": "Demo B650 board", "specs": specs, "confirmed": True}]
    verdict = check("ram", _ram(), owned)
    assert verdict["status"] == "unverified"
    assert "max_capacity_gb" in verdict["unknown"]


def test_failed_rule_beats_a_missing_field():
    offer = _ram(ddr_gen="DDR4")
    del offer["capacity_gb"]
    verdict = check("ram", offer, _owned())
    assert verdict["status"] == "no"
    assert "capacity_gb" in verdict["unknown"]


def test_ssd_key_and_pcie_match_fits():
    verdict = check("ssd", _ssd(), _owned())
    assert verdict["status"] == "fits"
    assert "M key matches your board" in verdict["reasons"]
    assert "PCIe 4 is within your board's PCIe 4" in verdict["reasons"]


def test_ssd_key_mismatch_is_no():
    verdict = check("ssd", _ssd(m2_key="B"), _owned())
    assert verdict["status"] == "no"
    assert "B key does not match your board's M key" in verdict["reasons"]


def test_ssd_faster_pcie_still_fits():
    verdict = check("ssd", _ssd(pcie_gen=5), _owned())
    assert verdict["status"] == "fits"
    assert "runs at PCIe 4 speed" in verdict["reasons"]


def test_ssd_missing_key_is_unverified():
    offer = _ssd()
    del offer["m2_key"]
    verdict = check("ssd", offer, _owned())
    assert verdict["status"] == "unverified"
    assert verdict["status"] != "fits"
    assert verdict["unknown"] == ["m2_key"]


def test_other_category_has_no_rules_yet():
    verdict = check("gpu", {"wattage_w": 200}, _owned())
    assert verdict == {
        "status": "unverified",
        "reasons": [],
        "unknown": ["category rules not written yet"],
    }


def test_ram_without_a_board_is_unverified():
    verdict = check("ram", _ram(), [])
    assert verdict["status"] == "unverified"
    assert verdict["unknown"] == ["board"]
