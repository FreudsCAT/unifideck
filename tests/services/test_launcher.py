"""Tests for services/launcher/service.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from unifideck.services.launcher.service import LauncherService

@pytest.fixture
def bus():
    return MagicMock()

@pytest.fixture
def service(bus):
    shortcut_svc = MagicMock()
    proton_svc = MagicMock()
    cloud_svc = MagicMock()
    edge_browser = MagicMock()
    launch_history = MagicMock()
    
    return LauncherService(
        bus, shortcut_svc, proton_svc, cloud_svc, edge_browser,
        launch_history=launch_history
    )

@pytest.mark.asyncio
async def test_launch_timer(service):
    # Mock platform-specific launch to return a result
    from unifideck.core.types import Result
    service._launch_native = AsyncMock(return_value=Result(success=True, rc=0))
    service._check_circuit_breaker = AsyncMock(return_value=False)
    
    ctx = MagicMock()
    ctx.is_xcloud = False
    ctx.is_windows_game = False
    ctx.game = {"title": "Native Game"}
    ctx.env = {}
    
    res = await service.launch(ctx)
    
    assert res.success is True
    assert res.elapsed >= 0.0
    assert service._launch_started_at is not None

@pytest.mark.asyncio
async def test_circuit_breaker_prevents_launch(service):
    from unifideck.core.types import Result
    service._check_circuit_breaker = AsyncMock(return_value=True)
    
    ctx = MagicMock()
    res = await service.launch(ctx)
    
    assert res.success is False
    assert res.error == "circuit_open"
