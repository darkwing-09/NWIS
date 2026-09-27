from fastapi import UploadFile

from config.errors import (
    FileTooLargeError,
    UnsupportedFileTypeError,
    VirusScanFailedError,
)
from infrastructure.security.scanner import get_virus_scanner


def validate_upload(file: UploadFile, max_size_mb: int = 20) -> None:
    """
    Validate uploaded document prior to persistence or storage write.
    Enforces PDF format, magic byte signature (%PDF-), size limit, and virus scan.
    """
    max_bytes = max_size_mb * 1024 * 1024

    # 1. Content-Type check
    content_type = (file.content_type or "").lower().strip()
    if content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise UnsupportedFileTypeError(
            "Only PDF documents are supported",
            detail={"filename": file.filename, "content_type": file.content_type},
        )

    # 2. Read content to inspect size and magic bytes
    content = file.file.read()
    file_size = len(content)

    # Reset stream pointer
    file.file.seek(0)

    # 3. Size check
    if file_size > max_bytes:
        raise FileTooLargeError(
            f"File exceeds maximum permitted size of {max_size_mb} MB",
            detail={"file_size_bytes": file_size, "max_allowed_bytes": max_bytes},
        )

    # 4. Magic-byte verification (%PDF-)
    if not content.startswith(b"%PDF-"):
        raise UnsupportedFileTypeError(
            "Invalid file content: missing standard PDF header (%PDF-)",
            detail={"filename": file.filename},
        )

    # 5. Antivirus scan
    scanner = get_virus_scanner()
    is_clean = scanner.scan(content)
    if not is_clean:
        raise VirusScanFailedError(
            "File rejected by virus and malware security scan",
            detail={"filename": file.filename},
        )
