from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.invited_pilot_plan import (
    INPUT_FILE_FIELDS,
    load_invited_pilot_plan,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "examples" / "invited-public-pilot.sepolia.example.yaml"


def _load() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, value: dict) -> Path:
    target = tmp_path / "pilot.yaml"
    target.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return target


def _materialize_inputs(plan_path: Path, value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in INPUT_FILE_FIELDS and isinstance(child, str):
                target = (plan_path.parent / child).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture\n", encoding="utf-8")
                if os.name != "nt":
                    target.chmod(0o600)
            else:
                _materialize_inputs(plan_path, child)
    elif isinstance(value, list):
        for child in value:
            _materialize_inputs(plan_path, child)


def test_example_is_valid_but_cannot_authorize_execution() -> None:
    value, summary = load_invited_pilot_plan(EXAMPLE)
    assert value["execution_requested"] is False
    assert summary["execution_authorized"] is False
    assert summary["validation_scope"] == (
        "plan_structure_and_referenced_input_presence"
    )
    assert summary["input_files_present"] is None
    assert summary["participant_count"] == 3
    assert summary["operator_group_count"] == 2
    assert summary["network_group_count"] == 3


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda value: value["rpc"].update(
                secondary_url_file=value["rpc"]["primary_url_file"]
            ),
            "pilot_plan_rpc_files_not_independent",
        ),
        (
            lambda value: value["rpc"].update(
                primary_url_file="https://credentialed.invalid/key"
            ),
            "pilot_plan_secret_must_use_file",
        ),
        (
            lambda value: [
                item.update(operator_group="operator-a")
                for item in value["participants"][1:]
            ],
            "pilot_plan_operator_groups_insufficient",
        ),
        (
            lambda value: [
                item.update(network_group="network-a")
                for item in value["participants"][1:]
            ],
            "pilot_plan_network_groups_insufficient",
        ),
        (
            lambda value: value["pilot"].update(
                participant_loopback_url=value["pilot"]["admin_url"]
            ),
            "pilot_plan_surfaces_not_separated",
        ),
    ],
)
def test_semantic_failures(tmp_path: Path, mutate, code: str) -> None:
    value = _load()
    mutate(value)
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value))
    assert error.value.code == code


def test_inline_secret_key_is_rejected_before_use(tmp_path: Path) -> None:
    value = _load()
    value["rpc"]["rpc_url"] = "https://credentialed.invalid/key"
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value))
    assert error.value.code == "pilot_plan_inline_secret_field"


def test_execution_rejects_template_sentinels(tmp_path: Path) -> None:
    value = _load()
    value["execution_requested"] = True
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value))
    assert error.value.code == "pilot_plan_placeholder_present"


def test_input_file_check_is_fail_closed(tmp_path: Path) -> None:
    value = copy.deepcopy(_load())
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value), check_input_files=True)
    assert error.value.code == "pilot_plan_input_file_missing"


def test_input_file_check_validates_distinct_restricted_rpc_values(
    tmp_path: Path,
) -> None:
    value = _load()
    plan_path = _write(tmp_path, value)
    _materialize_inputs(plan_path, value)
    primary = (plan_path.parent / value["rpc"]["primary_url_file"]).resolve()
    secondary = (plan_path.parent / value["rpc"]["secondary_url_file"]).resolve()
    primary.write_text("https://primary.invalid/key\n", encoding="utf-8")
    secondary.write_text("https://secondary.invalid/key\n", encoding="utf-8")
    if os.name != "nt":
        primary.chmod(0o600)
        secondary.chmod(0o600)

    _, summary = load_invited_pilot_plan(plan_path, check_input_files=True)
    assert summary["input_files_checked"] is True
    assert summary["input_files_present"] is True

    secondary.write_text("https://primary.invalid/other-key\n", encoding="utf-8")
    if os.name != "nt":
        secondary.chmod(0o600)
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(plan_path, check_input_files=True)
    assert error.value.code == "distinct_rpc_origins_required"


def test_participant_state_paths_are_unique_and_fresh(tmp_path: Path) -> None:
    value = _load()
    value["participants"][1]["cursor_database"] = value["participants"][0][
        "cursor_database"
    ]
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value))
    assert error.value.code == "pilot_plan_duplicate_identity"

    value = _load()
    plan_path = _write(tmp_path, value)
    _materialize_inputs(plan_path, value)
    primary = (plan_path.parent / value["rpc"]["primary_url_file"]).resolve()
    secondary = (plan_path.parent / value["rpc"]["secondary_url_file"]).resolve()
    primary.write_text("https://primary.invalid/key\n", encoding="utf-8")
    secondary.write_text("https://secondary.invalid/key\n", encoding="utf-8")
    if os.name != "nt":
        primary.chmod(0o600)
        secondary.chmod(0o600)
    _, summary = load_invited_pilot_plan(plan_path, check_input_files=True)
    assert summary["input_files_present"] is True


def test_secret_file_field_requires_a_path_shape(tmp_path: Path) -> None:
    value = _load()
    value["pilot"]["write_token_file"] = "opaque-inline-token-value"
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value))
    assert error.value.code == "schema_validation_failed"


def test_loopback_aliases_cannot_share_a_surface_port(tmp_path: Path) -> None:
    value = _load()
    value["pilot"]["participant_loopback_url"] = "http://localhost:8780"
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(_write(tmp_path, value))
    assert error.value.code == "pilot_plan_surfaces_not_separated"


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    raw = EXAMPLE.read_text(encoding="utf-8").replace(
        "execution_requested: false",
        "execution_requested: false\nexecution_requested: true",
        1,
    )
    target = tmp_path / "duplicate.yaml"
    target.write_text(raw, encoding="utf-8")
    with pytest.raises(LoveEngineError) as error:
        load_invited_pilot_plan(target)
    assert error.value.code == "pilot_plan_invalid_yaml"
