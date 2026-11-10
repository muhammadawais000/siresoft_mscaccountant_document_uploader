"""Private file storage: files exist on disk but have no public URL."""

from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    """Filesystem storage that refuses to build URLs.

    Documents must be served through the permission-checked download view, so any code
    path that asks the storage for a URL is a bug and fails loudly. An S3-compatible
    backend can replace this later through the ``STORAGES`` setting.
    """

    def url(self, name: str | None) -> str:
        raise NotImplementedError(
            "Intake documents have no public URL; use the permission-checked file view."
        )
