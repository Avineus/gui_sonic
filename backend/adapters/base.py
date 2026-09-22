class CommandError(RuntimeError):
    """Raised by any adapter when the underlying gNMI/gRPC/CLI call fails."""
