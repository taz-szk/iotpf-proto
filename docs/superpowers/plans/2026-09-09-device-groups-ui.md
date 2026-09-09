# デバイスグループ管理機能（UI）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/plans/2026-09-08-device-groups-backend.md` で実装済みのデバイスグループ管理バックエンドAPIを、テナントポータル（`admin-ui/tenant-portal.html`）とPF管理者画面（`platform-ui/tenant.html`）から操作できるUIを追加する。

**Architecture:** 両ファイルとも既存のAlpine.js単一ファイルアプリのタブ構成に新規タブ「グループ管理」を追加し、既存の「デバイス」タブにグループ列・割当UIを、「アラートルール」フォームに対象選択（デバイス/グループ/全テナント）を追加する。ファームウェアのグループ一括OTAは、既存のOTA送信ダイアログ（デバイス複数選択→個別送信ループ）に「グループで絞り込み」機能を追加する形で実現する（新しい一括送信APIエンドポイントは呼ばない — 既存の動作確認済みループを再利用し、UIリスクを抑える）。ダッシュボード設定のグループ別上書きはテナントポータルのみ対象（PF管理者画面にダッシュボード設定タブが存在しないため）。

**Tech Stack:** Alpine.js（既存パターン踏襲）、Tailwind CSS（`npm run build:css`必須）

**Spec:** `docs/superpowers/specs/2026-09-08-device-groups-design.md`（「UI変更」節）

## Global Constraints

- `admin-ui/*.html` / `platform-ui/*.html` を編集した後は必ず `npm run build:css` を実行してからコミットする
- `group_id` をAPIに送る際、未選択状態は必ず `null` を送ること。空文字列 `''` を送ってはならない（バックエンドの `_validate_uuid()` は空文字列もUUID形式チェックに通し、422エラーになる）。既存の `device_id` の「未選択=空文字列」という慣習とは異なるので注意
- このプランは `admin-ui/tenant-portal.html` と `platform-ui/tenant.html` のみを変更する。`core-api/`・`alert-service/` 配下は変更しないこと（バックエンドは前プランで実装済み）
- グループCRUD（作成・編集・削除）は管理者(admin)ロールのみ操作可能。テナントポータル側は既存の `isAdmin` ゲート（`x-show="isAdmin"`）を使う。PF管理者側はロール区分がないため常に許可
- デバイスのグループ割当はテナントポータル側では admin/operator（既存の `canEdit` ゲート）が操作可能
- テストは自動化されたJSテストフレームワークがこのリポジトリに存在しないため、各タスクでは (1) `npm run build:css` の実行成功、(2) 既存Pythonテストスイートに回帰がないことの確認（バックエンド未変更のため通常は無影響）、(3) 追加したHTML/Alpine.jsコードが既存の呼び出しパターン・APIパス・レスポンス形状と整合していることのレビュー、をもって検証とする

---

## File Structure

**変更:**
- `admin-ui/tenant-portal.html` — グループ管理タブ、デバイスタブへのグループ列/割当、アラートルーム対象選択、ファームウェアOTAのグループ絞り込み、ダッシュボード設定のグループセレクタ
- `platform-ui/tenant.html` — グループ管理タブ、デバイスタブへのグループ列/割当、アラートルール対象選択、ファームウェアOTAのグループ絞り込み

---

### Task 1: テナントポータル — グループ管理タブ + デバイスのグループ割当

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: `GET/POST /tenant-portal/me/groups`, `PATCH/DELETE /tenant-portal/me/groups/{group_id}`, `PATCH /tenant-portal/me/devices/{device_id}` body `{group_id: uuid|null}`（いずれも実装済み・`docs/superpowers/plans/2026-09-08-device-groups-backend.md` Task2/3参照）
- Produces: `groups` state配列と `loadGroups()`／`groupName(id)` ヘルパー。Task2はこれを再利用する

- [ ] **Step 1: `get tabs()` に「グループ管理」タブを追加する**

`admin-ui/tenant-portal.html` の `get tabs()`（1087〜1103行目付近）内、`base` 配列に `devices` の直後へ追加:

```javascript
      const base = [
        { id: 'dashboard',        label: 'ダッシュボード',           icon: '▦' },
        { id: 'dashboard-config', label: 'ダッシュボード設定',       icon: '⚙' },
        { id: 'tokens',           label: 'プロビジョニングトークン', icon: '🔑' },
        { id: 'devices',          label: 'デバイス',                 icon: '📡' },
        { id: 'groups',           label: 'グループ管理',             icon: '📁' },
        { id: 'firmware',         label: 'ファームウェア',           icon: '💾' },
        { id: 'alerts',           label: 'アラートルール',           icon: '🔔' },
        { id: 'users',            label: 'ユーザー管理',             icon: '👤' },
      ];
```

- [ ] **Step 2: state に groups 関連プロパティを追加する**

`devices: [], devicesLoading: false, _devTimer: null,`（1166行目付近）の直後に追加:

```javascript
    // グループ管理
    groups: [], showGroupForm: false, editingGroupId: null,
    groupForm: { name: '', description: '' }, groupError: '',
```

- [ ] **Step 3: グループCRUDのメソッドを追加する**

`formatDate(iso) { ... }`（1183〜1185行目付近）の直前に追加:

