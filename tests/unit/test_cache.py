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
        """Test validation returns all directories with correct status"""
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

        # Validate — returns ALL directories, not just invalid ones
        results = cache_manager.validate_directories()

        assert len(results) == 2
        existing_result = next(r for r in results if r.directory.path == existing_dir.resolve())
        deleted_result = next(r for r in results if r.directory.path == to_delete.resolve())

        assert existing_result.exists is True
        assert deleted_result.exists is False
        assert deleted_result.has_manifest is None

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


class TestDirectoryCacheBackup:
    """Tests for the .bak backup mechanism"""

    @staticmethod
    def test_save_creates_backup(cache_manager, temp_cache_dir) -> None:
        """Test that saving creates a .bak file"""
        tmp_path, data_dir = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        # First add — creates the primary file (no backup yet, nothing to back up)
        cache_manager.add_directory(target_dir, name='First')
        backup_path = cache_manager.cache_path.with_suffix('.bak')

        # Second mutation triggers a backup of the first state
        target_dir2 = tmp_path / 'project2'
        target_dir2.mkdir()
        cache_manager.add_directory(target_dir2, name='Second')

        assert backup_path.exists()

    @staticmethod
    def test_load_falls_back_to_backup(temp_cache_dir) -> None:
        """Test recovery from backup when the primary cache is corrupt"""
        tmp_path, data_dir = temp_cache_dir
        target_dir = tmp_path / 'project'
        target_dir.mkdir()

        # Set up a valid state and ensure a backup is created
        manager = DirectoryCacheManager(data_dir)
        manager.add_directory(target_dir, name='Saved')

        # Create a second mutation so the backup contains the first valid state
        target_dir2 = tmp_path / 'project2'
        target_dir2.mkdir()
        manager.add_directory(target_dir2)

        # Corrupt the primary cache file
        manager.cache_path.write_text('NOT VALID JSON', encoding='utf-8')

        # New manager should recover from backup
        recovered = DirectoryCacheManager(data_dir)
        directories = recovered.list_directories()

        # Backup was taken before the second add, so it should have the first entry
        assert len(directories) == 1
        assert directories[0].name == 'Saved'

    @staticmethod
    def test_both_corrupt_gives_empty_cache(temp_cache_dir) -> None:
        """Test that corruption in both primary and backup yields an empty cache"""
        _, data_dir = temp_cache_dir

        primary = data_dir / DirectoryCacheManager.CACHE_FILENAME
        backup = primary.with_suffix('.bak')

        data_dir.mkdir(parents=True, exist_ok=True)
        primary.write_text('GARBAGE', encoding='utf-8')
        backup.write_text('ALSO GARBAGE', encoding='utf-8')

        manager = DirectoryCacheManager(data_dir)
        assert manager.list_directories() == []
