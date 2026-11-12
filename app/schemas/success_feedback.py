"""Bounded catalog IDs only; feedback settings never accept media URLs/code."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sound = Literal[
    'none', 'selected-success', 'ring-golden', 'ring-prism', 'ring-crystal',
    'ring-arcade', 'kenney-sax', 'kenney-steel', 'kenney-arcade', 'kenney-glass',
    'kenney-confirmation', 'recorded-kalimba', 'recorded-balafon',
    'recorded-glockenspiel', 'recorded-vibraphone', 'moss-marimba', 'moss-piano',
    'moss-kalimba', 'moss-glockenspiel',
]
SmallSound = Sound | Literal['victory-bell-short']
SmallAnimation = Literal[
    'silk', 'aurora', 'comet', 'tides', 'ripple', 'shimmer', 'sparks', 'leaves',
    'panda-peek', 'check-bloom',
]
Animation = Literal['wave', 'panda', 'stamp', 'glow', 'spark', 'bamboo']
SetAnimation = Animation | Literal[
    'jade-tide', 'silk-current', 'moon-gate', 'jade-seal', 'ruyi-clouds',
    'bamboo-light', 'lotus-bloom', 'porcelain-ripple', 'orbit-harmony',
    'mountain-echo',
]


class SmallFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    animation: SmallAnimation
    sound: SmallSound


class LargerFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    animation: Animation
    sound: Sound


class SetFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    animation: SetAnimation
    sound: Sound


class SuccessConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    small: SmallFeedback
    step: LargerFeedback
    complete: SetFeedback


class SaveSuccessPreset(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    expectedVersion: int = Field(ge=0, strict=True)
    configuration: SuccessConfiguration


class ActivateSuccessPreset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    presetId: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,63}$')
    expectedVersion: int = Field(ge=1, strict=True)
    expectedPresetVersion: int = Field(ge=1, strict=True)