```javascript
    groupName(id) {
      const g = this.groups.find(g => g.id === id);
      return g ? g.name : '';
    },
    async loadGroups() {
      try { this.groups = await portalFetch('GET', '/me/groups') || []; }
      catch(e) { this.error = e.message; }
    },
    openGroupForm() {
      this.editingGroupId = null;
      this.groupForm = { name: '', description: '' };
      this.groupError = '';
      this.showGroupForm = true;
    },
    editGroup(g) {
      this.editingGroupId = g.id;
      this.groupForm = { name: g.name, description: g.description || '' };
      this.groupError = '';
      this.showGroupForm = true;
    },
    closeGroupForm() {
      this.showGroupForm = false;
      this.editingGroupId = null;
      this.groupError = '';
    },
    async saveGroup() {
      this.groupError = '';
      try {
        if (this.editingGroupId) {
          await portalFetch('PATCH', `/me/groups/${this.editingGroupId}`, this.groupForm);
        } else {
          await portalFetch('POST', '/me/groups', this.groupForm);
        }
        await this.loadGroups();
        this.closeGroupForm();
      } catch(e) {
        this.groupError = e.message || '保存に失敗しました';
      }
    },
    async deleteGroup(g) {
      if (!confirm(`グループ "${g.name}" を削除しますか？`)) return;
      try {
        await portalFetch('DELETE', `/me/groups/${g.id}`);
        await this.loadGroups();
      } catch(e) {
        this.error = 'このグループを削除できませんでした。使用中のアラートルールがある場合は、先にルールの対象を変更してください。';
      }
    },
    async assignDeviceGroup(device, groupId) {
      try {
        const updated = await portalFetch('PATCH', `/me/devices/${device.device_id}`, { group_id: groupId });
        device.group_id = updated.group_id;
      } catch(e) {
        this.error = e.message || 'グループ割当に失敗しました';
        await this.loadDevices();
      }
    },
```

- [ ] **Step 4: `init()` で `loadGroups()` を呼ぶ**

`async init() { ... await this.loadPanelConfigs(); this.loadSensorKeys(); ... }`（1209〜1229行目付近）の `await this.loadPanelConfigs();` の直後に追加:

```javascript
        await this.loadPanelConfigs();
        this.loadSensorKeys();
        this.loadGroups();
```

- [ ] **Step 5: グループ管理タブのHTMLを追加する**

`<!-- ===== 公開ダッシュボード（管理者のみ） ===== -->`（695行目付近）の直前に追加:

```html
          <!-- ===== グループ管理 ===== -->
          <div x-show="activeTab === 'groups'">
            <div class="flex items-center justify-between mb-4">
              <h3 class="font-medium text-gray-700">デバイスグループ</h3>
              <button x-show="isAdmin" @click="openGroupForm()"
                      class="bg-amber-500 hover:bg-amber-600 text-white px-3 py-1.5 rounded text-sm">
                + グループ追加
              </button>
            </div>
            <div x-show="showGroupForm" class="bg-white rounded border border-gray-200 p-4 mb-4">
              <p class="text-sm font-medium text-gray-700 mb-3" x-text="editingGroupId ? 'グループ編集' : 'グループ追加'"></p>
              <div x-show="groupError" class="mb-3 text-sm text-red-600" x-text="groupError"></div>
              <form @submit.prevent="saveGroup" class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs font-medium text-gray-600 mb-1">名前</label>
                  <input type="text" x-model="groupForm.name" required maxlength="100"
                         class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                </div>
                <div>
                  <label class="block text-xs font-medium text-gray-600 mb-1">説明（任意）</label>
                  <input type="text" x-model="groupForm.description"
                         class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                </div>
                <div class="col-span-2 flex justify-end gap-2">
                  <button type="button" @click="closeGroupForm()" class="text-sm text-gray-500 px-3 py-1.5">キャンセル</button>
                  <button type="submit" class="bg-amber-500 text-white text-sm px-3 py-1.5 rounded"
                          x-text="editingGroupId ? '更新' : '作成'"></button>
                </div>
              </form>
            </div>
            <div class="bg-white rounded shadow-sm border border-gray-200">
              <div x-show="groups.length === 0" class="py-8 text-center text-gray-400 text-sm">グループがありません</div>
              <table x-show="groups.length > 0" class="w-full text-sm">
                <thead class="bg-gray-50 border-b border-gray-100">
                  <tr>
                    <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">名前</th>
                    <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">説明</th>
                    <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">作成日</th>
                    <th class="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                  <template x-for="g in groups" :key="g.id">
                    <tr>
                      <td class="px-4 py-3 font-medium" x-text="g.name"></td>
                      <td class="px-4 py-3 text-gray-500" x-text="g.description || '-'"></td>
                      <td class="px-4 py-3 text-gray-400" x-text="g.created_at ? new Date(g.created_at).toLocaleDateString('ja-JP') : '-'"></td>
                      <td class="px-4 py-3 text-right space-x-3">
                        <button x-show="isAdmin" @click="editGroup(g)" class="text-amber-600 hover:text-amber-700 text-xs">編集</button>
                        <button x-show="isAdmin" @click="deleteGroup(g)" class="text-red-500 hover:text-red-700 text-xs">削除</button>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </div>

```

- [ ] **Step 6: デバイスタブにグループ列を追加する**

`admin-ui/tenant-portal.html` のデバイステーブルヘッダ（375〜378行目付近）を変更:

```html
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">FWバージョン</th>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">グループ</th>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">証明書期限</th>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">登録日時</th>
                      <th class="px-4 py-3"></th>
```

デバイス行（391〜392行目付近、`FWバージョン` の `<td>` の直後）に追加:

```html
                        <td class="px-4 py-3 text-gray-500" x-text="d.fw_version ?? '-'"></td>
                        <td class="px-4 py-3">
                          <select x-show="canEdit" :value="d.group_id || ''"
                                  @change="assignDeviceGroup(d, $event.target.value || null)"
                                  class="text-xs border border-gray-300 rounded px-1.5 py-1">
                            <option value="">未所属</option>
                            <template x-for="g in groups" :key="g.id">
                              <option :value="g.id" x-text="g.name"></option>
                            </template>
                          </select>
                          <span x-show="!canEdit" class="text-xs text-gray-500" x-text="groupName(d.group_id) || '未所属'"></span>
                        </td>
```

