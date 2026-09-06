"""Public exception hierarchy for Madako."""


class DataProfileError(Exception):
    """Base class for errors callers are expected to handle."""


class ProfilingError(DataProfileError):
    """Backward-compatible base for planning and profiling errors."""


class PlanningError(ProfilingError):
    """A profile plan cannot be created or safely executed."""


class WarehouseError(ProfilingError):
    """The warehouse could not estimate or execute a query."""


class ResultValidationError(ProfilingError):
    """Warehouse rows do not satisfy the profiling result contract."""


class StorageError(DataProfileError):
    """Profile storage could not be read or written safely."""


class StorageOperationError(StorageError, OSError):
    """A storage implementation failed while loading or saving data."""


class StorageFormatError(StorageError, ValueError):
    """Stored data uses an invalid, ambiguous, or unsupported format."""
