"""Routes: the HTML pages (core/views.py) and the JSON API (the contract in core/api.py)."""
from django.urls import path

from . import api, views

escrow_patterns = [
    path("leases/", views.dashboard, name="dashboard"),
    path("leases/new/", views.lease_create, name="lease_create"),
    path("leases/<uuid:lease_id>/", views.lease_detail, name="lease_detail"),
    path("verify/<uuid:lease_id>/", views.verify, name="verify"),
    path("invite/<str:token>/", views.invite, name="invite"),
]

legal_patterns = [
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
]

api_patterns = [
    path("api/me/", api.me),
    path("api/auth/signin/", api.signin),
    path("api/auth/signout/", api.signout),
    path("api/xaman/requests/<uuid:request_id>/", api.xaman_request),
    path("api/xaman/requests/<uuid:request_id>/cancel/", api.xaman_cancel),
    path("api/faucet/", api.faucet_status),
    path("api/faucet/activate/", api.faucet_activate),
    path("api/faucet/trustline/", api.faucet_trustline),
    path("api/faucet/tokens/", api.faucet_tokens),
    path("api/invites/<str:token>/", api.invite),
    path("api/invites/<str:token>/accept/", api.invite_accept),
    path("api/invites/<str:token>/decline/", api.invite_decline),
    path("api/leases/", api.leases),
    path("api/leases/<uuid:lease_id>/", api.lease_detail),
    path("api/leases/<uuid:lease_id>/public/", api.lease_public),
    path("api/leases/<uuid:lease_id>/settlement/", api.settlement_raw),
    path("api/leases/<uuid:lease_id>/cancel/", api.lease_cancel),
    path("api/leases/<uuid:lease_id>/arbiter/", api.lease_arbiter),
    path("api/leases/<uuid:lease_id>/approve-arbiter/", api.lease_approve_arbiter),
    path("api/leases/<uuid:lease_id>/escrow/", api.escrow),
    path("api/leases/<uuid:lease_id>/release/", api.release),
    path("api/leases/<uuid:lease_id>/proposals/", api.proposals),
    path("api/leases/<uuid:lease_id>/evidence/", api.evidence),
    path("api/evidence/<int:evidence_id>/file/", api.evidence_file),
    path("api/evidence/<int:evidence_id>/anchor/", api.evidence_anchor),
    path("api/proposals/<int:proposal_id>/sign/", api.proposal_sign),
    path("api/proposals/<int:proposal_id>/retry/", api.proposal_retry),
]
