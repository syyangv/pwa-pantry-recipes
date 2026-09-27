"""Pantry Recipes PWA — FastAPI backend package.

`app.main:create_app` is the application factory. `app.config.Settings` is the
fail-closed runtime configuration. `app.pwa_version` is vendored pwa-infra and
must stay byte-identical to the package (enforced by the drift gate).
"""