- [ ] **Step 7: `npm run build:css` を実行する**

リポジトリルートで実行:

```bash
npm run build:css
```

- [ ] **Step 8: 既存Pythonテストスイートに回帰がないことを確認する**

```bash
cd core-api
export POSTGRES_DSN="postgresql://iotadmin:c8c7fa9e0272daa73ad9c61839a2a46aeba26bceae43bf26@localhost:5432/iotplatform"
python -m pytest tests/ -q
```

Expected: 既知の無関係な7件のみ失敗（test_auth/test_db/test_emqx/test_emqx_publisher/test_provisioning/test_tenant_auth/test_tenant_devices）、新規failedなし（本タスクはHTML/JSのみでcore-apiは無変更のため影響ないはず）

- [ ] **Step 9: コミットする**

```bash
git add admin-ui/tenant-portal.html admin-ui/static/tailwind.css
git commit -m "feat(device-groups-ui): テナントポータルにグループ管理タブとデバイス割当UIを追加"
```

---

### Task 2: テナントポータル — アラートルール対象選択・ファームウェアOTAのグループ絞り込み・ダッシュボード設定のグループセレクタ

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: Task1の `groups` state・`loadGroups()`・`groupName(id)`。既存の `AlertRuleCreate`/`AlertRuleUpdate` の `group_id` フィールド（`docs/superpowers/plans/2026-09-08-device-groups-backend.md` Task4）。`GET/PUT /tenant-portal/dashboard/panel-configs?group_id=...`（Task6）

- [ ] **Step 1: アラートルールの `newRule` state 定義に `group_id` と `targetType` を追加する**

`newRule: { sensor_key: '', device_id: '', condition: 'above', threshold: null, trigger_mode: 'consecutive', consecutive_count: 3, duration_sec: 60, severity: 'warning', notify_emails: [] },`（1154〜1156行目、`state` オブジェクトのトップレベル初期値定義）を変更:

```javascript
    newRule: { sensor_key: '', device_id: '', group_id: null, condition: 'above', threshold: null,
               trigger_mode: 'consecutive', consecutive_count: 3, duration_sec: 60,
               severity: 'warning', notify_emails: [] },
    targetType: 'all', // 'device' | 'group' | 'all'
```

（`openAlertForm()` と `editAlertRule()` 内にも同じ形の `newRule` 再代入があるが、これらはStep 3・Step 4で全文置き換えるためこのStepでは触らない）

- [ ] **Step 2: `setTargetType()` ヘルパーを追加する**

`clearDevice() { ... }`（1295〜1299行目）の直後に追加:

```javascript
    setTargetType(type) {
      this.targetType = type;
      if (type !== 'device') { this.newRule.device_id = ''; this.deviceSearch = ''; }
      if (type !== 'group')  { this.newRule.group_id = null; }
    },
```

- [ ] **Step 3: `openAlertForm()` を変更する（対象タイプのリセットとグループ読み込み）**

`async openAlertForm() { ... }`（1311〜1328行目）を次のように変更:

```javascript
    async openAlertForm() {
      this.editingRuleId = null;
      this.sensorKeySelect = '';
      this.deviceSearch = '';
      this.targetType = 'all';
      this.newRule = { sensor_key: '', device_id: '', group_id: null, condition: 'above', threshold: null,
                       trigger_mode: 'consecutive', consecutive_count: 3, duration_sec: 60,
                       severity: 'warning', notify_emails: [] };
      this.emailsInput = '';
      this.showAlertForm = true;
      try {
        const [keys, devs] = await Promise.all([
          portalFetch('GET', '/me/sensor-keys'),
          portalFetch('GET', '/me/devices'),
        ]);
        this.sensorKeys = keys || [];
        this.alertDevices = devs || [];
      } catch(_) {}
    },
```

- [ ] **Step 4: `editAlertRule()` を変更する（既存ルールの対象タイプを復元）**

`async editAlertRule(rule) { ... }`（1337〜1363行目）を次のように変更:

```javascript
    async editAlertRule(rule) {
      this.editingRuleId = rule.id;
      this.sensorKeySelect = '__other__';
      this.deviceSearch = rule.device_id || '';
      this.targetType = rule.device_id ? 'device' : (rule.group_id ? 'group' : 'all');
      this.newRule = {
        sensor_key: rule.sensor_key, device_id: rule.device_id || '', group_id: rule.group_id || null,
        condition: rule.condition, threshold: rule.threshold,
        trigger_mode: rule.trigger_mode, consecutive_count: rule.consecutive_count,
        duration_sec: rule.duration_sec, severity: rule.severity,
        notify_emails: rule.notify_emails || [],
      };
      this.emailsInput = (rule.notify_emails || []).join(', ');
      this.showAlertForm = true;
      try {
        const [keys, devs] = await Promise.all([
          portalFetch('GET', '/me/sensor-keys'),
          portalFetch('GET', '/me/devices'),
        ]);
        this.sensorKeys = keys || [];
        this.alertDevices = devs || [];
        this.sensorKeySelect = this.sensorKeys.includes(rule.sensor_key)
          ? rule.sensor_key : '__other__';
        if (rule.device_id) {
          const d = this.alertDevices.find(d => d.device_id === rule.device_id);
          this.deviceSearch = d ? (d.device_name || d.device_id) : rule.device_id;
        }
      } catch(_) {}
    },
```

- [ ] **Step 5: アラートフォームHTMLに対象選択ラジオボタンとグループ選択を追加する**

`<label class="block text-xs font-medium text-gray-600 mb-1">デバイス（空=全デバイス）</label>` を含む `<div>` 全体（440〜469行目）を、対象選択ラジオボタン + 条件付き表示に変更:

