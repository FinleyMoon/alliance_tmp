"""Game enums, parameter models, and action specifications."""
from enum import Enum
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field


class GameStatus(str, Enum):
    WAITING = "WAITING"
    MAP_VOTING = "MAP_VOTING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class ActionType(str, Enum):
    MOVE = "MOVE"
    ATTACK = "ATTACK"
    GIVE_AP = "GIVE_AP"
    UPGRADE_RANGE = "UPGRADE_RANGE"


class WinConditionType(str, Enum):
    LAST_MAN = "LAST_MAN"
    ARMISTICE = "ARMISTICE"
    ROGUE = "ROGUE"
    STAGNATION_DRAW = "STAGNATION_DRAW"
    STAGNATION_SUDDEN_DEATH = "STAGNATION_SUDDEN_DEATH"
    MAX_TURN = "MAX_TURN"


class RogueResolutionMode(str, Enum):
    GRANT_AP = "GRANT_AP"
    INSTANT_WIN = "INSTANT_WIN"
    REVEAL_IDENTITY = "REVEAL_IDENTITY"


class StagnationMode(str, Enum):
    DRAW = "DRAW"
    SUDDEN_DEATH = "SUDDEN_DEATH"


class LobbyConfig(BaseModel):
    """Full configurable parameters for a game lobby according to Section 13."""
    board_width: int = Field(default=15, ge=5, le=50)
    board_height: int = Field(default=15, ge=5, le=50)

    def __init__(self, **data: Any):
        if "grid_width" in data and "board_width" not in data:
            data["board_width"] = data["grid_width"]
        if "grid_height" in data and "board_height" not in data:
            data["board_height"] = data["grid_height"]
        super().__init__(**data)

    @property
    def grid_width(self) -> int:
        return self.board_width

    @property
    def grid_height(self) -> int:
        return self.board_height
    starting_hp: int = Field(default=3, ge=1, le=10)
    starting_ap: int = Field(default=0, ge=0, le=10)
    starting_range_level: int = Field(default=1, ge=1, le=5)
    ap_drop_cron: str = Field(default="0 22 * * *")
    ap_drop_timezone: str = Field(default="UTC")
    ap_drop_amount: int = Field(default=1, ge=1, le=10)
    ap_drop_server_time: str = Field(default="22:00")
    first_ap_drop_days: int = Field(default=1, ge=0, le=365)
    burst_cap_ap: int = Field(default=3, ge=1, le=10)
    move_cost_ap: int = Field(default=1, ge=1, le=5)
    attack_cost_ap: int = Field(default=1, ge=1, le=5)
    give_ap_cost_ap: int = Field(default=0, ge=0, le=5) # Default 0: no transfer fee
    range_upgrade_cost_base: int = Field(default=2, ge=1, le=10)
    range_upgrade_cost_scale: float = Field(default=1.0, ge=1.0, le=3.0)
    win_cond_last_man: bool = Field(default=True)
    win_cond_armistice: bool = Field(default=True)
    armistice_threshold: float = Field(default=1.0, ge=0.5, le=1.0)
    armistice_trigger_threshold: float = Field(default=1.0, ge=0.1, le=1.0)
    armistice_hide_vote_count: bool = Field(default=False)
    diplomatic_conference_duration_sec: int = Field(default=120, ge=15, le=3600)
    conference_transparency: str = Field(default="REVEAL_COUNTS_AT_END")
    conference_end_condition: str = Field(default="ON_ALL_VOTED")
    conference_single_disagree_outcomes: Union[List[str], str] = Field(default='["GRANT_AP"]')
    conference_multi_disagree_outcomes: Union[List[str], str] = Field(default='["RESUME_COMBAT"]')
    armistice_outcome_multi_disagree: str = Field(default="RESUME_COMBAT")
    win_cond_rogue: bool = Field(default=True)
    rogue_resolution_mode: RogueResolutionMode = Field(default=RogueResolutionMode.GRANT_AP)
    rogue_bonus_ap: int = Field(default=3, ge=1, le=10)
    multi_disagree_bonus_ap: int = Field(default=2, ge=1, le=10)
    win_cond_stagnation: bool = Field(default=False)
    stagnation_days_limit: int = Field(default=5, ge=1, le=30)
    stagnation_mode: StagnationMode = Field(default=StagnationMode.DRAW)
    reveal_killer_on_death: bool = Field(default=False)
    allow_dead_chat: bool = Field(default=True)
    fog_of_war_stats: bool = Field(default=True)
    require_email_verify: bool = Field(default=False)
    audit_log_public: bool = Field(default=True)
    allow_late_join: bool = Field(default=False)
    map_voting_enabled: bool = Field(default=False)
    map_voting_duration_minutes: int = Field(default=10, ge=1, le=1440)


class ActionCommand(BaseModel):
    """Single action intent inside a command batch."""
    action_type: ActionType
    # Coordinates for MOVE action
    to_x: Optional[int] = None
    to_y: Optional[int] = None
    # Target player id for ATTACK or GIVE_AP
    target_player_id: Optional[str] = None


class ActionBatchPayload(BaseModel):
    """Action batch submitted by player, bounded by burst_cap_ap with state sequence verification."""
    expected_seq: Optional[int] = None
    actions: List[ActionCommand]
