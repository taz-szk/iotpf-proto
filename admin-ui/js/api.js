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
    },
    devices: {
        // accessed via raw SQL from tenant schema — future endpoint
    },
    provisioningTokens: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/provisioning-tokens`),
        create: (tenantId, maxDevices, expiresDays) =>
            request('POST', `/tenants/${tenantId}/provisioning-tokens`, { max_devices: maxDevices, expires_days: expiresDays }),
        revoke: (tenantId, tokenId) =>
            request('DELETE', `/tenants/${tenantId}/provisioning-tokens/${tokenId}`),
    },
    alertRules: {
        list: (tenantId) => request('GET', `/tenants/${tenantId}/alert-rules`),
        create: (tenantId, rule) => request('POST', `/tenants/${tenantId}/alert-rules`, rule),
        delete: (tenantId, ruleId) => request('DELETE', `/tenants/${tenantId}/alert-rules/${ruleId}`),
    },
    isLoggedIn: () => !!getToken(),
    logout: () => { clearTokens(); window.location.href = '/admin/'; },
    setTokens,
    request: (method, path, body = null) => request(method, path, body),
};

window.api = api;
