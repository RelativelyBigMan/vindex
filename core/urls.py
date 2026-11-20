"""API routes (the contract in core/api.py). HTML pages are added in Patch 4."""
from django.urls import path

from . import api

urlpatterns = [
    path("api/me/", api.me),
    path("api/demo/personas/", api.demo_personas),
    path("api/demo/persona/", api.demo_persona),
    path("api/demo/fund/", api.demo_fund),
    path("api/demo/run-bot/", api.demo_run_bot),
    path("api/wallet/link/", api.wallet_link),
    path("api/leases/", api.leases),
    path("api/leases/<uuid:lease_id>/", api.lease_detail),
    path("api/leases/<uuid:lease_id>/public/", api.lease_public),
    path("api/leases/<uuid:lease_id>/escrow-tx/", api.escrow_tx),
    path("api/leases/<uuid:lease_id>/escrow/", api.escrow),
    path("api/leases/<uuid:lease_id>/release-tx/", api.release_tx),
    path("api/leases/<uuid:lease_id>/release/", api.release),
    path("api/leases/<uuid:lease_id>/proposals/", api.proposals),
    path("api/leases/<uuid:lease_id>/evidence/", api.evidence),
    path("api/proposals/<int:proposal_id>/sign/", api.proposal_sign),
]
