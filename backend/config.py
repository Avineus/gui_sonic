from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GUI_", env_file=".env")

    # --- SONIC gNMI target (sonic-gnmi container, telemetry port) ---
    sonic_gnmi_host: str = "127.0.0.1"
    sonic_gnmi_port: int = 8080
    sonic_gnmi_username: str = "admin"
    sonic_gnmi_password: str = "admin"
    sonic_gnmi_insecure: bool = True  # set False and configure TLS for anything but a lab box

    # --- FRR command execution ---
    # frr_local=True: gateway runs on the same box as FRR, invokes `vtysh` via subprocess.
    # frr_local=False: gateway is remote, SSHes to frr_host and runs vtysh there.
    frr_local: bool = True
    frr_host: str = "127.0.0.1"
    frr_ssh_port: int = 22
    frr_ssh_user: str = "frr"
    frr_ssh_key_path: str | None = None

    # --- FRR northbound gRPC plugin (built with --enable-grpc, loaded via `-M grpc`) ---
    # The plugin only binds localhost with no TLS today, so this is normally
    # 127.0.0.1 from a gateway running on the same box as the daemon, or the
    # far end of an SSH tunnel otherwise.
    frr_grpc_host: str = "127.0.0.1"
    frr_grpc_port: int = 50051  # plugin default; override with `-M grpc:<port>`


settings = Settings()
