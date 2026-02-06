"""Test the directory cache functionality"""

from pathlib import Path

import pytest

from porringer.backend.cache import DirectoryCacheManager

# Test constants
TWO_DIRECTORIES = 2


class TestDirectoryCacheManager:
    """Tests for DirectoryCacheManager"""

    @staticmethod
    def test_add_directory(cache_manager, temp_cache_dir) -> None:
        """Test adding a directory to the cache"""
        tmp_path, _ = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        result = cache_manager.add_directory(target_dir, name='My Project')

        assert result.path == target_dir.resolve()
        assert result.name == 'My Project'

        # Verify persistence
        assert cache_manager.cache_path.exists()
        directories = cache_manager.list_directories()
        assert len(directories) == 1
        assert directories[0].path == target_dir.resolve()

    @staticmethod
    def test_add_duplicate_directory_fails(cache_manager, temp_cache_dir) -> None:
        """Test that adding duplicate directory raises error"""
        tmp_path, _ = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        cache_manager.add_directory(target_dir)

        with pytest.raises(ValueError, match='already registered'):
            cache_manager.add_directory(target_dir)

    @staticmethod
    def test_add_nonexistent_directory_fails(cache_manager, temp_cache_dir) -> None:
        """Test that adding nonexistent path raises error when validating"""
        tmp_path, _ = temp_cache_dir
        nonexistent = tmp_path / 'does_not_exist'

        with pytest.raises(ValueError, match='does not exist'):
            cache_manager.add_directory(nonexistent)

    @staticmethod
    def test_remove_directory(cache_manager, temp_cache_dir) -> None:
        """Test removing a directory from the cache"""
        tmp_path, _ = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        cache_manager.add_directory(target_dir)

        assert len(cache_manager.list_directories()) == 1

        result = cache_manager.remove_directory(target_dir)
        assert result is True
        assert len(cache_manager.list_directories()) == 0

    @staticmethod
    def test_remove_nonexistent_directory(cache_manager) -> None:
        """Test removing a directory that's not in cache"""
        result = cache_manager.remove_directory(Path('/nonexistent'))
        assert result is False

    @staticmethod
    def test_list_directories_empty(cache_manager) -> None:
        """Test listing when cache is empty"""
        directories = cache_manager.list_directories()
        assert directories == []

    @staticmethod
    def test_get_paths(cache_manager, temp_cache_dir) -> None:
        """Test getting just paths from directories"""
        tmp_path, _ = temp_cache_dir
        dir1 = tmp_path / 'project1'
        dir2 = tmp_path / 'project2'
        dir1.mkdir()
        dir2.mkdir()

        cache_manager.add_directory(dir1)
        cache_manager.add_directory(dir2)

        paths = cache_manager.get_paths()

        assert len(paths) == TWO_DIRECTORIES
        assert dir1.resolve() in paths
        assert dir2.resolve() in paths


class TestDirectoryCachePersistence:
    """Tests for cache persistence"""

    @staticmethod
    def test_cache_persists_across_instances(temp_cache_dir) -> None:
        """Test that cache data persists when creating new manager instance"""
        tmp_path, data_dir = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        # First manager adds data
        manager1 = DirectoryCacheManager(data_dir)
        manager1.add_directory(target_dir, name='Test')

        # Second manager reads persisted data
        manager2 = DirectoryCacheManager(data_dir)
        directories = manager2.list_directories()

        assert len(directories) == 1
        assert directories[0].name == 'Test'

    @staticmethod
    def test_clear_removes_all_data(cache_manager, temp_cache_dir) -> None:
        """Test that clear removes all directories"""
        tmp_path, _ = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        cache_manager.add_directory(target_dir)

        cache_manager.clear()

        assert len(cache_manager.list_directories()) == 0


class TestDirectoryCacheValidation:
    """Tests for directory validation"""

    @staticmethod
    def test_validate_directories(cache_manager, temp_cache_dir) -> None:
        """Test validation of directories"""
        tmp_path, _ = temp_cache_dir
        existing_dir = tmp_path / 'existing'
        existing_dir.mkdir()

        cache_manager.add_directory(existing_dir)

        # Add directory that we'll delete
        to_delete = tmp_path / 'to_delete'
        to_delete.mkdir()
        cache_manager.add_directory(to_delete)

        # Delete it
        to_delete.rmdir()

        # Validate
        invalid = cache_manager.validate_directories()

        assert len(invalid) == 1
        assert invalid[0][0].path == to_delete.resolve()
        assert 'does not exist' in invalid[0][1]

    @staticmethod
    def test_update_directory(cache_manager, temp_cache_dir) -> None:
        """Test updating directory metadata"""
        tmp_path, _ = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        cache_manager.add_directory(target_dir, name='Original')

        # Update name
        updated = cache_manager.update_directory(target_dir, name='New Name')
        assert updated is not None
        assert updated.name == 'New Name'
