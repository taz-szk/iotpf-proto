# TOTP リカバリ手順

## プラットフォーム管理者が認証アプリを紛失した場合

```sql
-- PostgreSQL に直接接続して実行
UPDATE platform_users SET totp_enabled = FALSE, totp_secret = NULL WHERE email = 'admin@example.com';
```

## テナントユーザーが認証アプリを紛失した場合

```sql
-- schema は tenant_{tenant_id のハイフンをアンダースコアに変換}
UPDATE "tenant_xxxxxxxx_xxxx_xxxx_xxxx_xxxxxxxxxxxx".users 
SET totp_enabled = FALSE, totp_secret = NULL 
WHERE email = 'user@example.com';
```

## MFA をグローバルに無効化する場合

```sql
UPDATE mfa_settings SET platform_required = FALSE, tenant_required = FALSE WHERE id = 1;
```
