"""Domain exceptions for service layer.

Services raise these instead of HTTPException to maintain
clean separation between business logic and HTTP protocol.
Routes translate these to appropriate HTTP responses.
"""


class AuthError(Exception):
    """Base authentication error."""

    def __init__(self, message: str = "认证失败"):
        self.message = message
        super().__init__(message)


class InvalidCredentialsError(AuthError):
    """Invalid username or password."""

    def __init__(self):
        super().__init__("账号或密码错误")


class InvalidTokenError(AuthError):
    """Invalid or malformed token."""

    def __init__(self, message: str = "令牌无效或已过期"):
        super().__init__(message)


class TokenExpiredError(AuthError):
    """Token has expired."""

    def __init__(self, message: str = "令牌已过期"):
        super().__init__(message)


class TokenRevokedError(AuthError):
    """Token has been revoked (logout/password change)."""

    def __init__(self, message: str = "令牌已失效"):
        super().__init__(message)


class AccountDisabledError(AuthError):
    """User account is disabled."""

    def __init__(self):
        super().__init__("账号已停用")


class UserNotFoundError(AuthError):
    """User not found."""

    def __init__(self):
        super().__init__("用户不存在")


class ValidationError(Exception):
    """Input validation error."""

    def __init__(self, message: str = "输入验证失败"):
        self.message = message
        super().__init__(message)
