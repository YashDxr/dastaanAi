from daastaan_api.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_round_trip(self):
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed)
        assert not verify_password("wrong password", hashed)

    def test_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_long_passwords_are_distinguished(self):
        """bcrypt ignores everything past 72 bytes. Without the SHA-256 pre-hash
        these two passwords would share a hash and either would unlock the
        account."""
        base = "a" * 80
        hashed = hash_password(base + "ending-one")
        assert not verify_password(base + "ending-two", hashed)
        assert verify_password(base + "ending-one", hashed)

    def test_malformed_hash_is_rejected_not_raised(self):
        assert not verify_password("anything", "not-a-bcrypt-hash")


class TestTokens:
    def test_round_trip_preserves_identity_and_role(self):
        payload = decode_access_token(create_access_token("user-1", "admin"))
        assert payload["sub"] == "user-1"
        assert payload["role"] == "admin"

    def test_tampered_token_rejected(self):
        import pytest
        from jwt import InvalidTokenError

        token = create_access_token("user-1", "user")
        # Flip the role claim by swapping in a token signed with another secret.
        forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)
