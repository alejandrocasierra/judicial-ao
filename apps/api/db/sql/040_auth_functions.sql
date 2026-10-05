-- Funciones SECURITY DEFINER mínimas para login: el rol de la app nunca
-- puede listar usuarios de otras organizaciones; sólo resolver 1 email.
CREATE FUNCTION auth_find_user(p_email text)
RETURNS TABLE (id uuid, organization_id uuid, password_hash text, org_role text, locale text,
               is_active boolean, failed_login_attempts int, locked_until timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT u.id, u.organization_id, u.password_hash, u.org_role, u.locale, u.is_active, u.failed_login_attempts, u.locked_until
  FROM users u WHERE u.email = lower(p_email) LIMIT 1
$$;

CREATE FUNCTION auth_register_failure(p_user_id uuid, p_max int, p_lock_seconds int)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  UPDATE users SET failed_login_attempts = failed_login_attempts + 1,
         locked_until = CASE WHEN failed_login_attempts + 1 >= p_max
                             THEN now() + make_interval(secs => p_lock_seconds) ELSE locked_until END
  WHERE id = p_user_id
$$;

CREATE FUNCTION auth_register_success(p_user_id uuid)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  UPDATE users SET failed_login_attempts = 0, locked_until = NULL, last_login_at = now() WHERE id = p_user_id
$$;
