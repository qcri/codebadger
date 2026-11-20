"""Reading container memory must not wait for Docker's CPU sampling interval."""

from unittest.mock import MagicMock

from src.services.joern_server_manager import JoernServerManager


def test_container_memory_uses_a_single_stats_sample():
    manager = JoernServerManager.__new__(JoernServerManager)  # bypass Docker/Redis setup
    container = MagicMock()
    container.stats.return_value = {"memory_stats": {"usage": 64 * 1024 * 1024}}
    manager.docker_client = MagicMock()
    manager.docker_client.containers.get.return_value = container
    manager.container_name = "codebadger-joern-server"

    assert manager._container_memory_mb() == 64
    container.stats.assert_called_once_with(stream=False, one_shot=True)
