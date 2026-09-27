"""Bounded docking proposals only, never admission, consent or execution authority."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, ValidationError,
    field_validator, model_serializer, model_validator,
)

from src.agent.contracts.decision import DecisionProtocolError, decode_protocol_json


__all__ = [
    "DockingPreparationProposal", "DockingPreparationEnvelope",
    "docking_preparation_json_schema", "parse_docking_preparation_json",
]

_MAX_BYTES = 8192
_MAX_DEPTH = 8
_MAX_NODES = 256
_ERROR = "invalid_docking_preparation"


def _string(value):
    if type(value) is not str:
        raise ValueError(_ERROR)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(_ERROR) from None
    return value


def _integer(value):
    if type(value) is not int:
        raise ValueError(_ERROR)
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError(_ERROR)
    return value


def _number(value):
    if type(value) not in (int, float):
        raise ValueError(_ERROR)
    if type(value) is float and not math.isfinite(value):
        raise ValueError(_ERROR)
    return value


def _list(value):
    if type(value) is not list:
        raise ValueError(_ERROR)
    return value


_Integer = Annotated[int, BeforeValidator(_integer)]
_Index = Annotated[_Integer, Field(ge=0)]
_Number = Annotated[int | float, BeforeValidator(_number)]
_SizeNumber = Annotated[_Number, Field(gt=0, le=100)]
_SourceId = Annotated[
    str, BeforeValidator(_string), Field(min_length=1, max_length=128, pattern=r"^[^/\\]+$"),
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def native_object(cls, value):
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise ValueError(_ERROR)
        return value


class _SourceSpan(_ClosedModel):
    source_id: _SourceId
    start: _Index
    end: _Index
    role: Annotated[Literal["request", "field", "extra_obligation"], BeforeValidator(_string)]

    @model_validator(mode="after")
    def nonempty_range(self):
        if self.end <= self.start:
            raise ValueError(_ERROR)
        # The server must still check the unchanged source's code-point length,
        # existence, ownership and generation; the DTO has none of that context.
        return self


class _Input(_ClosedModel):
    span_indices: Annotated[list[_Index], BeforeValidator(_list), Field(min_length=1, max_length=4)]

    @field_validator("span_indices")
    @classmethod
    def distinct_indices(cls, value):
        if len(set(value)) != len(value):
            raise ValueError(_ERROR)
        return value


class _ReferenceInput(_Input):
    value: _SourceId


class _CenterInput(_Input):
    value: Annotated[list[_Number], BeforeValidator(_list), Field(min_length=3, max_length=3)]


class _SizeInput(_Input):
    value: Annotated[list[_SizeNumber], BeforeValidator(_list), Field(min_length=3, max_length=3)]


class _ExhaustivenessInput(_Input):
    value: Annotated[_Integer, Field(ge=1, le=64)]


class _NumModesInput(_Input):
    value: Annotated[_Integer, Field(ge=1, le=50)]


def _omitted():
    # Private storage for an absent member, not an accepted input/default value.
    # A non-nullable annotation rejects explicit null and advertises no null
    # alternative; a factory avoids advertising a schema default of null.
    return None


class _Fields(_ClosedModel):
    receptor_ref: _ReferenceInput = Field(default_factory=_omitted)
    ligand_ref: _ReferenceInput = Field(default_factory=_omitted)
    center: _CenterInput = Field(default_factory=_omitted)
    size: _SizeInput = Field(default_factory=_omitted)
    exhaustiveness: _ExhaustivenessInput = Field(default_factory=_omitted)
    num_modes: _NumModesInput = Field(default_factory=_omitted)

    @model_serializer(mode="wrap")
    def omit_absent_members(self, serialize):
        # Also preserve absence in ordinary/nested dumps, not only when callers
        # remember exclude_unset=True. No missing numeric input is inferred.
        return {key: value for key, value in serialize(self).items() if key in self.model_fields_set}


class DockingPreparationProposal(_ClosedModel):
    version: Annotated[Literal["1"], BeforeValidator(_string)]
    kind: Annotated[
        Literal[
            "not_c", "docking_request", "docking_refinement",
            "docking_observation", "mixed", "uncertain",
        ],
        BeforeValidator(_string),
    ]
    unresolved: Annotated[bool, BeforeValidator(_boolean)]
    source_spans: Annotated[list[_SourceSpan], BeforeValidator(_list), Field(max_length=16)]
    fields: _Fields

    @model_validator(mode="after")
    def consistent_proposal(self):
        present = self.fields.model_fields_set
        if self.kind in {"not_c", "mixed", "uncertain", "docking_observation"} and present:
            raise ValueError(_ERROR)
        if self.kind in {"mixed", "uncertain"} and not self.unresolved:
            raise ValueError(_ERROR)
        for name in present:
            field = getattr(self.fields, name)
            if any(index >= len(self.source_spans) for index in field.span_indices):
                raise ValueError(_ERROR)
        return self


class DockingPreparationEnvelope(_ClosedModel):
    proposal: DockingPreparationProposal


def docking_preparation_json_schema() -> dict:
    return DockingPreparationEnvelope.model_json_schema()


def _check_resources(value):
    # Root depth 0; keys count as nodes, not as additional value depth. This
    # guard runs before ANY model validation, including for invalid envelopes.
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if type(item) is dict:
            nodes += len(item)
            children = item.values()
        elif type(item) is list:
            children = item
        else:
            children = ()
        if depth > _MAX_DEPTH or nodes > _MAX_NODES:
            raise ValueError(_ERROR)
        stack.extend((child, depth + 1) for child in children)


def parse_docking_preparation_json(raw: str) -> DockingPreparationProposal:
    """Parse one whole native/JSON payload, without repair or authorization."""
    try:
        value = decode_protocol_json(raw, max_bytes=_MAX_BYTES)
        _check_resources(value)
        return DockingPreparationEnvelope.model_validate(value, strict=True).proposal
    except (DecisionProtocolError, ValidationError, ValueError):
        # Decoder codes, dynamic keys and Pydantic input/context are private.
        raise DecisionProtocolError(_ERROR) from None
