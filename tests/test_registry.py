from fastapi.testclient import TestClient

from backend.main import app
from backend.commands import sonic_interface_status, frr_show_ip_route

client = TestClient(app)


def test_list_commands_exposes_both_platforms():
    resp = client.get("/api/commands")
    assert resp.status_code == 200
    ids = {c["command_id"] for c in resp.json()}
    assert {"sonic.show_interface_status", "frr.show_ip_route"} <= ids


def test_sonic_interface_status(monkeypatch):
    monkeypatch.setattr(
        sonic_interface_status.gnmi_adapter,
        "gnmi_get",
        lambda path, origin="sonic-db": {"admin_status": "up", "oper_status": "up", "speed": "100000"},
    )
    resp = client.post("/api/commands/sonic.show_interface_status", json={"interface": "Ethernet0"})
    assert resp.status_code == 200
    assert resp.json() == {
        "interface": "Ethernet0",
        "admin_status": "up",
        "oper_status": "up",
        "speed": "100000",
    }


def test_frr_show_ip_route(monkeypatch):
    monkeypatch.setattr(
        frr_show_ip_route.exec_adapter,
        "run_json",
        lambda command, timeout=10.0: {"0.0.0.0/0": [{"protocol": "static"}]},
    )
    resp = client.post("/api/commands/frr.show_ip_route", json={"vrf": "default"})
    assert resp.status_code == 200
    assert resp.json()["routes"] == {"0.0.0.0/0": [{"protocol": "static"}]}
