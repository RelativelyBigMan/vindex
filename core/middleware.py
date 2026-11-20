"""Security headers. The frontend has no inline scripts or styles, so the policy can be strict."""
from ledger.client import NETWORKS, network_key


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        ws = NETWORKS.get(network_key(), NETWORKS["testnet"])["ws"]
        self.policy = "; ".join([
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data: blob:",
            "font-src 'self'",
            # The verify page reads the ledger directly from the browser; nothing else is contacted.
            f"connect-src 'self' {ws}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ])

    def __call__(self, request):
        response = self.get_response(request)
        # The Django admin ships its own inline bits; it keeps Django's defaults.
        if not request.path.startswith("/admin/") and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.policy
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response
