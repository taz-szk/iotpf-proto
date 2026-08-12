# Simulator OTA Verification Feature Design

## Overview

Add OTA firmware update verification to the device simulator. When a device receives an OTA command via MQTT, it downloads the firmware file, verifies the SHA256 checksum, and simulates a version update — displaying detailed progress and results in a per-device dialog window.

## Requirements

- Real firmware download from the URL in the OTA command payload
- SHA256 checksum verification with both expected and computed values shown
- Progress bar showing download progress (bytes downloaded / total)
- Detailed timestamped log (download start, progress, checksum values, result)
- Per-device non-modal dialog (multiple devices can OTA simultaneously)
- Device card shows updated `fw_version` after completion
- Subsequent telemetry sends include the new `fw_version`

## Architecture

### Components

**`DeviceWorker` (modified)**
- New attribute: `fw_version: str = "1.0.0"`
- Registers `IotClient.on_command(self._handle_command)` after connecting
- `_handle_command(cmd_type, payload)`: puts `"ota_start"` event to queue when `cmd_type == "ota"`
- Updated `_send_loop`: injects `fw_version` into every telemetry payload before publishing

**`OtaProgressDialog` (new class in `simulator.py`)**
- `tk.Toplevel`, non-modal (no `grab_set()`)
- Title: `OTA - {device_id}`
- Displays: version transition header, progress bar, scrolled log, status label
- Background thread performs download+verify; calls `self.after(0, callback)` for thread-safe UI updates
- On completion, puts `"ota_done"` event to the main event queue

**`SimulatorApp` (modified)**
- `_ota_dialogs: dict[int, OtaProgressDialog]` to track open dialogs
- `_fw_version_labels: dict[int, tk.Label]` for per-device version labels in device rows
- `_process_queue` handles two new event types: `"ota_start"` and `"ota_done"`
- Device row UI gains a `fw: x.y.z` label

## Data Flow

```
MQTT command arrives
  → IotClient.on_command callback
  → DeviceWorker._handle_command("ota", payload)
  → queue.put((wid, "ota_start", {device_id, payload, ssl_verify}))
  → SimulatorApp._process_queue()
  → OtaProgressDialog(parent=self, wid, device_id, payload, ssl_verify, event_queue)
  → background thread: stream download + SHA256 verify (with ssl_verify)
    → self.after(0, ...) to update progress bar and log
  → on complete: queue.put((wid, "ota_done", {"version": new_version}))
  → SimulatorApp._process_queue(): update fw_version label + worker.fw_version
```

## OTA Command Payload (from platform)

```json
{
  "type": "ota",
  "firmware_id": "abc123",
  "version": "1.2.0",
  "download_url": "https://iot.example.com/api/firmware-download?token=...",
  "checksum": "sha256:a3f1c2d4e5b6...",
  "file_size": 2411724
}
```

## OtaProgressDialog UI Layout

```
┌─────────────────────────────────────────────────────┐
│ OTA - sensor-001                                     │
├─────────────────────────────────────────────────────┤
│ ファームウェア更新: v1.0.0 → v1.2.0                   │
│                                                      │
│ ダウンロード進捗:                                      │
│ [████████████░░░░░░░░] 58%  1.4MB / 2.3MB           │
│                                                      │
│ ┌──────────────────────────────────────────────────┐│
│ │[10:23:01] OTAコマンド受信 (firmware_id: abc123)   ││
│ │[10:23:01] ダウンロード開始: 2.3 MB               ││
│ │[10:23:02] ダウンロード中: 58% (1.4MB/2.3MB)      ││
│ │[10:23:04] ダウンロード完了                        ││
│ │[10:23:04] SHA256検証中...                         ││
│ │[10:23:04]   期待値: a3f1c2d4e5b6...              ││
│ │[10:23:04]   計算値: a3f1c2d4e5b6...              ││
│ │[10:23:04] ✓ チェックサム一致                      ││
│ │[10:23:04] バージョン更新: 1.0.0 → 1.2.0          ││
│ └──────────────────────────────────────────────────┘│
│                                                      │
│ ステータス: ✓ OTA完了                    [閉じる]    │
└─────────────────────────────────────────────────────┘
```

## Device Row UI Change

Before:
```
⚡ provisioning  sensor-001  [my-tenant]               [✕]
```

After:
```
⚡ provisioning  sensor-001  [my-tenant]  fw: 1.0.0    [✕]
```

The `fw: x.y.z` label updates to the new version when OTA completes.

## Download + Verification Implementation

`OtaHandler.download_and_verify()` in the SDK does not provide progress callbacks. The OTA dialog will implement its own streaming download for progress visibility, using the same algorithm:

```python
def _run_ota(self):
    # 1. GET with stream=True, iter_content(chunk_size=65536), verify=ssl_verify
    # 2. Use Content-Length header for total size (may be absent → indeterminate bar)
    # 3. Write to temp file, update sha256, call self.after(0, update_progress)
    # 4. Compare computed sha256 vs expected (strip "sha256:" prefix)
    # 5. Put ota_done or ota_failed event to queue
```

Temp file location: `{CERT_BASE}/{tenant_name}/{device_id}/ota_firmware.bin`
(overwritten on each OTA; cleaned up on device removal)

## fw_version in Telemetry

`DeviceWorker._send_loop` injects `fw_version` after calling the payload function:

```python
payload = payload_fn()
payload["fw_version"] = self.fw_version
self._client.publish_telemetry(payload)
```

This ensures Grafana reflects the current version in all telemetry dashboards.

## Error Handling

| Scenario | Behavior |
|---|---|
| Download HTTP error | Log error, show "✗ OTA失敗", dialog stays open |
| Checksum mismatch | Log both values, show "✗ チェックサム不一致", no version update |
| OTA already in progress for device | Ignore new command, log warning |
| Dialog closed before completion | Thread continues but puts no more events (wid key removed from `_ota_dialogs`) |

## Files to Change

| File | Change |
|---|---|
| `simulator/device_worker.py` | Add `fw_version`, `on_command` registration, `_handle_command`, `fw_version` injection in telemetry |
| `simulator/simulator.py` | Add `OtaProgressDialog` class, update `_add_device` for fw label, update `_process_queue` for ota events, add `_ota_dialogs` and `_fw_version_labels` dicts |
