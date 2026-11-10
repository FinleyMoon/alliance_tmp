"""Pydantic schemas for API inputs, outputs, and public/private serializers."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from app.models.game_types import LobbyConfig, ActionBatchPayload, GameStatus


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=128)
    national_flag: str = Field(default="🇺🇳", min_length=1, max_length=32)
    preferred_lang: str = Field(default="en")


class UserLoginRequest(BaseModel):
    username: str
    password: str


class UserPasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class UserProfileUpdateRequest(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=30)
    national_flag: Optional[str] = Field(default=None, min_length=1, max_length=32)
    preferred_lang: Optional[str] = Field(default=None)


class ConferenceVoteRequest(BaseModel):
    vote: bool


class PlaytestAwardApRequest(BaseModel):
    amount: int = Field(default=1, ge=1, le=10)


class PlaytestControlBotRequest(BaseModel):
    bot_player_id: str
    actions: Optional[List[Any]] = None
    action_batch: Optional[Any] = None
    expected_seq: Optional[int] = None


class UserProfileResponse(BaseModel):
    id: str
    username: str
    national_flag: str
    avatar_path: Optional[str]
    preferred_lang: str
    is_admin: bool


class CreateGameRequest(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    is_playtest: bool = False
    config: LobbyConfig = Field(default_factory=LobbyConfig)


class UpdateGameConfigRequest(BaseModel):
    updates: Dict[str, Any]


class PublicPlayerState(BaseModel):
    """Public serializer: hides opponents' HP, AP, and Range tiers."""
    id: str
    user_id: str
    username: str
    national_flag: str
    avatar_path: Optional[str]
    x: int
    y: int
    is_alive: bool
    is_bot: bool = False
    armistice_vote: bool
    conference_vote: Optional[int] = None
    # Confidential stats are None for opponents under Fog of War
    hp: Optional[int] = None
    ap: Optional[int] = None
    range_level: Optional[int] = None
    is_self: bool = False


class PublicGameStateResponse(BaseModel):
    """Full board state respecting Fog of War separation."""
    game_id: str
    name: str
    status: GameStatus
    board_width: int
    board_height: int
    current_shrink_level: int
    is_playtest: bool = False
    players: List[PublicPlayerState]
    self_player_id: Optional[str] = None
    winner_id: Optional[str] = None
    winner_type: Optional[str] = None
    winner_details: Optional[str] = None
    conference_status: str = "IDLE"
    conference_ends_at: Optional[str] = None
    conference_triggered_at: Optional[str] = None
    conference_outcome: Optional[str] = None
    map_voting_ends_at: Optional[str] = None
    last_event_seq: int = 0
    config: LobbyConfig


class RecreateGameRequest(BaseModel):
    name: Optional[str] = None
    is_playtest: Optional[bool] = None
    config: Optional[LobbyConfig] = None


class MapVoteRequest(BaseModel):
    ranks: List[int]


class GhostTrailItem(BaseModel):
    """Vector trail showing movement since player was last online."""
    player_id: str
    username: str
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    timestamp: str


class BoardDiffResponse(BaseModel):
    """What changed since last visit visualization payload."""
    last_active_timestamp: Optional[str]
    ghost_trails: List[GhostTrailItem]
    offline_events: List[Dict[str, Any]]
    casualty_sites: List[Dict[str, Any]]


class ActionExecutionReceipt(BaseModel):
    """Receipt returned to client after batch resolves."""
    request_id: str
    success: bool
    total_ap_spent: int
    remaining_ap: int
    receipts: List[Dict[str, Any]]
    updated_state: PublicGameStateResponse


class AuditLogItem(BaseModel):
    id: int
    game_id: str
    param_key: str
    old_value: str
    new_value: str
    admin_username: str
    timestamp: str


class ReplayEventItem(BaseModel):
    id: int
    event_seq: int
    event_type: str
    initiator_user_id: Optional[str]
    target_user_id: Optional[str]
    visibility: str
    payload: Dict[str, Any]
    prev_hash: str
    hash: str
    timestamp_ns: int
    created_at: str


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class InAppNotificationItem(BaseModel):
    id: str
    game_id: Optional[str]
    event_type: str
    title: str
    message: str
    is_read: bool
    created_at: str
