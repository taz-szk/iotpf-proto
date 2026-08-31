const API_BASE = '/api';
const TOKEN_KEY = 'iot_access_token';
const REFRESH_KEY = 'iot_refresh_token';

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setTokens(access, refresh) {
    localStorage.setItem(TOKEN_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
}
function clearTokens() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
}

async function request(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const resp = await fetch(`${API_BASE}${path}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : null,
    });

    if (resp.status === 401) {
        if (path === '/auth/login') {
            const err = await resp.json().catch(() => ({ detail: 'メールアドレスまたはパスワードが正しくありません' }));
            throw new Error(err.detail || 'メールアドレスまたはパスワードが正しくありません');
        }
        clearTokens();
        window.location.href = '/admin/';
        return;
    }
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'Request failed');
    }
    if (resp.status === 204) return null;
    return resp.json();
}

const api = {
    login: (email, password) => request('POST', '/auth/login', { email, password }),
    tenants: {
        list: () => request('GET', '/tenants'),
        create: (name, slug) => request('POST', '/tenants', { name, slug }),
        get: (id) => request('GET', `/tenants/${id}`),
        update: (id, name) => request('PATCH', `/tenants/${id}`, { name }),
        setStatus: (id, status) => request('PATCH', `/tenants/${id}/status`, { status }),
        delete: (id) => request('DELETE', `/tenants/${id}`),
    },
    tenantDevices: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/devices`),
        delete: (tenantId, deviceId) => request('DELETE', `/tenants/${tenantId}/devices/${deviceId}`),
    },
    provisioningTokens: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/provisioning-tokens`),
        create: (tenantId, maxDevices, expiresDays) =>
            request('POST', `/tenants/${tenantId}/provisioning-tokens`, { max_devices: maxDevices, expires_days: expiresDays }),
        revoke: (tenantId, tokenId) =>
            request('DELETE', `/tenants/${tenantId}/provisioning-tokens/${tokenId}`),
        listDevices: (tenantId, tokenId) =>
            request('GET', `/tenants/${tenantId}/provisioning-tokens/${tokenId}/devices`),
    },
    alertRules: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/alert-rules`),
        create: (tenantId, rule) => request('POST', `/tenants/${tenantId}/alert-rules`, rule),
        update: (tenantId, ruleId, rule) => request('PATCH', `/tenants/${tenantId}/alert-rules/${ruleId}`, rule),
        delete: (tenantId, ruleId) => request('DELETE', `/tenants/${tenantId}/alert-rules/${ruleId}`),
        sensorKeys: (tenantId) => request('GET', `/tenants/${tenantId}/sensor-keys`),
    },
    tenantUsers: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/users`),
        create: (tenantId, email, password, role) =>
            request('POST', `/tenants/${tenantId}/users`, { email, password, role }),
        resetPassword: (tenantId, userId, password) =>
            request('PATCH', `/tenants/${tenantId}/users/${userId}/password`, { password }),
        update: (tenantId, userId, body) =>
            request('PATCH', `/tenants/${tenantId}/users/${userId}`, body),
        delete: (tenantId, userId) =>
            request('DELETE', `/tenants/${tenantId}/users/${userId}`),
    },
    auth: {
        changePassword: (currentPassword, newPassword) =>
            request('POST', '/auth/change-password', { current_password: currentPassword, new_password: newPassword }),
    },
    platform: {
        grafanaOrgId: () => request('GET', '/tenants/platform/grafana-org-id'),
    },
    tenantAuth: {
        changePassword: (currentPassword, newPassword) =>
            fetch(`${API_BASE}/tenant-auth/change-password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
            }).then(r => { if (!r.ok) return r.json().then(e => { throw new Error(e.detail) }); }),
    },
    isLoggedIn: () => !!getToken(),
    logout: () => { clearTokens(); window.location.href = '/admin/'; },
    setTokens,
    request: (method, path, body = null) => request(method, path, body),
};

window.api = api;
