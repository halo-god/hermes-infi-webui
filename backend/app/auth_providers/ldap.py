"""LDAP / Active Directory provider (ldap3).

Supports two bind modes:
  - direct-bind: compute user DN from a template, bind directly
  - search-bind (recommended): bind as a service account, search for the user
    DN, then bind as the user to verify the password.

The connection_factory is injectable so tests can drive an in-memory MOCK_SYNC
directory without a real server.
"""
from __future__ import annotations

from typing import Callable

import ldap3
from ldap3 import BASE, SUBTREE, Connection, Server
from ldap3.utils.conv import escape_filter_chars

from app.auth_providers.base import IdentityInfo, ProviderError

ConnectionFactory = Callable[[dict, str, str], Connection]

_CONNECT_TIMEOUT = 8


def _make_server(config: dict) -> Server:
    return Server(
        config.get("host", "localhost"),
        port=int(config.get("port", 389)),
        use_ssl=bool(config.get("use_ssl", False)),
        get_info=ldap3.NONE,
        connect_timeout=_CONNECT_TIMEOUT,
    )


def _real_connection(config: dict, user_dn: str, password: str) -> Connection:
    return Connection(
        _make_server(config),
        user=user_dn,
        password=password,
        receive_timeout=_CONNECT_TIMEOUT,
    )


def _val(entry, attr: str) -> str | None:
    try:
        if entry is not None and attr in entry:
            v = entry[attr].value
            return str(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None
    return None


class LDAPProvider:
    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._factory = connection_factory or _real_connection

    # ── public API ──────────────────────────────────────────────────────────

    def authenticate(self, config: dict, username: str, password: str) -> IdentityInfo:
        """Authenticate a user; chooses direct-bind or search-bind based on config."""
        if not password:
            raise ProviderError("密码不能为空")
        uname = escape_filter_chars(username)
        auth_mode = config.get("auth_mode", "direct")
        if auth_mode == "search" and config.get("bind_dn"):
            return self._search_bind_auth(config, uname, username, password)
        return self._direct_bind_auth(config, uname, password)

    def test_connection(self, config: dict) -> dict:
        """Test LDAP server connectivity + service-account bind.

        Returns {"ok": bool, "message": str}.
        """
        host = (config.get("host") or "").strip()
        if not host:
            return {"ok": False, "message": "未填写服务器地址"}

        bind_dn = (config.get("bind_dn") or "").strip()
        bind_pw = config.get("bind_password") or ""

        try:
            server = _make_server(config)
            if bind_dn:
                conn = Connection(server, user=bind_dn, password=bind_pw, receive_timeout=_CONNECT_TIMEOUT)
            else:
                conn = Connection(server, receive_timeout=_CONNECT_TIMEOUT)

            if not conn.bind():
                desc = (conn.result or {}).get("description", "")
                if bind_dn:
                    return {"ok": False, "message": f"服务账号绑定失败：{desc or '账号或密码错误'}"}
                return {"ok": False, "message": f"匿名绑定失败：{desc}"}

            try:
                conn.unbind()
            except Exception:  # noqa: BLE001
                pass

            port = config.get("port", 389)
            ssl_label = "LDAPS" if config.get("use_ssl") else "LDAP"
            msg = f"已成功连接到 {ssl_label}://{host}:{port}"
            if bind_dn:
                msg += f"，服务账号验证通过"
            return {"ok": True, "message": msg}

        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"连接失败：{exc}"}

    # ── private helpers ──────────────────────────────────────────────────────

    def _direct_bind_auth(self, config: dict, uname: str, password: str) -> IdentityInfo:
        template = config.get("user_dn_template")
        if not template:
            raise ProviderError("LDAP 未配置 user_dn_template（直连绑定模式）")
        # If username is an email, extract the uid part for DN lookup
        uid = uname.split("@")[0] if "@" in uname else uname
        user_dn = template.format(username=uid)

        attr_email = config.get("attr_email", "mail")
        attr_name = config.get("attr_name", "cn")
        attr_dept = config.get("attr_dept", "departmentNumber")

        conn = self._factory(config, user_dn, password)
        try:
            if not conn.bind():
                raise ProviderError("LDAP 认证失败：账号或密码错误")
            conn.search(
                user_dn, "(objectClass=*)", search_scope=BASE,
                attributes=[attr_email, attr_name, attr_dept],
            )
            entry = conn.entries[0] if conn.entries else None
            email = _val(entry, attr_email) or f"{uname}@{config.get('email_domain', 'ldap.local')}"
            name = _val(entry, attr_name) or uname
            dept = _val(entry, attr_dept)
        finally:
            try:
                conn.unbind()
            except Exception:  # noqa: BLE001
                pass

        return IdentityInfo(
            external_id=user_dn, email=email.lower(), name=name,
            department=dept, source="ldap",
        )

    def _search_bind_auth(self, config: dict, uname: str, raw_username: str, password: str) -> IdentityInfo:
        bind_dn = config.get("bind_dn", "")
        bind_pw = config.get("bind_password", "")
        base_dn = config.get("base_dn", "")
        search_tpl = config.get("search_filter", "(uid={username})")
        search_filter = search_tpl.format(username=uname)

        attr_email = config.get("attr_email", "mail")
        attr_name = config.get("attr_name", "cn")
        attr_dept = config.get("attr_dept", "departmentNumber")

        server = _make_server(config)

        # Step 1: Service account search
        svc_conn = Connection(server, user=bind_dn, password=bind_pw, receive_timeout=_CONNECT_TIMEOUT)
        try:
            if not svc_conn.bind():
                raise ProviderError("LDAP 服务账号绑定失败，请联系管理员")
            svc_conn.search(
                base_dn, search_filter, search_scope=SUBTREE,
                attributes=[attr_email, attr_name, attr_dept],
            )
            if not svc_conn.entries:
                raise ProviderError(f"未找到用户：{raw_username}")
            entry = svc_conn.entries[0]
            user_dn = entry.entry_dn
            email_val = _val(entry, attr_email)
            name_val = _val(entry, attr_name)
            dept_val = _val(entry, attr_dept)
        finally:
            try:
                svc_conn.unbind()
            except Exception:  # noqa: BLE001
                pass

        # Step 2: User bind to verify password
        user_conn = Connection(server, user=user_dn, password=password, receive_timeout=_CONNECT_TIMEOUT)
        try:
            if not user_conn.bind():
                raise ProviderError("密码验证失败")
        finally:
            try:
                user_conn.unbind()
            except Exception:  # noqa: BLE001
                pass

        email = email_val or f"{raw_username}@{config.get('email_domain', 'ldap.local')}"
        return IdentityInfo(
            external_id=user_dn, email=email.lower(),
            name=name_val or raw_username, department=dept_val, source="ldap",
        )