```html
                <div class="col-span-2">
                  <label class="block text-xs font-medium text-gray-600 mb-1">対象</label>
                  <div class="flex gap-4 mb-2">
                    <label class="flex items-center gap-1.5 text-sm">
                      <input type="radio" value="all" x-model="targetType" @change="setTargetType('all')"> 全テナント
                    </label>
                    <label class="flex items-center gap-1.5 text-sm">
                      <input type="radio" value="device" x-model="targetType" @change="setTargetType('device')"> デバイス
                    </label>
                    <label class="flex items-center gap-1.5 text-sm">
                      <input type="radio" value="group" x-model="targetType" @change="setTargetType('group')"> グループ
                    </label>
                  </div>
                  <div x-show="targetType === 'device'" class="relative">
                    <input type="text" x-model="deviceSearch" autocomplete="off"
                           @focus="showDeviceDropdown = true"
                           @blur="onDeviceBlur()"
                           placeholder="デバイス名/IDで検索"
                           class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm pr-6">
                    <button type="button" x-show="deviceSearch"
                            @click="clearDevice()"
                            class="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-xs leading-none">✕</button>
                    <div x-show="showDeviceDropdown"
                         class="absolute z-20 w-full bg-white border border-gray-200 rounded shadow-lg mt-0.5 max-h-48 overflow-y-auto">
                      <template x-for="d in filteredDevices()" :key="d.device_id">
                        <button type="button" @mousedown.prevent @click="selectDevice(d)"
                                :class="d.device_id === newRule.device_id ? 'bg-blue-50' : 'hover:bg-blue-50'"
                                class="w-full text-left px-3 py-2 text-sm">
                          <span class="font-medium" x-text="d.device_name || d.device_id"></span>
                          <span x-show="d.device_name" class="text-xs text-gray-400 ml-1" x-text="d.device_id"></span>
                        </button>
                      </template>
                      <div x-show="filteredDevices().length === 0"
                           class="px-3 py-2 text-sm text-gray-400 text-center">一致するデバイスがありません</div>
                    </div>
                  </div>
                  <select x-show="targetType === 'group'" x-model="newRule.group_id"
                          class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                    <option :value="null">-- 選択してください --</option>
                    <template x-for="g in groups" :key="g.id">
                      <option :value="g.id" x-text="g.name"></option>
                    </template>
                  </select>
                </div>
```

注: 元のコードは2カラムグリッド内で「センサーキー」と並んで1カラムを占めていたが、対象選択は選択肢が増えるため `col-span-2` にして独立した行にする。

- [ ] **Step 6: アラートルール一覧テーブルの表示を対象種別に対応させる**

センサー列（538行目）を変更:

```html
                      <td class="px-4 py-3 text-sm font-medium" x-text="r.sensor_key + (r.device_id ? ' / ' + r.device_id : (r.group_id ? ' / [グループ] ' + groupName(r.group_id) : ''))"></td>
```

- [ ] **Step 7: ファームウェアOTAダイアログにグループ絞り込みを追加する**

state に追加（`otaTargetFirmware: null, otaDevices: [], otaSelectedIds: {},`の行、1160行目付近）:

```javascript
    otaTargetFirmware: null, otaDevices: [], otaSelectedIds: {}, otaGroupFilter: '',
```

`sendOta(id, version)`（1405〜1417行目）に `otaGroupFilter` のリセットを追加:

```javascript
    async sendOta(id, version) {
      this.otaTargetFirmware = { id, version };
      this.otaSelectedIds = {};
      this.otaGroupFilter = '';
      this.otaResults = [];
      this.otaLoading = true;
      try {
        this.otaDevices = await portalFetch('GET', '/me/devices') || [];
      } catch(e) {
        this.error = e.message;
      } finally {
        this.otaLoading = false;
      }
    },
```

`otaToggleDevice(...)`（1430〜1432行目）の直後に追加:

```javascript
    otaApplyGroupFilter() {
      if (!this.otaGroupFilter) { this.otaToggleAll(false); return; }
      const ids = {};
      this.otaDevices.filter(d => d.group_id === this.otaGroupFilter).forEach(d => { ids[d.device_id] = true; });
      this.otaSelectedIds = ids;
    },
```

`otaClose()`（1436〜1442行目）に `otaGroupFilter` のリセットを追加:

```javascript
    otaClose() {
      if (this.otaSubmitting) return;
      this.otaTargetFirmware = null;
      this.otaDevices = [];
      this.otaSelectedIds = {};
      this.otaGroupFilter = '';
      this.otaResults = [];
    },
```

OTAダイアログHTML（626〜647行目、「全選択」チェックボックスの直前）に絞り込みセレクトを追加:

```html
                <div x-show="!otaLoading">
                  <!-- グループで絞り込み -->
                  <div class="mb-3">
                    <label class="block text-xs font-medium text-gray-600 mb-1">グループで絞り込み</label>
                    <select x-model="otaGroupFilter" @change="otaApplyGroupFilter()"
                            class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                      <option value="">すべてのデバイス</option>
                      <template x-for="g in groups" :key="g.id">
                        <option :value="g.id" x-text="g.name"></option>
                      </template>
                    </select>
                  </div>
                  <!-- 全選択 -->
```

（既存の `<div x-show="!otaLoading">` タグを再利用し、その内側先頭に上記を挿入する。既存の「全選択」ブロック以降はそのまま）

- [ ] **Step 8: ダッシュボード設定タブにグループセレクタを追加する**

state に追加（`panelConfigs: [],` の行、1168行目付近）:

```javascript
    // ダッシュボード設定
    panelConfigs: [],
    dashboardConfigGroupId: '',
```

`loadPanelConfigs()`（1539〜1545行目）を変更:

