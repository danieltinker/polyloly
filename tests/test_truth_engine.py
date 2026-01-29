"""Tests for the Truth Engine."""

from __future__ import annotations

import pytest

from src.bot.settings import TruthEngineConfig
from src.domain.engines.truth_engine import MatchEvent, TruthDelta, TruthEngine, TruthFinal
from src.domain.types import DataSourceTier, MatchEventType, TruthStatus


@pytest.fixture
def config():
    """Provide truth engine config."""
    return TruthEngineConfig(
        confirm_threshold=0.90,
        max_wait_ms=10000,
        required_sources_for_final=2,
        allowed_skew_ms=2000,
        tier_a_sources=["grid", "official"],
        tier_b_sources=["opendota", "pandascore"],
        tier_c_sources=["liquipedia"],
    )


@pytest.fixture
def engine(config):
    """Provide a truth engine instance."""
    return TruthEngine(
        match_id="test_match_1",
        team_a_id="team_a",
        team_b_id="team_b",
        config=config,
    )


class TestTruthEngineStates:
    """Test state transitions."""

    def test_initial_state_is_pre_match(self, engine):
        """Engine starts in PRE_MATCH state."""
        assert engine.status == TruthStatus.PRE_MATCH
        assert engine.confidence == 0.0
        assert not engine.is_live
        assert not engine.is_final

    def test_pre_match_to_live_on_match_started(self, engine):
        """PRE_MATCH -> LIVE on MATCH_STARTED event."""
        event = MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        )

        result = engine.on_event(event)

        assert engine.status == TruthStatus.LIVE
        assert engine.is_live
        assert isinstance(result, TruthDelta)
        assert result.delta_type == "status"
        assert result.new_value == "LIVE"

    def test_live_to_paused(self, engine):
        """LIVE -> PAUSED on PAUSED event."""
        # First go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # Then pause
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.PAUSED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=2000,
        ))

        assert engine.status == TruthStatus.PAUSED
        assert engine.is_paused
        assert isinstance(result, TruthDelta)
        assert result.new_value == "PAUSED"

    def test_paused_to_live_on_resumed(self, engine):
        """PAUSED -> LIVE on RESUMED event."""
        # Go to LIVE then PAUSED
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.PAUSED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=2000,
        ))

        # Resume
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.RESUMED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=3000,
        ))

        assert engine.status == TruthStatus.LIVE
        assert isinstance(result, TruthDelta)
        assert result.new_value == "LIVE"

    def test_live_to_pending_on_match_ended(self, engine):
        """LIVE -> PENDING_CONFIRM on MATCH_ENDED event."""
        # Go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # Match ends
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=5000,
            payload={"winner_team_id": "team_a"},
        ))

        assert engine.status == TruthStatus.PENDING_CONFIRM
        assert engine.confidence == 0.80  # Tier B initial confidence
        assert isinstance(result, TruthDelta)
        assert result.new_value == "PENDING_CONFIRM"


class TestConfirmation:
    """Test confirmation and finalization logic."""

    def test_tier_a_single_source_finalizes(self, engine):
        """A single Tier-A source can finalize the match."""
        # Go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # Match ends with Tier-A source
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="grid",  # Tier A
            source_tier=DataSourceTier.TIER_A,
            timestamp_ms=5000,
            payload={"winner_team_id": "team_a"},
        ))

        assert engine.status == TruthStatus.FINAL
        assert engine.is_final
        assert engine.winner_if_final == "team_a"
        assert isinstance(result, TruthFinal)
        assert result.winner_team_id == "team_a"

    def test_two_tier_b_sources_finalize(self, engine):
        """Two Tier-B sources agreeing can finalize the match."""
        # Go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # First MATCH_ENDED from Tier-B
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=5000,
            payload={"winner_team_id": "team_a"},
        ))

        assert engine.status == TruthStatus.PENDING_CONFIRM

        # Second confirmation from different Tier-B source
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="pandascore",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=5100,
            source_event_id="ps_123",  # Different event ID
            payload={"winner_team_id": "team_a"},
        ))

        assert engine.status == TruthStatus.FINAL
        assert isinstance(result, TruthFinal)

    def test_contradiction_reverts_to_live(self, engine):
        """Contradicting winner reverts to LIVE state."""
        # Go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # First MATCH_ENDED says team_a wins
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=5000,
            payload={"winner_team_id": "team_a"},
        ))

        # Contradicting event says team_b wins
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="pandascore",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=5100,
            source_event_id="ps_456",
            payload={"winner_team_id": "team_b"},  # Different winner!
        ))

        assert engine.status == TruthStatus.LIVE  # Reverted
        assert engine.confidence == 0.0
        assert isinstance(result, TruthDelta)
        assert result.reason == "contradiction"

    def test_timeout_finalization(self, engine):
        """Match finalizes after timeout in PENDING_CONFIRM."""
        # Go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # Match ends
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_ENDED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=5000,
            payload={"winner_team_id": "team_a"},
        ))

        assert engine.status == TruthStatus.PENDING_CONFIRM

        # Tick with enough time passed (> 10000ms)
        result = engine.tick(now_ms=16000)

        assert engine.status == TruthStatus.FINAL
        assert isinstance(result, TruthFinal)


class TestDeduplication:
    """Test event deduplication."""

    def test_duplicate_event_ignored(self, engine):
        """Duplicate events are ignored."""
        event = MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
            source_event_id="event_123",
        )

        # First event processed
        result1 = engine.on_event(event)
        assert result1 is not None
        assert engine.status == TruthStatus.LIVE

        # Duplicate ignored
        result2 = engine.on_event(event)
        assert result2 is None


class TestScoreUpdates:
    """Test score update events."""

    def test_score_update_in_live(self, engine):
        """Score updates are processed in LIVE state."""
        # Go to LIVE
        engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.MATCH_STARTED,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=1000,
        ))

        # Score update
        result = engine.on_event(MatchEvent(
            match_id="test_match_1",
            event_type=MatchEventType.SCORE_UPDATE,
            source="opendota",
            source_tier=DataSourceTier.TIER_B,
            timestamp_ms=2000,
            source_event_id="score_1",
            payload={"team_a_score": 1, "team_b_score": 0},
        ))

        state = engine.get_state_snapshot()
        assert state.score_a == 1
        assert state.score_b == 0
        assert isinstance(result, TruthDelta)
        assert result.delta_type == "score"
