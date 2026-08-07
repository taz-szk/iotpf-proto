# simulator/simulator.py
from __future__ import annotations

import json
import os
import queue
import random
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, simpledialog, ttk
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device_worker import DeviceWorker


CERT_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")

STATE_ICONS = {
    "provisioning": "⚡",
    "connecting": "⟳",
    "connected": "●",
    "disconnected": "○",
    "error": "✗",
}
STATE_COLORS = {
    "provisioning": "orange",
    "connecting": "blue",
    "connected": "green",
    "disconnected": "gray",
    "error": "red",
}


class SimulatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IoT Platform デバイスシミュレーター")
        self.minsize(720, 520)

        self._event_queue: queue.Queue = queue.Queue()
        self._workers: dict[str, DeviceWorker] = {}
        self._state_labels: dict[str, tk.Label] = {}

        self._api_url = tk.StringVar(value="https://localhost/api")
        self._broker_host = tk.StringVar(value="localhost")
        self._broker_port = tk.IntVar(value=8883)
        self._bootstrap_token = tk.StringVar(value="")
        self._interval = tk.DoubleVar(value=5.0)
        self._use_random = tk.BooleanVar(value=True)
        self._custom_json_text: Optional[scrolledtext.ScrolledText] = None
        self._log_text: Optional[scrolledtext.ScrolledText] = None
        self._device_list_frame: Optional[tk.Frame] = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_queue)

    # ─── UI 構築 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        conn_frame = ttk.LabelFrame(self, text="接続設定", padding=6)
        conn_frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        ttk.Label(conn_frame, text="API URL:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(conn_frame, textvariable=self._api_url, width=35).grid(row=0, column=1, padx=4)
        ttk.Label(conn_frame, text="MQTTホスト:").grid(row=0, column=2, sticky=tk.W, padx=(8, 0))
        ttk.Entry(conn_frame, textvariable=self._broker_host, width=16).grid(row=0, column=3, padx=4)
        ttk.Label(conn_frame, text=":").grid(row=0, column=4)
        ttk.Spinbox(conn_frame, textvariable=self._broker_port, from_=1, to=65535, width=6).grid(row=0, column=5)

        ttk.Label(conn_frame, text="Bootstrap Token:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(conn_frame, textvariable=self._bootstrap_token, width=52, show="*").grid(
            row=1, column=1, columnspan=5, sticky=tk.W, padx=4
        )

        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        # 左: デバイス一覧
        left = ttk.LabelFrame(pane, text="デバイス一覧", padding=6)
        pane.add(left, minsize=200)

        self._device_list_frame = tk.Frame(left)
        self._device_list_frame.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(left)
        btn_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_frame, text="+ 追加", command=self._add_device_dialog).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="▶ 全開始", command=self._start_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="■ 全停止", command=self._stop_all).pack(side=tk.LEFT)

        # 右: テレメトリ設定
        right = ttk.LabelFrame(pane, text="テレメトリ設定", padding=6)
        pane.add(right, minsize=280)

        ttk.Radiobutton(
            right, text="ランダム値  (temperature / humidity / pressure)",
            variable=self._use_random, value=True,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            right, text="カスタム JSON",
            variable=self._use_random, value=False,
        ).pack(anchor=tk.W, pady=(2, 4))

        self._custom_json_text = scrolledtext.ScrolledText(right, height=6, width=32, font=("Consolas", 10))
        self._custom_json_text.insert("1.0", '{\n  "temperature": 25.0\n}')
        self._custom_json_text.pack(fill=tk.BOTH, expand=True)

        ivl_frame = tk.Frame(right)
        ivl_frame.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(ivl_frame, text="送信間隔:").pack(side=tk.LEFT)
        ttk.Spinbox(ivl_frame, textvariable=self._interval, from_=1, to=3600, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(ivl_frame, text="秒").pack(side=tk.LEFT)

        send_frame = tk.Frame(right)
        send_frame.pack(fill=tk.X)
        ttk.Button(send_frame, text="▶ 送信開始", command=self._start_sending).pack(side=tk.LEFT)
        ttk.Button(send_frame, text="■ 送信停止", command=self._stop_sending).pack(side=tk.LEFT, padx=4)

        log_outer = ttk.LabelFrame(self, text="ログ", padding=4)
        log_outer.pack(fill=tk.BOTH, padx=10, pady=(0, 10))

        self._log_text = scrolledtext.ScrolledText(
            log_outer, height=10, state=tk.DISABLED, font=("Consolas", 9)
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)
        self._log_text.tag_config("error", foreground="red")
        self._log_text.tag_config("warn", foreground="orange")
        self._log_text.tag_config("info", foreground="black")

    # ─── デバイス管理 ───────────────────────────────────────────────────────

    def _add_device_dialog(self) -> None:
        device_id = simpledialog.askstring("デバイス追加", "Device ID を入力してください:", parent=self)
        if not device_id or not device_id.strip():
            return
        device_id = device_id.strip()
        if device_id in self._workers:
            messagebox.showwarning("重複", f'"{device_id}" は既に追加されています', parent=self)
            return
        self._add_device(device_id)

    def _add_device(self, device_id: str) -> None:
        cert_dir = os.path.join(CERT_BASE, device_id)
        worker = DeviceWorker(
            device_id=device_id,
            api_url=self._api_url.get(),
            broker_host=self._broker_host.get(),
            broker_port=self._broker_port.get(),
            bootstrap_token=self._bootstrap_token.get(),
            cert_dir=cert_dir,
            event_queue=self._event_queue,
        )
        self._workers[device_id] = worker

        row = tk.Frame(self._device_list_frame)
        row.pack(fill=tk.X, pady=1, anchor=tk.W)
        state_lbl = tk.Label(row, text="⚡ provisioning", fg="orange", width=20, anchor=tk.W, font=("Consolas", 9))
        state_lbl.pack(side=tk.LEFT, padx=4)
        tk.Label(row, text=device_id, font=("Consolas", 9)).pack(side=tk.LEFT)
        self._state_labels[device_id] = state_lbl

        worker.start()

    # ─── 送信制御 ───────────────────────────────────────────────────────────

    def _get_payload_fn(self):
        if self._use_random.get():
            def fn():
                return {
                    "temperature": round(random.uniform(20.0, 30.0), 2),
                    "humidity": round(random.uniform(40.0, 80.0), 2),
                    "pressure": round(random.uniform(1000.0, 1020.0), 2),
                }
            return fn
        raw = self._custom_json_text.get("1.0", tk.END).strip()
        try:
            payload_dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._append_log(f"JSON パースエラー: {exc}", level="warn")
            return None
        return lambda: payload_dict

    def _start_sending(self) -> None:
        payload_fn = self._get_payload_fn()
        if payload_fn is None:
            return
        interval = self._interval.get()
        for worker in self._workers.values():
            worker.stop_sending()
            worker.start_sending(interval, payload_fn)

    def _stop_sending(self) -> None:
        for worker in self._workers.values():
            worker.stop_sending()

    def _start_all(self) -> None:
        self._start_sending()

    def _stop_all(self) -> None:
        self._stop_sending()

    # ─── キュー処理・UI 更新 ────────────────────────────────────────────────

    def _process_queue(self) -> None:
        try:
            while True:
                device_id, event_type, data = self._event_queue.get_nowait()
                if event_type == "status":
                    self._update_state(device_id, data["state"])
                elif event_type == "log":
                    self._append_log(data["message"], level=data.get("level", "info"))
                elif event_type == "telemetry":
                    payload_str = json.dumps(data.get("payload", {}), ensure_ascii=False)
                    self._append_log(f"{device_id}: telemetry {payload_str}", level="info")
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    def _update_state(self, device_id: str, state: str) -> None:
        lbl = self._state_labels.get(device_id)
        if lbl:
            icon = STATE_ICONS.get(state, "?")
            color = STATE_COLORS.get(state, "black")
            lbl.config(text=f"{icon} {state}", fg=color)

    def _append_log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{ts}] {message}\n", level)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ─── 終了処理 ───────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        for worker in self._workers.values():
            worker.stop()
        self.destroy()


if __name__ == "__main__":
    app = SimulatorApp()
    app.mainloop()