```javascript
    async loadPanelConfigs() {
      try {
        const qs = this.dashboardConfigGroupId ? ('?group_id=' + this.dashboardConfigGroupId) : '';
        this.panelConfigs = await portalFetch('GET', '/dashboard/panel-configs' + qs) || [];
      } catch(e) {
        // 設定なしは空配列なので無視
      }
    },
```

`savePanelConfigs()`（1564〜1580行目）を変更:

```javascript
    async savePanelConfigs() {
      this.panelConfigSaving = true;
      this.panelConfigError = '';
      try {
        const qs = this.dashboardConfigGroupId ? ('?group_id=' + this.dashboardConfigGroupId) : '';
        await portalFetch('PUT', '/dashboard/panel-configs' + qs, this.panelConfigs);
        if (!this.dashboardConfigGroupId) {
          this.switchTab('dashboard');
          // iframe を強制リロード
          this.$nextTick(() => {
            const iframe = document.querySelector('iframe');
            if (iframe) iframe.src = iframe.src;
          });
        }
      } catch(e) {
        this.panelConfigError = 'ダッシュボードの更新に失敗しました: ' + (e.message || e);
      } finally {
        this.panelConfigSaving = false;
      }
    },
```

注: グループ別設定を保存した場合はGrafana同期が発生しない（バックエンド仕様）ため、ダッシュボードタブへの自動切り替え・iframeリロードはテナント全体設定（`dashboardConfigGroupId` が空）のときのみ行う。

ダッシュボード設定タブHTML（`<h3 class="font-medium text-gray-700">センサー別チャートタイプ設定</h3>` を含む見出しブロック、747〜750行目）の直後にセレクタを追加:

```html
            <div class="mb-4">
              <label class="block text-xs font-medium text-gray-600 mb-1">対象</label>
              <select x-model="dashboardConfigGroupId" @change="loadPanelConfigs()"
                      class="w-full max-w-xs border border-gray-300 rounded px-2 py-1.5 text-sm">
                <option value="">テナント全体（デフォルト）</option>
                <template x-for="g in groups" :key="g.id">
                  <option :value="g.id" x-text="g.name"></option>
                </template>
              </select>
            </div>
```

- [ ] **Step 9: `npm run build:css` を実行する**

```bash
npm run build:css
```

- [ ] **Step 10: 既存Pythonテストスイートに回帰がないことを確認する**

```bash
cd core-api
export POSTGRES_DSN="postgresql://iotadmin:c8c7fa9e0272daa73ad9c61839a2a46aeba26bceae43bf26@localhost:5432/iotplatform"
python -m pytest tests/ -q
```

Expected: 既知の無関係な7件のみ失敗、新規failedなし

- [ ] **Step 11: コミットする**

```bash
git add admin-ui/tenant-portal.html admin-ui/static/tailwind.css
git commit -m "feat(device-groups-ui): テナントポータルのアラート対象選択・OTAグループ絞り込み・ダッシュボード設定グループセレクタを追加"
```

---

### Task 3: PF管理者画面 — グループ管理タブ + デバイスのグループ割当

**Files:**
- Modify: `platform-ui/tenant.html`

**Interfaces:**
- Consumes: `GET/POST /tenants/{tenant_id}/groups`, `PATCH/DELETE /tenants/{tenant_id}/groups/{group_id}`, `PATCH /tenants/{tenant_id}/devices/{device_id}` body `{group_id: uuid|null}`（`docs/superpowers/plans/2026-09-08-device-groups-backend.md` Task2/3）。呼び出しは既存の `api.request(method, path, body)` ヘルパーを使う（`admin-ui/js/api.js` の変更は不要）
- Produces: `groups` state配列と `loadGroups()`／`groupName(id)` ヘルパー。Task4はこれを再利用する

- [ ] **Step 1: タブナビゲーションに「グループ管理」を追加する**

`platform-ui/tenant.html` のタブボタン群（63〜83行目）、「デバイス」ボタンの直前に追加:

```html
        <button @click="activeTab = 'groups'; loadGroups()"
                :class="activeTab === 'groups' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'"
                class="px-4 py-2 text-sm font-medium">グループ管理</button>
```

- [ ] **Step 2: state に groups 関連プロパティを追加する**

`devicesLoading: false,`（888行目付近）の直後に追加:

```javascript
        groups: [],
        showGroupForm: false,
        editingGroupId: null,
        groupForm: { name: '', description: '' },
        groupError: '',
```

- [ ] **Step 3: グループCRUDのメソッドを追加する**

`async loadDevices() { ... }`（958〜968行目）の直前に追加:

```javascript
        groupName(id) {
          const g = this.groups.find(g => g.id === id);
          return g ? g.name : '';
        },

        async loadGroups() {
          try {
            this.groups = await api.request('GET', `/tenants/${tenantId}/groups`);
          } catch(e) {
            this.error = e.message;
          }
        },

        openGroupForm() {
          this.editingGroupId = null;
          this.groupForm = { name: '', description: '' };
          this.groupError = '';
          this.showGroupForm = true;
        },

        editGroup(g) {
          this.editingGroupId = g.id;
          this.groupForm = { name: g.name, description: g.description || '' };
          this.groupError = '';
          this.showGroupForm = true;
        },

        closeGroupForm() {
          this.showGroupForm = false;
          this.editingGroupId = null;
          this.groupError = '';
        },

        async saveGroup() {
          this.groupError = '';
          try {
            if (this.editingGroupId) {
              await api.request('PATCH', `/tenants/${tenantId}/groups/${this.editingGroupId}`, this.groupForm);
            } else {
              await api.request('POST', `/tenants/${tenantId}/groups`, this.groupForm);
            }
            await this.loadGroups();
            this.closeGroupForm();
          } catch(e) {
            this.groupError = e.message || '保存に失敗しました';
          }
        },

        async deleteGroup(g) {
          if (!confirm(`グループ "${g.name}" を削除しますか？`)) return;
          try {
            await api.request('DELETE', `/tenants/${tenantId}/groups/${g.id}`);
            await this.loadGroups();
          } catch(e) {
            this.error = 'このグループを削除できませんでした。使用中のアラートルールがある場合は、先にルールの対象を変更してください。';
          }
        },

        async assignDeviceGroup(device, groupId) {
          try {
            const updated = await api.request('PATCH', `/tenants/${tenantId}/devices/${device.device_id}`, { group_id: groupId });
            device.group_id = updated.group_id;
          } catch(e) {
            this.error = e.message || 'グループ割当に失敗しました';
            await this.loadDevices();
          }
        },
```

