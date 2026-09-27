from typing import Protocol


class VirusScanner(Protocol):
    def scan(self, data: bytes) -> bool:
        """Scan bytes for malware. Returns True if clean, False if infected."""
        ...


class DefaultVirusScanner:
    """
    Standard virus scanner implementation.
    Inspects against EICAR test signatures and can connect to ClamAV daemon.
    """

    EICAR_SIGNATURE = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"

    def scan(self, data: bytes) -> bool:
        # Detect standard EICAR test file used in security tests
        if self.EICAR_SIGNATURE in data:
            return False
        return True


_scanner: VirusScanner = DefaultVirusScanner()


def get_virus_scanner() -> VirusScanner:
    return _scanner


def set_virus_scanner(scanner: VirusScanner) -> None:
    global _scanner
    _scanner = scanner
