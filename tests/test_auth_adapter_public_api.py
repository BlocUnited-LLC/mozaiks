"""Public, independently configured JWT validators; no HTTP or real credentials."""

from __future__ import annotations

import asyncio
import json
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import mozaiksai.core.auth as auth
import mozaiksai.core.auth.adapters as adapters
import mozaiksai.core.auth.adapters.jwt_adapter as implementation
from mozaiksai.core.auth import AuthError, GenericJWTAdapter, JWTAdapterConfig


def _settings(name: str) -> dict[str, str]:
    return {
        "AUTH_PROVIDER": "jwt",
        "AUTH_ENABLED": "true",
        "AUTH_JWKS_URL": f"https://{name}.example.test/jwks",
        "AUTH_ISSUER": f"https://{name}.example.test",
        "AUTH_AUDIENCE": f"{name}-api",
        "AUTH_SCOPES_CLAIM": "scope",
        "AUTH_SCOPES_FORMAT": "space",
        "AUTH_REQUIRED_SCOPE": f"{name}.read",
        "AUTH_CLOCK_SKEW": "0",
        "AUTH_ALGORITHMS": "RS256",
        "AUTH_ACCESS_TOKEN_TYPE_CLAIM": "",
        "AUTH_ACCESS_TOKEN_TYPE_VALUE": "",
    }


@pytest.fixture(scope="module")
def signing_keys():
    return {name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
            for name in ("left", "right", "global")}


@pytest.fixture
def offline_keys(monkeypatch, signing_keys):
    clients = []

    class LocalJWKSClient:
        def __init__(self, *, jwks_url, cache_ttl, use_discovery, consult_environment, discovery_client):
            assert not use_discovery
            assert not consult_environment
            self.url = jwks_url
            self.cache_ttl = cache_ttl
            clients.append(self)

        async def get_signing_key(self, kid):
            await asyncio.sleep(0)  # Interleave concurrent validations.
            assert kid == "local-key"
            name = next(name for name in signing_keys if self.url == _settings(name)["AUTH_JWKS_URL"])
            key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_keys[name].public_key()))
            return {**key, "kid": kid, "alg": "RS256", "use": "sig"}

    monkeypatch.setattr(implementation, "JWKSClient", LocalJWKSClient)
    return clients


def _token(signing_keys, name="left", *, typ="at+jwt", signer=None, **overrides):
    now = int(time.time())
    claims = {
        "sub": f"{name}-user",
        "iss": _settings(name)["AUTH_ISSUER"],
        "aud": _settings(name)["AUTH_AUDIENCE"],
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 300,
        "scope": f"{name}.read",
        **overrides,
    }
    return jwt.encode(
        {key: value for key, value in claims.items() if value is not None},
        signing_keys[signer or name], algorithm="RS256", headers={"kid": "local-key", "typ": typ},
    )


def test_public_exports_are_the_existing_implementation():
    for name in ("GenericJWTAdapter", "JWTAdapterConfig"):
        assert name in auth.__all__
        assert name in adapters.__all__
        assert getattr(auth, name) is getattr(adapters, name) is getattr(implementation, name)


@pytest.mark.parametrize("options,mode", [({}, "contains"), ({"audience_mode": "single"}, "single")])
def test_audience_policy_preserves_existing_positional_configuration(options, mode):
    config = JWTAdapterConfig("https://issuer.test/jwks", "https://issuer.test", "api", "token_use", "access", **options)
    assert config.jwks_url == "https://issuer.test/jwks"
    assert config.issuer == "https://issuer.test"
    assert config.audience == "api"
    assert config.access_token_type_claim == "token_use"
    assert config.access_token_type_value == "access"
    assert config.audience_mode == mode


