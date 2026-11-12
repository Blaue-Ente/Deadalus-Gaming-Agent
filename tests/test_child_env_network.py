"""Playground child processes keep the machine's proxy and CA settings, never Daedalus's secrets.

Found live (2026-11-11): behind a proxy every `npm install` in a Playground project timed out because HTTPS_PROXY and
the CA settings were stripped from the child environment; Full Auto spent 47 minutes in one milestone's verify."""
from __future__ import annotations

from daedalus.playground_runtime import PlaygroundRuntime


def test_proxy_and_ca_settings_reach_npm_but_keys_do_not(monkeypatch):
    for k, v in {"HTTPS_PROXY": "http://proxy.local:8080", "no_proxy": "localhost", "NODE_EXTRA_CA_CERTS": "/ca.pem",
                 "npm_config_https_proxy": "http://proxy.local:8080", "OPENROUTER_API_KEY": "sk-secret",
                 "DAEDALUS_TOKEN": "owner-secret", "ANTHROPIC_API_KEY": "sk-ant-secret"}.items():
        monkeypatch.setenv(k, v)
    env = PlaygroundRuntime.child_env(0)
    assert env["HTTPS_PROXY"] == "http://proxy.local:8080" and env["no_proxy"] == "localhost"
    assert env["NODE_EXTRA_CA_CERTS"] == "/ca.pem" and env["npm_config_https_proxy"] == "http://proxy.local:8080"
    assert not {"OPENROUTER_API_KEY", "DAEDALUS_TOKEN", "ANTHROPIC_API_KEY"} & set(env)
