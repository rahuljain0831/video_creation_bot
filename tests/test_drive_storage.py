"""Tests for pipeline/drive_storage.py"""
import io
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest


def test_upload_to_drive_returns_file_id(tmp_path):
    """Mock folder lookup + file create."""
    from pipeline import drive_storage

    # Create a temporary file to upload
    local_file = tmp_path / "test_video.mp4"
    local_file.write_text("fake video data")

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock folder lookup
        mock_service.files().list().execute.return_value = {"files": [{"id": "folder_123"}]}

        # Mock file creation
        mock_service.files().create().execute.return_value = {"id": "file_456"}

        result = drive_storage.upload_to_drive(local_file, folder_name="pending")

        assert result == "file_456"
        # Verify that list and create were called
        mock_service.files().list.assert_called()
        mock_service.files().create.assert_called()


def test_upload_to_drive_file_not_found():
    """Test that FileNotFoundError is raised for non-existent file."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service"):
        with pytest.raises(FileNotFoundError):
            drive_storage.upload_to_drive(Path("/nonexistent/file.mp4"))


def test_download_from_drive_writes_file(tmp_path):
    """Mock MediaIoBaseDownload."""
    from pipeline import drive_storage

    dest_file = tmp_path / "downloaded.mp4"

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock get_media request
        mock_request = MagicMock()
        mock_service.files().get_media.return_value = mock_request

        # Mock MediaIoBaseDownload to simulate file download
        with patch("pipeline.drive_storage.MediaIoBaseDownload") as mock_download_class:
            mock_downloader = MagicMock()
            mock_download_class.return_value = mock_downloader
            # Simulate download complete on first chunk
            mock_downloader.next_chunk.side_effect = [
                (MagicMock(progress=lambda: 100), True),  # (status, done)
            ]

            result = drive_storage.download_from_drive("file_123", dest_file)

            assert result == dest_file
            mock_service.files().get_media.assert_called_with(fileId="file_123")
            mock_download_class.assert_called_once()


def test_move_drive_file():
    """Mock get parents + update."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock get to retrieve current parents
        mock_service.files().get().execute.return_value = {"parents": ["folder_old_123"]}

        # Mock list to get destination folder
        mock_service.files().list().execute.return_value = {"files": [{"id": "folder_new_456"}]}

        # Mock update
        mock_service.files().update().execute.return_value = {"id": "file_789", "parents": ["folder_new_456"]}

        drive_storage.move_drive_file("file_789", "uploaded")

        # Verify get was called
        mock_service.files().get.assert_called_with(fileId="file_789", fields="parents")

        # Verify update was called with correct parameters
        mock_service.files().update.assert_called_with(
            fileId="file_789",
            addParents="folder_new_456",
            removeParents="folder_old_123",
            fields="id, parents",
        )


def test_delete_old_files():
    """Mock list + delete."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Track list calls: root folder, subfolder, then old files query
        list_responses = [
            {"files": [{"id": "root_123"}]},  # For _get_subfolder (root lookup)
            {"files": [{"id": "pending_456"}]},  # For _get_subfolder (subfolder lookup)
            {"files": [{"id": "file_1"}, {"id": "file_2"}]},  # For delete_old_files query
        ]
        list_call_count = [0]

        def list_side_effect(*args, **kwargs):
            result = list_responses[list_call_count[0]]
            list_call_count[0] += 1
            mock_list_obj = MagicMock()
            mock_list_obj.execute.return_value = result
            return mock_list_obj

        mock_service.files.return_value.list = list_side_effect

        # Set up delete calls to return immediately
        delete_mock = MagicMock()
        delete_mock.execute.return_value = {}
        mock_service.files.return_value.delete.return_value = delete_mock

        result = drive_storage.delete_old_files("pending", older_than_days=7)

        assert result == 2
        # Verify delete was called twice
        assert mock_service.files.return_value.delete.call_count == 2


def test_get_or_create_folder_existing():
    """Test retrieving an existing folder."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock list to return existing folder
        list_call = MagicMock()
        list_call.execute.return_value = {"files": [{"id": "existing_folder_123"}]}
        mock_service.files.return_value.list.return_value = list_call

        result = drive_storage._get_or_create_folder("test_folder")

        assert result == "existing_folder_123"
        # Verify list was called
        mock_service.files.return_value.list.assert_called()
        # Verify create was not called
        mock_service.files.return_value.create.assert_not_called()


def test_get_or_create_folder_create():
    """Test creating a new folder."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock list to return no results
        list_call = MagicMock()
        list_call.execute.return_value = {"files": []}
        mock_service.files.return_value.list.return_value = list_call

        # Mock create to return new folder
        create_call = MagicMock()
        create_call.execute.return_value = {"id": "new_folder_456"}
        mock_service.files.return_value.create.return_value = create_call

        result = drive_storage._get_or_create_folder("test_folder")

        assert result == "new_folder_456"
        mock_service.files.return_value.create.assert_called()


def test_get_or_create_folder_with_parent():
    """Test creating a folder with parent."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock list to return no results
        mock_service.files().list().execute.return_value = {"files": []}

        # Mock create
        mock_service.files().create().execute.return_value = {"id": "new_folder_456"}

        result = drive_storage._get_or_create_folder("subfolder", parent_id="parent_123")

        assert result == "new_folder_456"
        # Verify create was called with parent
        call_args = mock_service.files().create.call_args
        assert call_args[1]["body"]["parents"] == ["parent_123"]


def test_get_subfolder():
    """Test getting or creating video-uploads/<subfolder>."""
    from pipeline import drive_storage

    with patch.object(drive_storage, "_build_service") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock list to return root folder on first call, subfolder on second
        list_call_1 = MagicMock()
        list_call_1.execute.return_value = {"files": [{"id": "root_123"}]}

        list_call_2 = MagicMock()
        list_call_2.execute.return_value = {"files": [{"id": "sub_456"}]}

        mock_service.files.return_value.list.side_effect = [list_call_1, list_call_2]

        result = drive_storage._get_subfolder("pending")

        assert result == "sub_456"
        assert mock_service.files.return_value.list.call_count == 2


def test_build_service_caching():
    """Test that _build_service caches the service object."""
    from pipeline import drive_storage

    # Reset cache
    drive_storage._service_cache = None

    with patch.dict("os.environ", {"GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON": "/fake/creds.json"}):
        with patch("pipeline.drive_storage.Credentials.from_service_account_file") as mock_creds:
            with patch("pipeline.drive_storage.build") as mock_build:
                mock_creds.return_value = MagicMock()
                mock_service = MagicMock()
                mock_build.return_value = mock_service

                # First call should build service
                result1 = drive_storage._build_service()
                assert result1 == mock_service
                assert mock_build.call_count == 1

                # Second call should return cached service
                result2 = drive_storage._build_service()
                assert result2 == mock_service
                assert mock_build.call_count == 1  # Still 1, not 2

    # Reset cache for other tests
    drive_storage._service_cache = None


def test_build_service_env_var_missing():
    """Test that _build_service raises ValueError if env var not set."""
    from pipeline import drive_storage

    drive_storage._service_cache = None

    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON"):
            drive_storage._build_service()

    # Reset cache
    drive_storage._service_cache = None