- [ ] **Step 4: `init()` で `loadGroups()` を呼ぶ**

`async init() { ... }`（660〜686行目）の `this.loading = false;` の直前（`finally` ブロック内）に追加:

```javascript
          } finally {
            this.loading = false;
            this.loadGroups();
          }
```

- [ ] **Step 5: グループ管理タブのHTMLを追加する**

`<!-- ファームウェアタブ -->`（531行目）の直前に追加:

```html
      <!-- グループ管理タブ -->
      <div x-show="activeTab === 'groups'">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-medium text-gray-700">デバイスグループ</h3>
          <button @click="openGroupForm()"
                  class="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm">
            + グループ追加
          </button>
        </div>
        <div x-show="showGroupForm" class="bg-white rounded-xl border border-gray-200 p-4 mb-4">
          <p class="text-sm font-medium text-gray-700 mb-3" x-text="editingGroupId ? 'グループ編集' : 'グループ追加'"></p>
          <div x-show="groupError" class="mb-3 text-sm text-red-600" x-text="groupError"></div>
          <form @submit.prevent="saveGroup" class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-medium text-gray-600 mb-1">名前</label>
              <input type="text" x-model="groupForm.name" required maxlength="100"
                     class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-600 mb-1">説明（任意）</label>
              <input type="text" x-model="groupForm.description"
                     class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
            </div>
            <div class="col-span-2 flex justify-end gap-2">
              <button type="button" @click="closeGroupForm()" class="text-sm text-gray-500 px-3 py-1.5">キャンセル</button>
              <button type="submit" class="bg-blue-600 text-white text-sm px-3 py-1.5 rounded"
                      x-text="editingGroupId ? '更新' : '作成'"></button>
            </div>
          </form>
        </div>
        <div class="bg-white rounded-xl shadow-sm border border-gray-200">
          <div x-show="groups.length === 0" class="py-8 text-center text-gray-400 text-sm">グループがありません</div>
          <table x-show="groups.length > 0" class="w-full text-sm">
            <thead class="bg-gray-50 border-b border-gray-100">
              <tr>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">名前</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">説明</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">作成日</th>
                <th class="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <template x-for="g in groups" :key="g.id">
                <tr>
                  <td class="px-4 py-3 font-medium" x-text="g.name"></td>
                  <td class="px-4 py-3 text-gray-500" x-text="g.description || '-'"></td>
                  <td class="px-4 py-3 text-gray-400" x-text="g.created_at ? new Date(g.created_at).toLocaleDateString('ja-JP') : '-'"></td>
                  <td class="px-4 py-3 text-right space-x-3">
                    <button @click="editGroup(g)" class="text-blue-500 hover:text-blue-700 text-xs">編集</button>
                    <button @click="deleteGroup(g)" class="text-red-500 hover:text-red-700 text-xs">削除</button>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>

```

- [ ] **Step 6: デバイスタブにグループ列を追加する**

デバイステーブルヘッダ（486〜493行目付近）を変更:

```html
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">FWバージョン</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">グループ</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">証明書期限</th>
                <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">登録日時</th>
                <th class="px-4 py-3"></th>
```

デバイス行（513行目、FWバージョンの `<td>` の直後）に追加:

```html
                  <td class="px-4 py-3 text-gray-500" x-text="d.fw_version ?? '-'"></td>
                  <td class="px-4 py-3">
                    <select :value="d.group_id || ''"
                            @change="assignDeviceGroup(d, $event.target.value || null)"
                            class="text-xs border border-gray-300 rounded px-1.5 py-1">
                      <option value="">未所属</option>
                      <template x-for="g in groups" :key="g.id">
                        <option :value="g.id" x-text="g.name"></option>
                      </template>
                    </select>
                  </td>
```

- [ ] **Step 7: `npm run build:css` を実行する**

```bash
npm run build:css
```

- [ ] **Step 8: 既存Pythonテストスイートに回帰がないことを確認する**

```bash
cd core-api
export POSTGRES_DSN="postgresql://iotadmin:c8c7fa9e0272daa73ad9c61839a2a46aeba26bceae43bf26@localhost:5432/iotplatform"
python -m pytest tests/ -q
```

Expected: 既知の無関係な7件のみ失敗、新規failedなし

- [ ] **Step 9: コミットする**

```bash
git add platform-ui/tenant.html admin-ui/static/tailwind.css
git commit -m "feat(device-groups-ui): PF管理者画面にグループ管理タブとデバイス割当UIを追加"
```

---

### Task 4: PF管理者画面 — アラートルール対象選択・ファームウェアOTAのグループ絞り込み

**Files:**
- Modify: `platform-ui/tenant.html`

**Interfaces:**
- Consumes: Task3の `groups` state・`loadGroups()`・`groupName(id)`。既存の `AlertRuleCreate`/`AlertRuleUpdate` の `group_id` フィールド

- [ ] **Step 1: `newRule` に `group_id` を追加し、対象選択の state を追加する**

