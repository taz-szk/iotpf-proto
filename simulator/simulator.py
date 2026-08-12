# simulator/simulator.py
from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device_worker import DeviceWorker

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CERT_BASE = os.path.join(_BASE_DIR, "certs")
CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")

STATE_ICONS = {
    "provisioning": "⚡", "connecting": "⟳", "connected": "●",
    "disconnected": "○", "error": "✗",
}
STATE_COLORS = {
    "provisioning": "orange", "connecting": "blue", "connected": "green",
    "disconnected": "gray", "error": "red",
}

# anomaly_prob は UI 上で % 表示 (例: 1.0 = 1%)
DEFAULT_RANDOM_FIELDS = [
    {"name": "temperature", "min": 20.0, "max": 30.0, "anomaly_val": 100.0, "anomaly_prob": 1.0},
    {"name": "humidity",    "min": 40.0, "max": 80.0, "anomaly_val":   0.0, "anomaly_prob": 1.0},
    {"name": "pressure",    "min": 1000.0, "max": 1020.0, "anomaly_val": 800.0, "anomaly_prob": 1.0},
]


def _center_on_parent(win: tk.Toplevel, parent: tk.Widget) -> None:
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width()  - win.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


# ─── テナント設定ダイアログ ───────────────────────────────────────────────────

class TenantDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, existing: dict | None = None):
        super().__init__(parent)
        self.title("テナント設定" if existing is None else "テナント編集")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result: dict | None = None

        d = existing or {}
        self._name_var   = tk.StringVar(value=d.get("name", ""))
        self._api_var    = tk.StringVar(value=d.get("api_url", "https://localhost/api"))
        self._host_var   = tk.StringVar(value=d.get("broker_host", "localhost"))
        self._port_var   = tk.IntVar(value=d.get("broker_port", 8883))
        self._token_var  = tk.StringVar(value=d.get("bootstrap_token", ""))
        self._ssl_var    = tk.BooleanVar(value=d.get("ssl_verify", True))
        self._email_var  = tk.StringVar(value=d.get("platform_email", ""))
        self._passwd_var = tk.StringVar(value=d.get("platform_password", ""))

        f = ttk.Frame(self, padding=12)
        f.pack(fill=tk.BOTH, expand=True)

        fields = [
            ("テナント名 *",        self._name_var,   False, 36),
            ("API URL",            self._api_var,    False, 36),
            ("MQTTホスト",         self._host_var,   False, 24),
        ]
        for i, (lbl, var, secret, w) in enumerate(fields):
            ttk.Label(f, text=lbl).grid(row=i, column=0, sticky=tk.W, pady=3, padx=(0, 6))
            ttk.Entry(f, textvariable=var, width=w, show="*" if secret else "").grid(
                row=i, column=1, sticky=tk.W)

        ttk.Label(f, text="MQTTポート").grid(row=3, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Spinbox(f, textvariable=self._port_var, from_=1, to=65535, width=9).grid(
            row=3, column=1, sticky=tk.W)

        ttk.Label(f, text="Bootstrap Token").grid(row=4, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(f, textvariable=self._token_var, width=36, show="*").grid(
            row=4, column=1, sticky=tk.W)

        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(8, 4))
        ttk.Label(f, text="管理者メール", foreground="gray").grid(row=6, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(f, textvariable=self._email_var, width=36).grid(row=6, column=1, sticky=tk.W)
        ttk.Label(f, text="管理者パスワード", foreground="gray").grid(row=7, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(f, textvariable=self._passwd_var, width=36, show="*").grid(row=7, column=1, sticky=tk.W)
        ttk.Label(f, text="↑ デバイス削除時にPF側APIを呼ぶために使用",
                  foreground="gray", font=("", 8)).grid(row=8, column=0, columnspan=2, sticky=tk.W)

        ttk.Checkbutton(f, text="SSL証明書を検証する（オフ=自己署名証明書を許可）",
                        variable=self._ssl_var).grid(row=9, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        bf = ttk.Frame(f)
        bf.grid(row=10, column=0, columnspan=2, pady=(12, 0), sticky=tk.E)
        ttk.Button(bf, text="OK",       command=self._ok,      width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="キャンセル", command=self.destroy, width=8).pack(side=tk.LEFT)

        self.minsize(440, 280)
        _center_on_parent(self, parent)
        self.wait_window()

    def _ok(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            messagebox.showwarning("入力エラー", "テナント名を入力してください", parent=self)
            return
        self.result = {
            "name": name,
            "api_url": self._api_var.get().strip(),
            "broker_host": self._host_var.get().strip(),
            "broker_port": self._port_var.get(),
            "bootstrap_token": self._token_var.get(),
            "ssl_verify": self._ssl_var.get(),
            "platform_email": self._email_var.get().strip(),
            "platform_password": self._passwd_var.get(),
        }
        self.destroy()


# ─── OTA 進捗ダイアログ ───────────────────────────────────────────────────────

class OtaProgressDialog(tk.Toplevel):
    """デバイスごとのOTA進捗ウィンドウ（非モーダル）"""

    def __init__(
        self,
        parent: tk.Widget,
        wid: int,
        device_id: str,
        old_version: str,
        new_version: str,
        payload: dict,
        ssl_verify: bool,
        event_queue: queue.Queue,
        out_path: str,
    ):
        super().__init__(parent)
        self._wid = wid
        self._device_id = device_id
        self._old_version = old_version
        self._new_version = new_version
        self._payload = payload
        self._ssl_verify = ssl_verify
        self._event_queue = event_queue
        self._out_path = out_path
        self._done = False

        self.title(f"OTA - {device_id}")
        self.resizable(True, True)
        self.minsize(520, 400)

        # ヘッダー
        hf = ttk.LabelFrame(self, text="ファームウェア更新", padding=6)
        hf.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(hf, text=f"v{old_version}  →  v{new_version}",
                 font=("", 12, "bold")).pack(anchor=tk.W)

        # プログレスバー
        pf = ttk.LabelFrame(self, text="ダウンロード進捗", padding=6)
        pf.pack(fill=tk.X, padx=8, pady=4)
        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress_bar = ttk.Progressbar(
            pf, variable=self._progress_var, maximum=100.0, length=480)
        self._progress_bar.pack(fill=tk.X, pady=(0, 4))
        self._progress_label = tk.Label(pf, text="待機中...", anchor=tk.W, font=("Consolas", 9))
        self._progress_label.pack(anchor=tk.W)

        # ログ
        lf = ttk.LabelFrame(self, text="ログ", padding=4)
        lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self._log_text = scrolledtext.ScrolledText(
            lf, height=12, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # ステータス + 閉じるボタン
        bf = tk.Frame(self)
        bf.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._status_label = tk.Label(bf, text="● OTA処理中...", fg="blue", anchor=tk.W)
        self._status_label.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self._close_btn = ttk.Button(bf, text="閉じる", state=tk.DISABLED,
                                     command=self.destroy)
        self._close_btn.pack(side=tk.RIGHT)

        _center_on_parent(self, parent)

    def start_ota(self) -> None:
        t = threading.Thread(target=self._run_ota, daemon=True,
                             name=f"ota-{self._device_id}")
        t.start()

    # ─── バックグラウンドスレッド ─────────────────────────────────────────────

    def _run_ota(self) -> None:
        try:
            import requests as _req
            self._schedule_log(
                f"OTAコマンド受信 (firmware_id: {self._payload.get('firmware_id', 'unknown')})")
            url = self._payload["download_url"]
            checksum_raw = self._payload.get("checksum", "")
            expected_sha256 = checksum_raw.removeprefix("sha256:")
            file_size = self._payload.get("file_size", 0)

            # ダウンロード開始
            size_str = f"{file_size / 1024 / 1024:.1f} MB" if file_size else "不明"
            self._schedule_log(f"ダウンロード開始: {size_str}")
            self._schedule_log(f"URL: {url}")

            resp = _req.get(url, stream=True, verify=self._ssl_verify, timeout=300)
            resp.raise_for_status()

            total = int(resp.headers.get("Content-Length", file_size or 0))

            sha256 = hashlib.sha256()
            downloaded = 0
            os.makedirs(os.path.dirname(self._out_path), exist_ok=True)

            with open(self._out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha256.update(chunk)
                    downloaded += len(chunk)
                    self.after(0, self._update_progress, downloaded, total)

            self._schedule_log(f"ダウンロード完了 ({downloaded / 1024 / 1024:.2f} MB)")

            # SHA256 検証
            actual_sha256 = sha256.hexdigest()
            self._schedule_log("SHA256 検証中...")
            self._schedule_log(f"  期待値: {expected_sha256[:32]}...")
            self._schedule_log(f"  計算値: {actual_sha256[:32]}...")

            if actual_sha256 == expected_sha256:
                self._schedule_log("✓ チェックサム一致")
                self._schedule_log(
                    f"バージョン更新: {self._old_version} → {self._new_version}")
                self._event_queue.put(
                    (self._wid, "ota_done", {"version": self._new_version}))
                self.after(0, self._finish, True, f"✓ OTA完了 (v{self._new_version})")
            else:
                self._schedule_log(
                    f"✗ チェックサム不一致\n  期待: {expected_sha256}\n  実際: {actual_sha256}")
                self._event_queue.put(
                    (self._wid, "ota_failed", {"error": "checksum_mismatch"}))
                self.after(0, self._finish, False, "✗ チェックサム不一致")

        except Exception as exc:
            self._schedule_log(f"✗ OTAエラー: {exc}")
            self._event_queue.put(
                (self._wid, "ota_failed", {"error": str(exc)}))
            self.after(0, self._finish, False, f"✗ エラー: {exc}")

    # ─── UIコールバック（メインスレッドから呼ばれる）──────────────────────────

    def _update_progress(self, downloaded: int, total: int) -> None:
        try:
            if total > 0:
                pct = downloaded / total * 100
                self._progress_var.set(pct)
                d_mb = downloaded / 1024 / 1024
                t_mb = total / 1024 / 1024
                self._progress_label.config(
                    text=f"{d_mb:.1f} MB / {t_mb:.1f} MB  ({pct:.0f}%)")
            else:
                d_mb = downloaded / 1024 / 1024
                self._progress_label.config(text=f"{d_mb:.1f} MB ダウンロード中...")
        except tk.TclError:
            pass

    def _finish(self, success: bool, message: str) -> None:
        try:
            self._done = True
            self._status_label.config(
                text=message, fg="green" if success else "red")
            self._progress_var.set(100.0 if success else self._progress_var.get())
            self._close_btn.config(state=tk.NORMAL)
        except tk.TclError:
            pass

    def _schedule_log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _append_log(self, msg: str) -> None:
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_text.config(state=tk.NORMAL)
            self._log_text.insert(tk.END, f"[{ts}] {msg}\n")
            self._log_text.see(tk.END)
            self._log_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass


# ─── メインアプリ ─────────────────────────────────────────────────────────────

class SimulatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IoT Platform デバイスシミュレーター")
        self.minsize(880, 620)

        self._event_queue: queue.Queue = queue.Queue()
        self._next_wid: int = 0
        self._workers: dict[int, DeviceWorker] = {}
        self._state_labels: dict[int, tk.Label] = {}
        self._device_rows: dict[int, tk.Frame] = {}
        self._tenants: list[dict] = []
        self._device_tenants: dict[int, str] = {}   # wid -> tenant name
        self._field_rows: list[dict] = []            # ランダムフィールド行

        self._use_random = tk.BooleanVar(value=True)
        self._interval   = tk.DoubleVar(value=5.0)
        self._custom_json_text: Optional[scrolledtext.ScrolledText] = None
        self._log_text:         Optional[scrolledtext.ScrolledText] = None
        self._device_list_frame: Optional[tk.Frame] = None
        self._tenant_listbox:    Optional[tk.Listbox] = None
        self._random_fields_inner: Optional[tk.Frame] = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_queue)
        self._load_config()

    # ─── UI 構築 ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # === 左ペイン ===
        left = tk.Frame(pane)
        pane.add(left, minsize=230)

        # テナント一覧
        tf = ttk.LabelFrame(left, text="テナント", padding=4)
        tf.pack(fill=tk.X, pady=(0, 4))
        self._tenant_listbox = tk.Listbox(tf, height=4, font=("Consolas", 9), selectmode=tk.SINGLE)
        self._tenant_listbox.pack(fill=tk.X)
        tb = tk.Frame(tf)
        tb.pack(fill=tk.X, pady=(3, 0))
        ttk.Button(tb, text="+ 追加", width=6, command=self._add_tenant).pack(side=tk.LEFT)
        ttk.Button(tb, text="編集",   width=5, command=self._edit_tenant).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="削除",   width=5, command=self._delete_tenant).pack(side=tk.LEFT)

        # デバイス一覧
        df = ttk.LabelFrame(left, text="デバイス一覧", padding=4)
        df.pack(fill=tk.BOTH, expand=True)
        self._device_list_frame = tk.Frame(df)
        self._device_list_frame.pack(fill=tk.BOTH, expand=True)
        db = tk.Frame(df)
        db.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(db, text="+ 追加",  command=self._add_device_dialog).pack(side=tk.LEFT)
        ttk.Button(db, text="▶ 全開始", command=self._start_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(db, text="■ 全停止", command=self._stop_all).pack(side=tk.LEFT)

        # === 右ペイン ===
        right = ttk.LabelFrame(pane, text="テレメトリ設定", padding=6)
        pane.add(right, minsize=420)

        # モード切替
        mf = tk.Frame(right)
        mf.pack(fill=tk.X)
        ttk.Radiobutton(mf, text="ランダム値", variable=self._use_random, value=True,
                        command=self._refresh_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(mf, text="カスタム JSON", variable=self._use_random, value=False,
                        command=self._refresh_mode).pack(side=tk.LEFT, padx=(8, 0))

        # ランダム設定フレーム
        self._random_frame = tk.Frame(right)
        self._build_random_ui()

        # カスタム JSON フレーム
        self._custom_frame = tk.Frame(right)
        self._custom_json_text = scrolledtext.ScrolledText(
            self._custom_frame, height=9, font=("Consolas", 10))
        self._custom_json_text.insert("1.0", '{\n  "temperature": 25.0\n}')
        self._custom_json_text.pack(fill=tk.BOTH, expand=True)

        self._refresh_mode()

        # 送信コントロール
        ctrl = tk.Frame(right)
        ctrl.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(ctrl, text="送信間隔:").pack(side=tk.LEFT)
        ttk.Spinbox(ctrl, textvariable=self._interval, from_=1, to=3600, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(ctrl, text="秒").pack(side=tk.LEFT)
        ttk.Button(ctrl, text="▶ 送信開始", command=self._start_sending).pack(side=tk.LEFT, padx=(14, 2))
        ttk.Button(ctrl, text="■ 送信停止", command=self._stop_sending).pack(side=tk.LEFT)

        # === ログ ===
        lf = ttk.LabelFrame(self, text="ログ", padding=4)
        lf.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._log_text = scrolledtext.ScrolledText(lf, height=8, state=tk.DISABLED, font=("Consolas", 9))
        self._log_text.pack(fill=tk.X)
        self._log_text.tag_config("error", foreground="red")
        self._log_text.tag_config("warn",  foreground="orange")
        self._log_text.tag_config("info",  foreground="black")

    def _build_random_ui(self) -> None:
        """ランダムフィールドテーブルを（再）構築する"""
        for w in self._random_frame.winfo_children():
            w.destroy()
        self._field_rows = []

        # ヘッダー行
        hdr = tk.Frame(self._random_frame)
        hdr.pack(fill=tk.X)
        for col, (w, text) in enumerate([
            (13, "フィールド名"), (7, "最小値"), (7, "最大値"), (9, "異常値"), (7, "確率(%)"),
        ]):
            tk.Label(hdr, text=text, font=("", 8, "bold"), width=w, anchor=tk.W).grid(
                row=0, column=col, padx=1)

        # フィールド行コンテナ
        self._random_fields_inner = tk.Frame(self._random_frame)
        self._random_fields_inner.pack(fill=tk.X)

        # デフォルト行を追加
        for fd in DEFAULT_RANDOM_FIELDS:
            self._add_field_row(fd)

        ttk.Button(self._random_frame, text="+ フィールド追加",
                   command=self._add_field_row).pack(anchor=tk.W, pady=(4, 0))

    def _add_field_row(self, data: dict | None = None) -> None:
        d = data or {"name": "", "min": 0.0, "max": 100.0, "anomaly_val": 200.0, "anomaly_prob": 1.0}
        rd = {
            "name":        tk.StringVar(value=str(d.get("name", ""))),
            "min":         tk.StringVar(value=str(d.get("min", 0.0))),
            "max":         tk.StringVar(value=str(d.get("max", 100.0))),
            "anomaly_val": tk.StringVar(value=str(d.get("anomaly_val", 200.0))),
            "anomaly_prob":tk.StringVar(value=str(d.get("anomaly_prob", 1.0))),
        }
        frame = tk.Frame(self._random_fields_inner)
        frame.pack(fill=tk.X, pady=1)
        for col, (key, w) in enumerate([
            ("name", 13), ("min", 7), ("max", 7), ("anomaly_val", 9), ("anomaly_prob", 7),
        ]):
            ttk.Entry(frame, textvariable=rd[key], width=w).grid(row=0, column=col, padx=1)

        rd["frame"] = frame
        self._field_rows.append(rd)

        def remove(r=rd):
            r["frame"].destroy()
            self._field_rows.remove(r)

        ttk.Button(frame, text="×", width=2, command=remove).grid(row=0, column=5, padx=2)

    def _refresh_mode(self) -> None:
        if self._use_random.get():
            self._custom_frame.pack_forget()
            self._random_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        else:
            self._random_frame.pack_forget()
            self._custom_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    # ─── テナント管理 ─────────────────────────────────────────────────────────

    def _refresh_tenant_list(self) -> None:
        self._tenant_listbox.delete(0, tk.END)
        for t in self._tenants:
            self._tenant_listbox.insert(tk.END, t["name"])

    def _add_tenant(self) -> None:
        dlg = TenantDialog(self)
        if not dlg.result:
            return
        if any(t["name"] == dlg.result["name"] for t in self._tenants):
            messagebox.showwarning("重複", f'テナント名 "{dlg.result["name"]}" は既に存在します', parent=self)
            return
        self._tenants.append(dlg.result)
        self._refresh_tenant_list()

    def _edit_tenant(self) -> None:
        sel = self._tenant_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        dlg = TenantDialog(self, existing=self._tenants[idx])
        if not dlg.result:
            return
        old_name = self._tenants[idx]["name"]
        if dlg.result["name"] != old_name:
            for did, tn in self._device_tenants.items():
                if tn == old_name:
                    self._device_tenants[did] = dlg.result["name"]
        self._tenants[idx] = dlg.result
        self._refresh_tenant_list()

    def _delete_tenant(self) -> None:
        sel = self._tenant_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        name = self._tenants[idx]["name"]
        if any(tn == name for tn in self._device_tenants.values()):
            messagebox.showwarning("使用中", f'テナント "{name}" はデバイスに割り当て中のため削除できません', parent=self)
            return
        if messagebox.askyesno("確認", f'テナント "{name}" を削除しますか？', parent=self):
            self._tenants.pop(idx)
            self._refresh_tenant_list()

    # ─── デバイス管理 ─────────────────────────────────────────────────────────

    def _next_device_id(self) -> str:
        existing = {w.device_id for w in self._workers.values()}
        n = 1
        while f"sim-{n:03d}" in existing:
            n += 1
        return f"sim-{n:03d}"

    def _add_device_dialog(self) -> None:
        if not self._tenants:
            messagebox.showwarning("テナント未設定", "先にテナントを追加してください", parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title("デバイス追加")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        device_id_var = tk.StringVar(value=self._next_device_id())
        tenant_var    = tk.StringVar(value=self._tenants[0]["name"])

        f = ttk.Frame(dlg, padding=14)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(f, text="Device ID:").grid(row=0, column=0, sticky=tk.W, pady=4, padx=(0, 8))
        ttk.Entry(f, textvariable=device_id_var, width=28).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(f, text="テナント:").grid(row=1, column=0, sticky=tk.W, pady=4, padx=(0, 8))
        ttk.Combobox(f, textvariable=tenant_var,
                     values=[t["name"] for t in self._tenants],
                     state="readonly", width=27).grid(row=1, column=1, sticky=tk.W)

        def ok():
            dev_id = device_id_var.get().strip()
            if not dev_id:
                messagebox.showwarning("入力エラー", "Device ID を入力してください", parent=dlg)
                return
            tenant_name = tenant_var.get()
            already = any(
                w.device_id == dev_id and self._device_tenants.get(wid) == tenant_name
                for wid, w in self._workers.items()
            )
            if already:
                messagebox.showwarning("重複", f'テナント "{tenant_name}" に "{dev_id}" は既に追加されています', parent=dlg)
                return
            tenant = next((t for t in self._tenants if t["name"] == tenant_var.get()), None)
            if not tenant:
                return
            dlg.destroy()
            self._add_device(dev_id, tenant)

        bf = ttk.Frame(f)
        bf.grid(row=2, column=0, columnspan=2, pady=(12, 0), sticky=tk.E)
        ttk.Button(bf, text="OK",       command=ok,          width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="キャンセル", command=dlg.destroy, width=8).pack(side=tk.LEFT)
        _center_on_parent(dlg, self)
        dlg.wait_window()

    def _add_device(self, device_id: str, tenant: dict) -> None:
        wid = self._next_wid
        self._next_wid += 1
        cert_dir = os.path.join(CERT_BASE, tenant["name"], device_id)
        worker = DeviceWorker(
            wid=wid,
            device_id=device_id,
            api_url=tenant["api_url"],
            broker_host=tenant["broker_host"],
            broker_port=tenant["broker_port"],
            bootstrap_token=tenant["bootstrap_token"],
            cert_dir=cert_dir,
            event_queue=self._event_queue,
            ssl_verify=tenant["ssl_verify"],
        )
        self._workers[wid] = worker
        self._device_tenants[wid] = tenant["name"]

        row = tk.Frame(self._device_list_frame)
        row.pack(fill=tk.X, pady=1, anchor=tk.W)
        state_lbl = tk.Label(row, text="⚡ provisioning", fg="orange",
                             width=20, anchor=tk.W, font=("Consolas", 9))
        state_lbl.pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=device_id, font=("Consolas", 9)).pack(side=tk.LEFT)
        tk.Label(row, text=f" [{tenant['name']}]", fg="gray", font=("", 8)).pack(side=tk.LEFT)
        tk.Button(row, text="✕", font=("", 8), fg="gray", relief=tk.FLAT, bd=0,
                  cursor="hand2", command=lambda w=wid: self._remove_device(w)
                  ).pack(side=tk.RIGHT, padx=2)
        self._state_labels[wid] = state_lbl
        self._device_rows[wid] = row

        worker.start()

    def _remove_device(self, wid: int) -> None:
        worker = self._workers.get(wid)
        if not worker:
            return
        tenant_name = self._device_tenants.get(wid, "")
        tenant_cfg  = next((t for t in self._tenants if t["name"] == tenant_name), None)

        if not messagebox.askyesno(
            "デバイス削除",
            f'"{worker.device_id}" を削除しますか？\n'
            "・シミュレーターから除外\n"
            "・プラットフォーム側のデバイス登録を削除\n"
            "・ローカル証明書を削除",
            parent=self,
        ):
            return

        # PF 側 API でデバイスを削除
        cert_dir      = os.path.join(CERT_BASE, tenant_name, worker.device_id)
        tenant_id_path = os.path.join(cert_dir, "tenant_id")
        if tenant_cfg and os.path.exists(tenant_id_path):
            with open(tenant_id_path) as fp:
                tenant_id = fp.read().strip()
            api_url  = tenant_cfg["api_url"]
            verify   = tenant_cfg.get("ssl_verify", True)
            email    = tenant_cfg.get("platform_email", "")
            password = tenant_cfg.get("platform_password", "")
            try:
                import urllib3
                if not verify:
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                import requests as _req
                login_resp = _req.post(
                    f"{api_url}/auth/login",
                    json={"email": email, "password": password},
                    timeout=10, verify=verify,
                )
                login_resp.raise_for_status()
                token = login_resp.json()["access_token"]
                del_resp = _req.delete(
                    f"{api_url}/tenants/{tenant_id}/devices/{worker.device_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10, verify=verify,
                )
                if del_resp.status_code == 204:
                    self._append_log(f"{worker.device_id}: PF側から削除完了", level="info")
                elif del_resp.status_code == 404:
                    self._append_log(f"{worker.device_id}: PF側に登録なし（スキップ）", level="info")
                else:
                    del_resp.raise_for_status()
            except Exception as exc:
                if not messagebox.askyesno(
                    "API エラー",
                    f"プラットフォーム側の削除に失敗しました:\n{exc}\n\nシミュレーターからだけ削除しますか？",
                    parent=self,
                ):
                    return
        elif not os.path.exists(tenant_id_path):
            # まだプロビジョニングされていないデバイスはPF側に存在しない
            self._append_log(f"{worker.device_id}: 未プロビジョニング — PF側削除をスキップ", level="info")

        # ローカル証明書を削除
        import shutil
        if os.path.isdir(cert_dir):
            shutil.rmtree(cert_dir, ignore_errors=True)

        # ワーカー停止・UI 除去
        worker.stop()
        self._workers.pop(wid, None)
        self._state_labels.pop(wid, None)
        self._device_tenants.pop(wid, None)
        row = self._device_rows.pop(wid, None)
        if row:
            row.destroy()

    # ─── テレメトリ送信 ───────────────────────────────────────────────────────

    def _get_payload_fn(self):
        if not self._use_random.get():
            raw = self._custom_json_text.get("1.0", tk.END).strip()
            try:
                payload_dict = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._append_log(f"JSON パースエラー: {exc}", level="warn")
                return None
            return lambda: payload_dict

        fields = []
        for rd in self._field_rows:
            try:
                name = rd["name"].get().strip()
                if not name:
                    continue
                fields.append({
                    "name":        name,
                    "min":         float(rd["min"].get()),
                    "max":         float(rd["max"].get()),
                    "anomaly_val": float(rd["anomaly_val"].get()),
                    "anomaly_prob":float(rd["anomaly_prob"].get()) / 100.0,  # % → 0-1
                })
            except ValueError:
                continue

        if not fields:
            self._append_log("有効なフィールドがありません", level="warn")
            return None

        def fn():
            payload = {}
            for fld in fields:
                if random.random() < fld["anomaly_prob"]:
                    payload[fld["name"]] = round(fld["anomaly_val"], 4)
                else:
                    payload[fld["name"]] = round(random.uniform(fld["min"], fld["max"]), 4)
            return payload

        return fn

    def _start_sending(self) -> None:
        fn = self._get_payload_fn()
        if fn is None:
            return
        interval = self._interval.get()
        for w in self._workers.values():
            w.stop_sending()
            w.start_sending(interval, fn)

    def _stop_sending(self) -> None:
        for w in self._workers.values():
            w.stop_sending()

    def _start_all(self) -> None:
        self._start_sending()

    def _stop_all(self) -> None:
        self._stop_sending()

    # ─── キュー処理・UI 更新 ─────────────────────────────────────────────────

    def _process_queue(self) -> None:
        try:
            while True:
                wid, event_type, data = self._event_queue.get_nowait()
                if event_type == "status":
                    self._update_state(wid, data["state"])
                elif event_type == "log":
                    self._append_log(data["message"], level=data.get("level", "info"))
                elif event_type == "telemetry":
                    w = self._workers.get(wid)
                    label = w.device_id if w else str(wid)
                    s = json.dumps(data.get("payload", {}), ensure_ascii=False)
                    self._append_log(f"{label}: telemetry {s}", level="info")
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    def _update_state(self, wid: int, state: str) -> None:
        lbl = self._state_labels.get(wid)
        if lbl:
            lbl.config(text=f"{STATE_ICONS.get(state,'?')} {state}",
                       fg=STATE_COLORS.get(state, "black"))

    def _append_log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{ts}] {message}\n", level)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ─── 設定の保存・復元 ────────────────────────────────────────────────────

    def _collect_random_fields(self) -> list[dict]:
        result = []
        for rd in self._field_rows:
            try:
                name = rd["name"].get().strip()
                if not name:
                    continue
                result.append({
                    "name":        name,
                    "min":         float(rd["min"].get()),
                    "max":         float(rd["max"].get()),
                    "anomaly_val": float(rd["anomaly_val"].get()),
                    "anomaly_prob":float(rd["anomaly_prob"].get()),
                })
            except ValueError:
                pass
        return result

    def _save_config(self) -> None:
        try:
            custom_json = self._custom_json_text.get("1.0", tk.END).strip() if self._custom_json_text else ""
            cfg = {
                "tenants": self._tenants,
                "devices": [{"device_id": w.device_id, "tenant_name": self._device_tenants[wid]}
                            for wid, w in self._workers.items()],
                "use_random":    self._use_random.get(),
                "interval":      self._interval.get(),
                "custom_json":   custom_json,
                "random_fields": self._collect_random_fields(),
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"設定保存エラー: {e}")

    def _load_config(self) -> None:
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            self._tenants = cfg.get("tenants", [])
            self._refresh_tenant_list()

            self._use_random.set(cfg.get("use_random", True))
            self._interval.set(cfg.get("interval", 5.0))

            if cfg.get("custom_json") and self._custom_json_text:
                self._custom_json_text.delete("1.0", tk.END)
                self._custom_json_text.insert("1.0", cfg["custom_json"])

            saved_fields = cfg.get("random_fields")
            if saved_fields:
                for rd in list(self._field_rows):
                    rd["frame"].destroy()
                self._field_rows.clear()
                for fd in saved_fields:
                    self._add_field_row(fd)

            self._refresh_mode()

            for dev in cfg.get("devices", []):
                did, tn = dev["device_id"], dev["tenant_name"]
                tenant = next((t for t in self._tenants if t["name"] == tn), None)
                already = any(
                    w.device_id == did and self._device_tenants.get(k) == tn
                    for k, w in self._workers.items()
                )
                if tenant and not already:
                    self._add_device(did, tenant)
                elif not tenant:
                    self._append_log(f"デバイス '{did}': テナント '{tn}' が見つかりません", level="warn")

        except Exception as e:
            self._append_log(f"設定読み込みエラー: {e}", level="warn")

    # ─── 終了処理 ─────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self._save_config()
        for w in self._workers.values():
            w.stop()
        self.destroy()


if __name__ == "__main__":
    app = SimulatorApp()
    app.mainloop()
