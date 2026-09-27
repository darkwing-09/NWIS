from typing import Any, Dict, Optional


class NWISError(Exception):
    """Base for all domain errors. Never raise bare Exception anywhere in services/."""

    def __init__(
        self,
        message: str,
        code: str = "internal_error",
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}


class NotFoundError(NWISError):
    def __init__(self, message: str = "Resource not found", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="not_found", detail=detail)


class ValidationError(NWISError):
    def __init__(self, message: str = "Validation failed", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="validation_error", detail=detail)


class ConflictError(NWISError):
    def __init__(self, message: str = "Resource conflict or deduplication violation", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="conflict", detail=detail)


class ExternalServiceError(NWISError):
    def __init__(self, message: str = "External service error", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="external_service_error", detail=detail)


class AuthorizationError(NWISError):
    def __init__(self, message: str = "Forbidden", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="forbidden", detail=detail)


class AuthenticationError(NWISError):
    def __init__(self, message: str = "Unauthorized", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="unauthorized", detail=detail)


class CorruptFileError(NWISError):
    def __init__(self, message: str = "Corrupt file", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="corrupt_file", detail=detail)


class OCRProcessingError(ExternalServiceError):
    def __init__(self, message: str = "OCR processing failed", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, detail=detail)
        self.code = "ocr_processing_error"


class ModelNotApprovedError(NWISError):
    def __init__(self, message: str = "Model is not approved for active mode", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, code="model_not_approved", detail=detail)


class UnsupportedFileTypeError(ValidationError):
    def __init__(self, message: str = "Unsupported file type", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, detail=detail)
        self.code = "unsupported_file_type"


class FileTooLargeError(ValidationError):
    def __init__(self, message: str = "File exceeds maximum permitted size", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, detail=detail)
        self.code = "file_too_large"


class VirusScanFailedError(ValidationError):
    def __init__(self, message: str = "File failed virus scan security check", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, detail=detail)
        self.code = "virus_scan_failed"


class WellNotFoundError(NotFoundError):
    def __init__(self, message: str = "Well not found", detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message=message, detail=detail)
        self.code = "well_not_found"