`newRule: { sensor_key: '', device_id: '', condition: 'above', threshold: null, trigger_mode: 'consecutive', consecutive_count: 3, duration_sec: 60, severity: 'warning', notify_emails: [], },`（650〜654行目）を変更:

```javascript
        newRule: {
          sensor_key: '', device_id: '', group_id: null, condition: 'above', threshold: null,
          trigger_mode: 'consecutive', consecutive_count: 3, duration_sec: 60,
          severity: 'warning', notify_emails: [],
        },
        targetType: 'all', // 'device' | 'group' | 'all'
```

- [ ] **Step 2: `setTargetType()` ヘルパーを追加する**

`clearDevice() { ... }`（742〜746行目）の直後に追加:

```javascript
        setTargetType(type) {
          this.targetType = type;
          if (type !== 'device') { this.newRule.device_id = ''; this.deviceSearch = ''; }
          if (type !== 'group')  { this.newRule.group_id = null; }
        },
```

- [ ] **Step 3: `openAlertForm()` を変更する**

`async openAlertForm() { ... }`（760〜779行目）を変更:

```javascript
        async openAlertForm() {
          this.editingRuleId = null;
          this.sensorKeySelect = '';
          this.deviceSearch = '';
          this.targetType = 'all';
          this.newRule = {
            sensor_key: '', device_id: '', group_id: null, condition: 'above', threshold: null,
            trigger_mode: 'consecutive', consecutive_count: 3, duration_sec: 60,
            severity: 'warning', notify_emails: [],
          };
          this.emailsInput = '';
          this.showAlertForm = true;
          try {
            const [keys, devs] = await Promise.all([
              api.alertRules.sensorKeys(tenantId),
              api.tenantDevices.list(tenantId),
            ]);
            this.sensorKeys = keys || [];
            this.devices = devs || [];
          } catch(_) {}
        },
```

- [ ] **Step 4: `editAlertRule()` を変更する**

`async editAlertRule(rule) { ... }`（790〜821行目）を変更:

```javascript
        async editAlertRule(rule) {
          this.editingRuleId = rule.id;
          this.sensorKeySelect = '__other__';
          this.deviceSearch = rule.device_id || '';
          this.targetType = rule.device_id ? 'device' : (rule.group_id ? 'group' : 'all');
          this.newRule = {
            sensor_key: rule.sensor_key,
            device_id: rule.device_id || '',
            group_id: rule.group_id || null,
            condition: rule.condition,
            threshold: rule.threshold,
            trigger_mode: rule.trigger_mode,
            consecutive_count: rule.consecutive_count,
            duration_sec: rule.duration_sec,
            severity: rule.severity,
            notify_emails: rule.notify_emails || [],
          };
          this.emailsInput = (rule.notify_emails || []).join(', ');
          this.showAlertForm = true;
          try {
            const [keys, devs] = await Promise.all([
              api.alertRules.sensorKeys(tenantId),
              api.tenantDevices.list(tenantId),
            ]);
            this.sensorKeys = keys || [];
            this.devices = devs || [];
            this.sensorKeySelect = this.sensorKeys.includes(rule.sensor_key)
              ? rule.sensor_key : '__other__';
            if (rule.device_id) {
              const d = this.devices.find(d => d.device_id === rule.device_id);
              this.deviceSearch = d ? (d.device_name || d.device_id) : rule.device_id;
            }
          } catch(_) {}
        },
```

- [ ] **Step 5: アラートフォームHTMLに対象選択ラジオボタンとグループ選択を追加する**

`<label class="block text-xs font-medium text-gray-600 mb-1">デバイス（空=全デバイス）</label>` を含む `<div>` 全体（199〜228行目）を変更:

```html
            <div class="col-span-2">
              <label class="block text-xs font-medium text-gray-600 mb-1">対象</label>
              <div class="flex gap-4 mb-2">
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="radio" value="all" x-model="targetType" @change="setTargetType('all')"> 全テナント
                </label>
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="radio" value="device" x-model="targetType" @change="setTargetType('device')"> デバイス
                </label>
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="radio" value="group" x-model="targetType" @change="setTargetType('group')"> グループ
                </label>
              </div>
              <div x-show="targetType === 'device'" class="relative">
                <input type="text" x-model="deviceSearch" autocomplete="off"
                       @focus="showDeviceDropdown = true"
                       @blur="onDeviceBlur()"
                       placeholder="デバイス名/IDで検索"
                       class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm pr-6">
                <button type="button" x-show="deviceSearch"
                        @click="clearDevice()"
                        class="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-xs leading-none">✕</button>
                <div x-show="showDeviceDropdown"
                     class="absolute z-20 w-full bg-white border border-gray-200 rounded shadow-lg mt-0.5 max-h-48 overflow-y-auto">
                  <template x-for="d in filteredDevices()" :key="d.device_id">
                    <button type="button" @mousedown.prevent @click="selectDevice(d)"
                            :class="d.device_id === newRule.device_id ? 'bg-blue-50' : 'hover:bg-blue-50'"
                            class="w-full text-left px-3 py-2 text-sm">
                      <span class="font-medium" x-text="d.device_name || d.device_id"></span>
                      <span x-show="d.device_name" class="text-xs text-gray-400 ml-1" x-text="d.device_id"></span>
                    </button>
                  </template>
                  <div x-show="filteredDevices().length === 0"
                       class="px-3 py-2 text-sm text-gray-400 text-center">一致するデバイスがありません</div>
                </div>
              </div>
              <select x-show="targetType === 'group'" x-model="newRule.group_id"
                      class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                <option :value="null">-- 選択してください --</option>
                <template x-for="g in groups" :key="g.id">
                  <option :value="g.id" x-text="g.name"></option>
                </template>
              </select>
            </div>
```

- [ ] **Step 6: アラートルール一覧テーブルの表示を対象種別に対応させる**

センサー列（302行目）を変更:

