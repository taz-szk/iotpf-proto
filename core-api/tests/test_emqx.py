def test_emqx_auth_valid_cn(client):
    resp = client.post("/emqx/auth", json={
        "username": "tenant-a-id:device-001",
        "clientid": "tenant-a-id:device-001",
        "peerhost": "192.168.1.100",
        "cert_common_name": "tenant-a-id:device-001",
    })
    assert resp.status_code == 200
    assert resp.json()["result"] == "allow"

def test_emqx_auth_invalid_cn_format(client):
    resp = client.post("/emqx/auth", json={
        "username": "invalid-no-colon",
        "clientid": "invalid",
        "peerhost": "192.168.1.100",
        "cert_common_name": "invalid-no-colon",
    })
    assert resp.status_code == 200
    assert resp.json()["result"] == "deny"

def test_emqx_acl_valid_telemetry_publish(client):
    resp = client.post("/emqx/acl", json={
        "username": "tenant-abc:device-001",
        "clientid": "tenant-abc:device-001",
        "topic": "/tenant-abc/devices/device-001/telemetry",
        "action": "publish",
        "peerhost": "192.168.1.100",
    })
    assert resp.status_code == 200
    assert resp.json()["result"] == "allow"

def test_emqx_acl_cross_tenant_denied(client):
    resp = client.post("/emqx/acl", json={
        "username": "tenant-abc:device-001",
        "clientid": "tenant-abc:device-001",
        "topic": "/tenant-xyz/devices/device-001/telemetry",
        "action": "publish",
        "peerhost": "192.168.1.100",
    })
    assert resp.status_code == 200
    assert resp.json()["result"] == "deny"

def test_emqx_acl_subscribe_commands(client):
    resp = client.post("/emqx/acl", json={
        "username": "tenant-abc:device-001",
        "clientid": "tenant-abc:device-001",
        "topic": "/tenant-abc/devices/device-001/commands",
        "action": "subscribe",
        "peerhost": "192.168.1.100",
    })
    assert resp.status_code == 200
    assert resp.json()["result"] == "allow"

def test_emqx_acl_publish_commands_denied(client):
    resp = client.post("/emqx/acl", json={
        "username": "tenant-abc:device-001",
        "clientid": "tenant-abc:device-001",
        "topic": "/tenant-abc/devices/device-001/commands",
        "action": "publish",
        "peerhost": "192.168.1.100",
    })
    assert resp.status_code == 200
    assert resp.json()["result"] == "deny"
