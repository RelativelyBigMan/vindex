"""Routes: the HTML pages (core/views.py) and the JSON API (the contract in core/api.py)."""
from django.urls import path

from . import api, views

escrow_patterns = [
    path("leases/", views.dashboard, name="dashboard"),
    path("leases/new/", views.lease_create, name="lease_create"),
    path("leases/<uuid:lease_id>/", views.lease_detail, name="lease_detail"),
    path("verify/<uuid:lease_id>/", views.verify, name="verify"),
    path("arbiter/", views.arbiter, name="arbiter"),
    path("demo/", views.demo, name="demo"),
    path("demo/stage/", views.stage, name="stage"),
]

legal_patterns = [
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
]

api_patterns = [
    path("api/me/", api.me),
    path("api/demo/personas/", api.demo_personas),
    path("api/demo/persona/", api.demo_persona),
    path("api/demo/fund/", api.demo_fund),
    path("api/demo/run-bot/", api.demo_run_bot),
    path("api/demo/stage/", api.demo_stage),
    path("api/demo/stage/act/", api.demo_stage_act),
    path("api/wallet/challenge/", api.wallet_challenge),
    path("api/wallet/link/", api.wallet_link),
    path("api/wallet/unlink/", api.wallet_unlink),
    path("api/credentials/<str:address>/", api.credentials),
    path("api/leases/", api.leases),
    path("api/leases/<uuid:lease_id>/", api.lease_detail),
    path("api/leases/<uuid:lease_id>/public/", api.lease_public),
    path("api/leases/<uuid:lease_id>/settlement/", api.settlement_raw),
    path("api/leases/<uuid:lease_id>/escrow-tx/", api.escrow_tx),
    path("api/leases/<uuid:lease_id>/escrow/", api.escrow),
    path("api/leases/<uuid:lease_id>/release-tx/", api.release_tx),
    path("api/leases/<uuid:lease_id>/release/", api.release),
    path("api/leases/<uuid:lease_id>/proposals/", api.proposals),
    path("api/leases/<uuid:lease_id>/evidence/", api.evidence),
    path("api/evidence/<int:evidence_id>/file/", api.evidence_file),
    path("api/evidence/<int:evidence_id>/anchor-tx/", api.evidence_anchor_tx),
    path("api/evidence/<int:evidence_id>/anchor/", api.evidence_anchor),
    path("api/proposals/<int:proposal_id>/sign/", api.proposal_sign),
]
