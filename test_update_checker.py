import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock
from collections import namedtuple

# Mock decky before imports
class MockLogger:
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARN: {msg}")
    def error(self, msg): print(f"ERROR: {msg}")

decky_mock = MagicMock()
decky_mock.logger = MockLogger()
sys.modules['decky'] = decky_mock

# Add plugin to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from py_modules.unifideck.download.update_checker import UpdateChecker

class MockPlugin:
    def __init__(self):
        self.epic = MagicMock()
        self.epic.check_for_updates = AsyncMock()
        
        self.gog = MagicMock()
        self.gog.check_for_game_update = AsyncMock()
        
        self.amazon = MagicMock()
        self.amazon.check_for_updates = AsyncMock()

async def test_update_checker():
    print("Running UpdateChecker tests...")
    plugin = MockPlugin()
    checker = UpdateChecker(plugin=plugin)
    
    # Test 1: Check Epic games
    plugin.epic.check_for_updates.return_value = ["gameA", "gameB"]
    
    # First check should call the API
    has_update_a = await checker.check_for_game_update("epic", "gameA")
    assert has_update_a == True
    plugin.epic.check_for_updates.assert_called_once()
    
    # Second check for same game should use cache
    has_update_a_cached = await checker.check_for_game_update("epic", "gameA")
    assert has_update_a_cached == True
    plugin.epic.check_for_updates.assert_called_once()  # Call count shouldn't increase
    
    # Check for another game returned in the batch
    has_update_b = await checker.check_for_game_update("epic", "gameB")
    assert has_update_b == True
    plugin.epic.check_for_updates.assert_called_once()
    
    # Check for a game NOT in the batch update
    has_update_c = await checker.check_for_game_update("epic", "gameC")
    assert has_update_c == False
    plugin.epic.check_for_updates.assert_called_once()
    
    print("✅ Epic batch caching works")
    
    # Test 2: Check GOG games
    plugin.gog.check_for_game_update.return_value = True
    
    has_gog_update = await checker.check_for_game_update("gog", "gogGame1")
    assert has_gog_update == True
    plugin.gog.check_for_game_update.assert_called_once_with("gogGame1")
    
    # This shouldn't have affected the epic cache or triggered epic call
    plugin.epic.check_for_updates.assert_called_once()
    
    print("✅ GOG individual caching works and doesn't pollute Epic TTL")
    
    # Test 3: Clear cache
    checker.clear_cache_for_game("epic", "gameA")
    
    # This should trigger a new API call
    plugin.epic.check_for_updates.return_value = ["gameB"] # gameA no longer has update
    has_update_a_cleared = await checker.check_for_game_update("epic", "gameA")
    assert has_update_a_cleared == False
    assert plugin.epic.check_for_updates.call_count == 2
    
    print("✅ Cache clearing works")
    print("All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_update_checker())