@pytest.mark.asyncio
async def test_simultaneous_verifiers_keep_settings_and_global_factory(monkeypatch, offline_keys, signing_keys):
    global_settings = _settings("global")
    for name, value in global_settings.items():
        monkeypatch.setenv(name, value)
    auth.reset_auth_adapter()
    try:
        global_adapter = auth.get_auth_adapter()
        left_settings = _settings("left")
        left_config = JWTAdapterConfig.from_env(left_settings)
        left_config.jwks_cache_ttl_seconds = 31
        right_config = JWTAdapterConfig.from_env(_settings("right"))
        right_config.jwks_cache_ttl_seconds = 47
        right_config.user_id_claim = "subject"
        right_config.scopes_claim = "grants"
        right_config.scopes_format = "array"
        right_config.access_token_type_claim = "token_use"
        right_config.access_token_type_value = "access"
        left = GenericJWTAdapter(config=left_config)
        right = GenericJWTAdapter(config=right_config)
        left_settings["AUTH_AUDIENCE"] = "changed-after-construction"

        tokens = [
            _token(signing_keys),
            _token(signing_keys, "right", typ="JWT", subject="custom-right", grants=["right.read"],
                   scope=None, token_use="access"),
            _token(signing_keys, "global"),
        ]
        validators = (left, right, global_adapter)
        for _ in range(2):
            results = await asyncio.gather(*(adapter.validate_token(token)
                                             for adapter, token in zip(validators, tokens, strict=True)))
            assert [result.user_id for result in results] == ["left-user", "custom-right", "global-user"]
            assert [result.scopes for result in results] == [["left.read"], ["right.read"], ["global.read"]]
            assert all(not result.roles for result in results)
        assert len(offline_keys) == 3  # Each verifier retains its own lazy client.
        assert {client.url: client.cache_ttl for client in offline_keys if client.cache_ttl in (31, 47)} == {
            left_config.jwks_url: 31, right_config.jwks_url: 47,
        }
        assert auth.get_auth_adapter() is global_adapter
        assert {name: os.environ[name] for name in global_settings} == global_settings
    finally:
        auth.reset_auth_adapter()


