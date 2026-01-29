"""
Truth Engine - State machine for esports match truth.

Converts raw esports events into high-confidence trading signals.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union

from src.bot.bus import Event
from src.bot.logging import get_component_logger
from src.bot.settings import TruthEngineConfig
from src.domain.types import (
    DataSourceTier,
    MatchEventType,
    TruthState,
    TruthStatus,
)

logger = get_component_logger("truth_engine")


# =============================================================================
# Event Types
# =============================================================================


@dataclass
class MatchEvent(Event):
    """Normalized esports match event."""

    match_id: str = ""
    event_type: MatchEventType = MatchEventType.MATCH_CREATED
    source: str = ""
    source_tier: DataSourceTier = DataSourceTier.TIER_B

    # Event-specific data
    payload: dict[str, Any] = field(default_factory=dict)

    # For dedup
    source_event_id: Optional[str] = None
    seq: Optional[int] = None

    def partition_key(self) -> str:
        return self.match_id


@dataclass
class TruthDelta(Event):
    """Truth engine state change signal."""

    match_id: str = ""
    delta_type: str = ""  # "status" | "score" | "round" | "map"
    old_value: Any = None
    new_value: Any = None
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    reason: Optional[str] = None

    def partition_key(self) -> str:
        return self.match_id


@dataclass
class TruthFinal(Event):
    """Match outcome finalized signal."""

    match_id: str = ""
    winner_team_id: str = ""
    confidence: float = 0.0
    confirmed_by: list[str] = field(default_factory=list)
    finalized_at_ms: int = 0

    def partition_key(self) -> str:
        return self.match_id


# =============================================================================
# Truth Engine
# =============================================================================


class TruthEngine:
    """
    State machine for converting esports events into truth signals.
    
    States:
        PRE_MATCH: Match created but not started
        LIVE: Match actively in progress
        PAUSED: Match temporarily paused
        PENDING_CONFIRM: Match ended, awaiting confirmation
        FINAL: Outcome confirmed, immutable
        
    Features:
        - Deterministic and idempotent
        - Multi-source confirmation
        - Tolerant to out-of-order events
    """

    def __init__(
        self,
        match_id: str,
        team_a_id: str,
        team_b_id: str,
        config: TruthEngineConfig,
    ):
        self._config = config
        self._state = TruthState(
            match_id=match_id,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
        )

    @property
    def state(self) -> TruthState:
        """Get current state (read-only copy)."""
        return copy.deepcopy(self._state)

    @property
    def status(self) -> TruthStatus:
        """Current status."""
        return self._state.status

    @property
    def confidence(self) -> float:
        """Current confidence level."""
        return self._state.confidence

    @property
    def is_live(self) -> bool:
        """Is match currently live?"""
        return self._state.status == TruthStatus.LIVE

    @property
    def is_paused(self) -> bool:
        """Is match currently paused?"""
        return self._state.status == TruthStatus.PAUSED

    @property
    def is_final(self) -> bool:
        """Is match finalized?"""
        return self._state.status == TruthStatus.FINAL

    @property
    def is_effectively_final(self) -> bool:
        """Is confidence high enough to treat as final?"""
        return (
            self._state.status in (TruthStatus.PENDING_CONFIRM, TruthStatus.FINAL)
            and self._state.confidence >= 0.85
        )

    @property
    def winner_if_final(self) -> Optional[str]:
        """Winner team ID if effectively final, else None."""
        if self.is_effectively_final:
            return self._state.winner_team_id
        return None

    def on_event(self, event: MatchEvent) -> Optional[Union[TruthDelta, TruthFinal]]:
        """
        Process an event and return signal if state changed.
        
        Args:
            event: Normalized match event
            
        Returns:
            TruthDelta if state changed, TruthFinal if finalized, None otherwise
        """
        # Dedup check
        if self._is_duplicate(event):
            logger.debug(
                "Duplicate event ignored",
                event_type="duplicate_event",
                match_id=self._state.match_id,
                source_event_id=event.source_event_id,
            )
            return None

        # Ordering check
        if self._is_out_of_order(event):
            logger.warning(
                "Out of order event dropped",
                event_type="out_of_order_event",
                match_id=self._state.match_id,
                event_ts=event.timestamp_ms,
                last_ts=self._state.last_event_ms,
            )
            return None

        # Update last event timestamp
        self._state.last_event_ms = max(self._state.last_event_ms, event.timestamp_ms)

        # Dispatch to state-specific handler
        match self._state.status:
            case TruthStatus.PRE_MATCH:
                return self._on_event_pre_match(event)
            case TruthStatus.LIVE:
                return self._on_event_live(event)
            case TruthStatus.PAUSED:
                return self._on_event_paused(event)
            case TruthStatus.PENDING_CONFIRM:
                return self._on_event_pending(event)
            case TruthStatus.FINAL:
                self._on_event_final(event)
                return None

        return None

    def tick(self, now_ms: int) -> Optional[TruthFinal]:
        """
        Check for timeout-based finalization.
        
        Should be called periodically (e.g., on ClockTick).
        """
        if self._state.status != TruthStatus.PENDING_CONFIRM:
            return None

        if self._state.ended_at_ms is None:
            return None

        elapsed = now_ms - self._state.ended_at_ms
        if elapsed >= self._config.max_wait_ms:
            logger.info(
                "Finalizing on timeout",
                event_type="finalize_timeout",
                match_id=self._state.match_id,
                elapsed_ms=elapsed,
            )
            return self._finalize()

        return None

    # =========================================================================
    # State Handlers
    # =========================================================================

    def _on_event_pre_match(self, event: MatchEvent) -> Optional[TruthDelta]:
        """Handle events in PRE_MATCH state."""
        match event.event_type:
            case MatchEventType.MATCH_STARTED:
                self._state.status = TruthStatus.LIVE
                logger.info(
                    "Match started",
                    event_type="status_change",
                    match_id=self._state.match_id,
                    new_status="LIVE",
                )
                return TruthDelta(
                    match_id=self._state.match_id,
                    delta_type="status",
                    old_value="PRE_MATCH",
                    new_value="LIVE",
                    sources=[event.source],
                )

            case MatchEventType.PAUSED:
                # Rare but allowed (pre-match technical issues)
                self._state.status = TruthStatus.PAUSED
                return TruthDelta(
                    match_id=self._state.match_id,
                    delta_type="status",
                    old_value="PRE_MATCH",
                    new_value="PAUSED",
                    sources=[event.source],
                )

            case _:
                # Ignore other events until match starts
                return None

    def _on_event_live(self, event: MatchEvent) -> Optional[Union[TruthDelta, TruthFinal]]:
        """Handle events in LIVE state."""
        match event.event_type:
            case MatchEventType.PAUSED:
                self._state.status = TruthStatus.PAUSED
                return TruthDelta(
                    match_id=self._state.match_id,
                    delta_type="status",
                    old_value="LIVE",
                    new_value="PAUSED",
                    sources=[event.source],
                )

            case MatchEventType.SCORE_UPDATE:
                old_score = (self._state.score_a, self._state.score_b)
                self._state.score_a = event.payload.get("team_a_score", self._state.score_a)
                self._state.score_b = event.payload.get("team_b_score", self._state.score_b)
                new_score = (self._state.score_a, self._state.score_b)

                if old_score != new_score:
                    return TruthDelta(
                        match_id=self._state.match_id,
                        delta_type="score",
                        old_value=old_score,
                        new_value=new_score,
                        sources=[event.source],
                    )

            case MatchEventType.ROUND_ENDED:
                self._state.round_index = event.payload.get("round_index", self._state.round_index)
                winner = event.payload.get("winner_team_id")
                return TruthDelta(
                    match_id=self._state.match_id,
                    delta_type="round",
                    new_value=winner,
                    confidence=0.6,
                    sources=[event.source],
                )

            case MatchEventType.MAP_ENDED:
                old_map = self._state.map_index
                self._state.map_index = event.payload.get("map_index", self._state.map_index)
                winner = event.payload.get("winner_team_id")
                return TruthDelta(
                    match_id=self._state.match_id,
                    delta_type="map",
                    old_value=old_map,
                    new_value={"map_index": self._state.map_index, "winner": winner},
                    confidence=0.75,
                    sources=[event.source],
                )

            case MatchEventType.MATCH_ENDED:
                return self._enter_pending_confirm(event)

        return None

    def _on_event_paused(self, event: MatchEvent) -> Optional[Union[TruthDelta, TruthFinal]]:
        """Handle events in PAUSED state."""
        match event.event_type:
            case MatchEventType.RESUMED:
                self._state.status = TruthStatus.LIVE
                return TruthDelta(
                    match_id=self._state.match_id,
                    delta_type="status",
                    old_value="PAUSED",
                    new_value="LIVE",
                    sources=[event.source],
                )

            case MatchEventType.MATCH_ENDED:
                return self._enter_pending_confirm(event)

            case _:
                # Ignore other events while paused
                return None

    def _on_event_pending(self, event: MatchEvent) -> Optional[Union[TruthDelta, TruthFinal]]:
        """Handle events in PENDING_CONFIRM state."""
        if event.event_type != MatchEventType.MATCH_ENDED:
            return None

        winner = event.payload.get("winner_team_id")

        if winner == self._state.winner_team_id:
            # Consistent confirmation
            self._add_confirmation(event.source, event.source_tier)

            if self._should_finalize():
                return self._finalize()
        else:
            # Contradiction! Revert to LIVE
            logger.warning(
                "Contradiction detected, reverting to LIVE",
                event_type="contradiction",
                match_id=self._state.match_id,
                expected_winner=self._state.winner_team_id,
                received_winner=winner,
                source=event.source,
            )

            self._state.status = TruthStatus.LIVE
            self._state.winner_team_id = None
            self._state.confidence = 0.0
            self._state.sources_confirming.clear()
            self._state.ended_at_ms = None

            return TruthDelta(
                match_id=self._state.match_id,
                delta_type="status",
                old_value="PENDING_CONFIRM",
                new_value="LIVE",
                reason="contradiction",
                sources=[event.source],
            )

        return None

    def _on_event_final(self, event: MatchEvent) -> None:
        """Handle events in FINAL state (mostly ignored)."""
        if event.event_type == MatchEventType.CORRECTION:
            logger.warning(
                "Correction received for finalized match",
                event_type="post_final_correction",
                match_id=self._state.match_id,
                payload=event.payload,
            )
        # All other events ignored in FINAL state

    # =========================================================================
    # Helpers
    # =========================================================================

    def _enter_pending_confirm(self, event: MatchEvent) -> TruthDelta:
        """Transition to PENDING_CONFIRM state."""
        self._state.status = TruthStatus.PENDING_CONFIRM
        self._state.winner_team_id = event.payload.get("winner_team_id")
        self._state.ended_at_ms = event.timestamp_ms
        self._state.sources_confirming = {event.source}

        # Initial confidence based on source tier
        if event.source_tier == DataSourceTier.TIER_A:
            self._state.confidence = 0.90
        elif event.source_tier == DataSourceTier.TIER_B:
            self._state.confidence = 0.80
        else:
            self._state.confidence = 0.70

        logger.info(
            "Match ended, pending confirmation",
            event_type="status_change",
            match_id=self._state.match_id,
            new_status="PENDING_CONFIRM",
            winner=self._state.winner_team_id,
            confidence=self._state.confidence,
            source=event.source,
        )

        return TruthDelta(
            match_id=self._state.match_id,
            delta_type="status",
            old_value="LIVE",
            new_value="PENDING_CONFIRM",
            confidence=self._state.confidence,
            sources=[event.source],
        )

    def _add_confirmation(self, source: str, tier: DataSourceTier) -> None:
        """Add a confirmation source and boost confidence."""
        if source in self._state.sources_confirming:
            return  # Already counted

        self._state.sources_confirming.add(source)

        # Boost based on source tier
        if tier == DataSourceTier.TIER_A:
            self._state.confidence = min(1.0, self._state.confidence + 0.10)
        elif tier == DataSourceTier.TIER_B:
            self._state.confidence = min(0.95, self._state.confidence + 0.08)
        else:
            self._state.confidence = min(0.90, self._state.confidence + 0.03)

        logger.debug(
            "Confirmation added",
            event_type="confirmation_added",
            match_id=self._state.match_id,
            source=source,
            tier=tier.value,
            new_confidence=self._state.confidence,
            total_sources=len(self._state.sources_confirming),
        )

    def _should_finalize(self) -> bool:
        """Determine if we should finalize."""
        # High confidence threshold
        if self._state.confidence >= self._config.confirm_threshold:
            return True

        # Tier-A source confirmed
        tier_a_confirmed = any(
            source in self._config.tier_a_sources
            for source in self._state.sources_confirming
        )
        if tier_a_confirmed:
            return True

        # Multiple sources agree
        if len(self._state.sources_confirming) >= self._config.required_sources_for_final:
            return True

        return False

    def _finalize(self) -> TruthFinal:
        """Finalize the match result."""
        self._state.status = TruthStatus.FINAL
        self._state.finalized_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        logger.info(
            "Match finalized",
            event_type="match_finalized",
            match_id=self._state.match_id,
            winner=self._state.winner_team_id,
            confidence=self._state.confidence,
            sources=list(self._state.sources_confirming),
        )

        return TruthFinal(
            match_id=self._state.match_id,
            winner_team_id=self._state.winner_team_id or "",
            confidence=self._state.confidence,
            confirmed_by=list(self._state.sources_confirming),
            finalized_at_ms=self._state.finalized_at_ms,
        )

    def _is_duplicate(self, event: MatchEvent) -> bool:
        """Check if event is a duplicate."""
        # Method 1: Source provides event ID
        if event.source_event_id:
            if event.source_event_id in self._state.seen_event_ids:
                return True
            self._state.seen_event_ids.add(event.source_event_id)
            return False

        # Method 2: Hash-based dedup
        event_hash = self._hash_event(event)
        if event_hash in self._state.seen_event_ids:
            return True
        self._state.seen_event_ids.add(event_hash)
        return False

    def _hash_event(self, event: MatchEvent) -> str:
        """Generate hash for event deduplication."""
        content = f"{event.event_type.value}:{event.timestamp_ms}:{json.dumps(event.payload, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _is_out_of_order(self, event: MatchEvent) -> bool:
        """Check if event is out of order."""
        # Method 1: Sequence numbers
        if event.seq is not None and self._state.last_seq is not None:
            if event.seq <= self._state.last_seq:
                return True
            self._state.last_seq = event.seq
            return False

        # Method 2: Timestamp-based (with skew tolerance)
        if event.timestamp_ms < self._state.last_event_ms - self._config.allowed_skew_ms:
            return True

        return False

    def get_state_snapshot(self) -> TruthState:
        """Return immutable copy of current state."""
        return copy.deepcopy(self._state)
