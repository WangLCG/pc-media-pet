from app import webrtc


def test_ipv4_only_ice_configuration_disables_ipv6_candidates(monkeypatch):
    calls = []

    def get_addresses(*, use_ipv4, use_ipv6):
        calls.append((use_ipv4, use_ipv6))
        return ["192.168.0.87"]

    monkeypatch.setattr(webrtc, "_original_get_host_addresses", get_addresses)
    monkeypatch.setattr(webrtc.ice, "get_host_addresses", webrtc.ice.get_host_addresses)
    webrtc.configure_ice_candidates(ipv6_enabled=False)

    assert webrtc.ice.get_host_addresses(use_ipv4=True, use_ipv6=True) == ["192.168.0.87"]
    assert calls == [(True, False)]


def test_ipv6_enabled_restores_default_ice_candidate_provider(monkeypatch):
    def get_addresses(*, use_ipv4, use_ipv6):
        return ["2001:db8::1"] if use_ipv6 else []

    monkeypatch.setattr(webrtc, "_original_get_host_addresses", get_addresses)
    monkeypatch.setattr(webrtc.ice, "get_host_addresses", webrtc.ice.get_host_addresses)
    webrtc.configure_ice_candidates(ipv6_enabled=True)

    assert webrtc.ice.get_host_addresses(use_ipv4=False, use_ipv6=True) == ["2001:db8::1"]


def test_local_ice_configuration_has_no_stun_or_turn_servers():
    assert webrtc.local_ice_configuration().iceServers == []