```html
                  <td class="px-4 py-3 text-sm font-medium" x-text="r.sensor_key + (r.device_id ? ' / ' + r.device_id : (r.group_id ? ' / [グループ] ' + groupName(r.group_id) : ''))"></td>
```

- [ ] **Step 7: ファームウェアOTAダイアログにグループ絞り込みを追加する**

PF管理者側のOTAダイアログはデバイスID手入力のみ（複数選択機能がない）。グループ絞り込みで「対象デバイスをグループから選ぶ」体験を提供するため、ダイアログを1台ずつのテキスト入力からグループ選択＋一括送信（バックエンドの一括OTAエンドポイントを直接呼ぶ）に変更する。テナントポータルと異なり、こちらは既存に複数選択の仕組みが無いため、新規に一括APIを呼ぶ方式を採用する。

state に追加（`otaDeviceId: '',` の行、872行目付近）:

```javascript
        otaDeviceId: '',
        otaGroupId: '',
        otaSubmitting: false,
        otaResults: [],
```

`sendOta(firmwareId, version)`（1056〜1059行目）を変更:

```javascript
        sendOta(firmwareId, version) {
          this.otaTargetFirmware = { id: firmwareId, version };
          this.otaDeviceId = '';
          this.otaGroupId = '';
          this.otaResults = [];
        },
```

`async submitOta() { ... }`（1061〜1072行目付近、実際の行番号は`grep`で確認すること）を変更:

```javascript
        async submitOta() {
          if (!this.otaTargetFirmware) return;
          this.otaResults = [];
          if (this.otaGroupId) {
            this.otaSubmitting = true;
            try {
              const resp = await api.request('POST', `/tenants/${tenantId}/groups/${this.otaGroupId}/ota`,
                { firmware_id: this.otaTargetFirmware.id });
              this.otaResults = resp.results || [];
            } catch(e) {
              this.error = e.message;
            } finally {
              this.otaSubmitting = false;
            }
            return;
          }
          if (!this.otaDeviceId) return;
          try {
            await api.request('POST', `/tenants/${tenantId}/devices/${this.otaDeviceId}/ota`,
              { firmware_id: this.otaTargetFirmware.id });
            this.otaDeviceId = '';
            this.otaTargetFirmware = null;
          } catch(e) {
            this.error = e.message;
          }
        },
```

注: グループ送信時はレスポンスの `results`（デバイス単位の成功/失敗一覧）を表示するため、送信後もダイアログを閉じない。単体デバイス送信時は既存動作（送信後にダイアログを閉じる）を維持する。`submitOta()` の直前に実際に存在する既存コードを `grep -n "async submitOta" platform-ui/tenant.html` で確認し、既存の変数名・挙動と齟齬がないようにすること。

OTAダイアログHTML（604〜620行目）を変更:

```html
        <!-- OTA送信ダイアログ -->
        <div x-show="otaTargetFirmware" class="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50">
          <div class="bg-white rounded-xl p-6 w-full max-w-sm shadow-xl">
            <h3 class="font-medium text-gray-800 mb-4" x-text="'OTA送信: v' + otaTargetFirmware?.version"></h3>
            <div class="mb-3">
              <label class="block text-sm font-medium text-gray-600 mb-1">グループへ一括送信（任意）</label>
              <select x-model="otaGroupId" :disabled="!!otaDeviceId"
                      class="w-full border border-gray-300 rounded px-3 py-2 text-sm">
                <option value="">-- グループを選択しない --</option>
                <template x-for="g in groups" :key="g.id">
                  <option :value="g.id" x-text="g.name"></option>
                </template>
              </select>
            </div>
            <div class="mb-4">
              <label class="block text-sm font-medium text-gray-600 mb-1">または個別デバイスID</label>
              <input type="text" x-model="otaDeviceId" placeholder="device-001" :disabled="!!otaGroupId"
                     class="w-full border border-gray-300 rounded px-3 py-2 text-sm">
            </div>
            <div x-show="otaResults.length > 0" class="mb-4 max-h-40 overflow-y-auto space-y-1">
              <template x-for="r in otaResults" :key="r.device_id">
                <div class="text-xs px-2 py-1 rounded"
                     :class="r.status === 'dispatched' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'"
                     x-text="r.device_id + ': ' + (r.status === 'dispatched' ? '送信完了' : '送信失敗')"></div>
              </template>
            </div>
            <div class="flex justify-end gap-2">
              <button @click="otaTargetFirmware = null; otaDeviceId = ''; otaGroupId = ''; otaResults = []"
                      :disabled="otaSubmitting"
                      class="text-sm text-gray-500 px-3 py-1.5">キャンセル</button>
              <button @click="submitOta()" :disabled="(!otaDeviceId && !otaGroupId) || otaSubmitting"
                      class="bg-blue-600 disabled:bg-blue-300 text-white text-sm px-3 py-1.5 rounded">
                <span x-show="!otaSubmitting">送信</span>
                <span x-show="otaSubmitting">送信中...</span>
              </button>
            </div>
          </div>
        </div>
```

- [ ] **Step 8: `npm run build:css` を実行する**

```bash
npm run build:css
```

- [ ] **Step 9: 既存Pythonテストスイートに回帰がないことを確認する**

```bash
cd core-api
export POSTGRES_DSN="postgresql://iotadmin:c8c7fa9e0272daa73ad9c61839a2a46aeba26bceae43bf26@localhost:5432/iotplatform"
python -m pytest tests/ -q
```

Expected: 既知の無関係な7件のみ失敗、新規failedなし

- [ ] **Step 10: コミットする**

```bash
git add platform-ui/tenant.html admin-ui/static/tailwind.css
git commit -m "feat(device-groups-ui): PF管理者画面のアラート対象選択とOTAグループ一括送信を追加"
```
