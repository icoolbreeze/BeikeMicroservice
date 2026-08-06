"""Auth center package for the personal CRM connector.

Contains the ``crm-authd`` CLI plus a minimal FastAPI status surface. The
auth center is the only process allowed to invoke ``bootstrap()`` and
``refresh()`` against ``CredentialBootstrapProvider``; everything else in
the service goes through ``SessionProvider.authorized_fetch``.
"""