@pytest.mark.asyncio
@pytest.mark.parametrize("audience_mode", ["contains", "single"])
@pytest.mark.parametrize(
    "overrides,typ,signer,reason",
    [
        ({"aud": "right-api"}, "at+jwt", None, "Invalid token audience"),
        ({"iss": "https://right.example.test"}, "at+jwt", None, "Invalid token issuer"),
        ({}, "JWT", None, "not an access token"),
        ({}, "at+jwt", "right", "Invalid token signature"),
        ({"aud": None}, "at+jwt", None, "missing required claim: aud"),
        ({"scope": "right.read"}, "at+jwt", None, "Missing required scope"),
    ],
)
async def test_public_verifier_rejects_invalid_signed_tokens(offline_keys, signing_keys, overrides, typ, signer, reason, audience_mode):
    adapter = GenericJWTAdapter(config=JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": audience_mode}))
    with pytest.raises(AuthError, match=reason):
        await adapter.validate_token(_token(signing_keys, typ=typ, signer=signer, **overrides))


@pytest.mark.asyncio
async def test_zero_clock_skew_rejects_recent_expiry(offline_keys, signing_keys):
    strict_config = JWTAdapterConfig.from_env(_settings("left"))
    tolerant_config = JWTAdapterConfig.from_env(_settings("left"))
    tolerant_config.clock_skew_seconds = 120
    token = _token(signing_keys, exp=int(time.time()) - 10, iat=int(time.time()) - 30)
    assert strict_config.clock_skew_seconds == 0
    assert (await GenericJWTAdapter(config=tolerant_config).validate_token(token)).user_id == "left-user"
    with pytest.raises(AuthError, match="Token has expired"):
        await GenericJWTAdapter(config=strict_config).validate_token(token)


@pytest.mark.asyncio
async def test_missing_configured_audience_fails_closed(offline_keys, signing_keys):
    config = JWTAdapterConfig.from_env(_settings("left"))
    config.audience = ""
    with pytest.raises(AuthError, match="without audience verification"):
        await GenericJWTAdapter(config=config).validate_token(_token(signing_keys))
    assert not offline_keys


@pytest.mark.asyncio
async def test_default_audience_check_remains_nonexclusive(offline_keys, signing_keys):
    """The default accepts the configured resource among several audiences."""
    first = JWTAdapterConfig.from_env(_settings("left"))
    second = JWTAdapterConfig.from_env(_settings("left"))
    second.audience = "second-api"
    token = _token(signing_keys, aud=[first.audience, second.audience])
    results = await asyncio.gather(
        GenericJWTAdapter(config=first).validate_token(token),
        GenericJWTAdapter(config=second).validate_token(token),
    )
    assert [result.user_id for result in results] == ["left-user", "left-user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["left-api", ["left-api"]])
async def test_single_audience_mode_accepts_one_matching_resource(offline_keys, signing_keys, audience):
    config = JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": "single"})
    assert config.audience_mode == "single"
    assert (await GenericJWTAdapter(config=config).validate_token(_token(signing_keys, aud=audience))).user_id == "left-user"


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", [["left-api", "right-api"], ["right-api", "left-api"], ["left-api", "left-api"]])
async def test_single_audience_mode_rejects_every_multi_entry_claim(offline_keys, signing_keys, audience):
    config = JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": "single"})
    with pytest.raises(AuthError, match="Invalid token audience"):
        await GenericJWTAdapter(config=config).validate_token(_token(signing_keys, aud=audience))


@pytest.mark.asyncio
async def test_two_single_audience_resources_reject_shared_token_in_both_directions(offline_keys, signing_keys):
    first = JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": "single"})
    second = JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": "single", "AUTH_AUDIENCE": "second-api"})
    left, right = GenericJWTAdapter(config=first), GenericJWTAdapter(config=second)
    shared = _token(signing_keys, aud=["left-api", "second-api"])
    for adapter, matching, wrong in ((left, "left-api", "second-api"), (right, "second-api", "left-api")):
        assert (await adapter.validate_token(_token(signing_keys, aud=matching))).user_id == "left-user"
        for token in (shared, _token(signing_keys, aud=wrong)):
            with pytest.raises(AuthError, match="Invalid token audience"):
                await adapter.validate_token(token)


@pytest.mark.parametrize("mode", ["unknown", "", None, True, 1, ["single"]])
def test_invalid_explicit_audience_mode_fails_configuration(mode):
    with pytest.raises(ValueError, match="audience mode"):
        JWTAdapterConfig(audience_mode=mode)


@pytest.mark.parametrize("value,expected", [("", "contains"), ("  ", "contains"), ("contains", "contains"), (" single ", "single")])
def test_audience_mode_reads_only_its_configuration_snapshot(monkeypatch, value, expected):
    monkeypatch.setenv("AUTH_AUDIENCE_MODE", "unknown")
    config = JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": value})
    assert config.audience_mode == expected
    assert JWTAdapterConfig.from_env(_settings("left")).audience_mode == "contains"


@pytest.mark.asyncio
async def test_factory_cache_tracks_audience_mode_and_does_not_mutate_existing_instances(monkeypatch, offline_keys, signing_keys):
    for name, value in _settings("left").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AUTH_AUDIENCE_MODE", "contains")
    auth.reset_auth_adapter()
    try:
        containing = auth.get_auth_adapter()
        token = _token(signing_keys, aud=["left-api", "second-api"])
        assert (await containing.validate_token(token)).user_id == "left-user"
        monkeypatch.setenv("AUTH_AUDIENCE_MODE", "single")
        single = auth.get_auth_adapter()
        assert single is not containing and auth.get_auth_adapter() is single
        with pytest.raises(AuthError, match="Invalid token audience"):
            await single.validate_token(token)
        assert (await containing.validate_token(token)).user_id == "left-user"
    finally:
        auth.reset_auth_adapter()


@pytest.mark.asyncio
async def test_invalid_mutated_audience_policy_cannot_silently_disable_the_constraint(offline_keys, signing_keys):
    config = JWTAdapterConfig.from_env({**_settings("left"), "AUTH_AUDIENCE_MODE": "single"})
    config.audience_mode = "unknown"
    with pytest.raises(AuthError, match="audience policy"):
        await GenericJWTAdapter(config=config).validate_token(_token(signing_keys))
    assert not offline_keys
