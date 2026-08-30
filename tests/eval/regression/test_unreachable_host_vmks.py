"""list_host_vmks against hosts vCenter has lost contact with.

Found on a real VCF 9.1 estate (2026-08-30) where 4 of 8 ESXi hosts sat in
``notResponding``. vCenter keeps answering property reads for such a host out
of its own cache — no fault, no marker — so the read "succeeds" and looks
authoritative. Two distinct failures came out of that:

1. Asked for one unreachable host by name, ``host.config`` is ``None`` and the
   bare ``AttributeError: 'NoneType' object has no attribute 'network'``
   reached the user.

2. Enumerating every host, an unreachable one contributed no rows and was
   dropped — while the envelope still said ``truncated: false``, positively
   certifying a list that four hosts were missing from. That is 形态 #1, with
   the envelope signing it.

The controls below matter as much as the failures: every one of these tests
passes trivially if the tool starts reporting everything as unknown. A
connected host must still get an ordinary, complete answer, and a host that
genuinely has no VMkernel adapters must stay distinguishable from one nobody
asked.
"""

import types

import pytest

from vmware_aiops.ops import host_network_mgmt as hnm


def _vnic(device, ip="192.0.2.11", mtu=1500):
    spec = types.SimpleNamespace(
        ip=types.SimpleNamespace(ipAddress=ip, subnetMask="255.255.255.0", dhcp=False),
        mtu=mtu,
        mac="00:50:56:01:02:03",
        distributedVirtualPort=None,
        netStackInstanceKey="defaultTcpipStack",
    )
    return types.SimpleNamespace(device=device, spec=spec, portgroup="Management Network")


def _nic_mgr(device="vmk0", nic_types=("management",)):
    cand = types.SimpleNamespace(key=f"k-{device}", device=device)
    return types.SimpleNamespace(
        info=types.SimpleNamespace(
            netConfig=[
                types.SimpleNamespace(
                    nicType=t, selectedVnic=[f"k-{device}"], candidateVnic=[cand]
                )
                for t in nic_types
            ]
        )
    )


def _host(name, state="connected", vnics=(), config_present=True, nic_mgr=None):
    """A HostSystem stand-in.

    ``config_present=False`` is what vCenter actually hands back for a host it
    has lost contact with: the managed object is still there, ``config`` is
    ``None``.
    """
    return types.SimpleNamespace(
        name=name,
        _moId=f"host-{name}",
        runtime=types.SimpleNamespace(connectionState=state),
        config=(
            types.SimpleNamespace(network=types.SimpleNamespace(vnic=list(vnics)))
            if config_present
            else None
        ),
        configManager=types.SimpleNamespace(
            networkSystem=None,
            virtualNicManager=nic_mgr if nic_mgr is not None else _nic_mgr(),
        ),
    )


def _wire(monkeypatch, hosts):
    """Stand in for inventory._collect over [HostSystem].

    A host with no ``config`` contributes no ``config.network.vnic`` key at
    all, which is the real shape: PropertyCollector omits a property it has no
    value for, so the key is *absent* rather than empty — the whole reason
    ``[]`` was the wrong default.
    """

    def fake_collect(si, obj_type, paths):
        rows = []
        for h in hosts:
            props = {
                "name": h.name,
                "runtime.connectionState": h.runtime.connectionState,
            }
            if h.config is not None:
                props["config.network.vnic"] = h.config.network.vnic
            rows.append((h, props))
        return rows

    monkeypatch.setattr(hnm, "_collect", fake_collect)
    by_name = {h.name: h for h in hosts}
    monkeypatch.setattr(hnm, "find_host_by_name", lambda si, n: by_name.get(n))


# --- failure 1: the named unreachable host --------------------------------------


def test_named_unreachable_host_answers_instead_of_raising(monkeypatch):
    """No bare AttributeError, and the answer says the host went unread."""
    dead = _host("esxi04.lab", state="notResponding", config_present=False)
    _wire(monkeypatch, [dead])

    out = hnm.list_host_vmks(object(), host_name="esxi04.lab")

    assert out["total"] == 1, "the host must appear, not vanish"
    (row,) = out["items"]
    assert row["host"] == "esxi04.lab"
    assert row["reachable"] is False
    assert "notResponding" in row["note"]
    assert out["hosts_unreachable"] == 1


def test_unread_row_states_no_facts_it_did_not_read(monkeypatch):
    """None, never [] or False — those are claims about the host."""
    dead = _host("esxi04.lab", state="notResponding", config_present=False)
    _wire(monkeypatch, [dead])

    (row,) = hnm.list_host_vmks(object(), host_name="esxi04.lab")["items"]

    for field in ("device", "ip", "netmask", "dhcp", "mtu", "mac",
                  "portgroup", "dvs_port", "netstack", "services"):
        assert row[field] is None, f"{field} must be unknown, not a measurement"


def test_named_reachable_host_still_gets_an_ordinary_answer(monkeypatch):
    """Control: the fix must not turn every named lookup into a shrug."""
    live = _host("esxi01.lab", vnics=[_vnic("vmk0")])
    _wire(monkeypatch, [live])

    out = hnm.list_host_vmks(object(), host_name="esxi01.lab")

    (row,) = out["items"]
    assert row["reachable"] is True
    assert row["device"] == "vmk0"
    assert row["ip"] == "192.0.2.11"
    assert row["mtu"] == 1500
    assert row["services"] == ["management"]
    assert row["note"] is None
    assert out["hosts_unreachable"] == 0
    assert "unreachable_note" not in out


# --- failure 2: the silent disappearance ----------------------------------------


def test_enumeration_never_drops_an_unreachable_host(monkeypatch):
    """The estate that produced this: half the hosts unreachable, none said so."""
    hosts = [
        _host("esxi01.lab", vnics=[_vnic("vmk0"), _vnic("vmk1", ip="192.0.2.12")]),
        _host("esxi04.lab", state="notResponding", config_present=False),
    ]
    _wire(monkeypatch, hosts)

    out = hnm.list_host_vmks(object())

    hosts_seen = {r["host"] for r in out["items"]}
    assert "esxi04.lab" in hosts_seen, "an unasked host must not vanish from the list"
    assert out["hosts_unreachable"] == 1
    assert "1 host(s) could not be read" in out["unreachable_note"]


def test_envelope_does_not_certify_a_list_it_could_not_complete(monkeypatch):
    """`truncated` stays a paging fact; the incompleteness gets its own key.

    Overriding `hint` would be wrong (vmware_policy.paginated refuses it, and
    the family meaning is fixed: this page was truncated, here is the rest).
    """
    hosts = [
        _host("esxi01.lab", vnics=[_vnic("vmk0")]),
        _host("esxi04.lab", state="notResponding", config_present=False),
    ]
    _wire(monkeypatch, hosts)

    out = hnm.list_host_vmks(object())

    assert out["truncated"] is False and out["hint"] is None
    assert out["unreachable_note"], "incompleteness must be stated somewhere"


def test_all_connected_estate_carries_no_caveat(monkeypatch):
    """Control: a banner on every clean run is a banner nobody reads."""
    hosts = [
        _host("esxi01.lab", vnics=[_vnic("vmk0")]),
        _host("esxi02.lab", vnics=[_vnic("vmk0")]),
    ]
    _wire(monkeypatch, hosts)

    out = hnm.list_host_vmks(object())

    assert out["total"] == 2
    assert out["hosts_unreachable"] == 0
    assert "unreachable_note" not in out
    assert all(r["reachable"] is True and r["device"] == "vmk0" for r in out["items"])


def test_reachable_host_with_no_vmks_is_not_an_unread_host(monkeypatch):
    """Control: measured-empty and unmeasured must not collapse into one shape.

    A connected host that answers with an empty vnic list contributes no rows —
    correctly, there is nothing to list — and is NOT counted as unreachable.
    """
    hosts = [_host("esxi01.lab", vnics=[])]
    _wire(monkeypatch, hosts)

    out = hnm.list_host_vmks(object())

    assert out["items"] == []
    assert out["total"] == 0
    assert out["hosts_unreachable"] == 0
    assert "unreachable_note" not in out


def test_connected_host_that_returned_no_config_is_still_unread(monkeypatch):
    """The second way a host goes unmeasured: state says connected, config is absent.

    `connectionState` alone is not the test — the property that carries the
    answer has to actually be there. Trusting the state would let this host
    vanish exactly like a notResponding one.
    """
    hosts = [_host("esxi01.lab", state="connected", config_present=False)]
    _wire(monkeypatch, hosts)

    out = hnm.list_host_vmks(object())

    (row,) = out["items"]
    assert row["reachable"] is False
    assert "connected but returned no network configuration" in row["note"]
    assert out["hosts_unreachable"] == 1


def test_cached_vnics_are_reported_but_flagged_not_dropped(monkeypatch):
    """vCenter answers a notResponding host out of cache — say so, keep the rows.

    Dropping them loses real (if stale) information; presenting them unmarked
    is the lie the tester caught elsewhere.
    """
    hosts = [_host("esxi04.lab", state="notResponding", vnics=[_vnic("vmk0")])]
    _wire(monkeypatch, hosts)

    out = hnm.list_host_vmks(object())

    (row,) = out["items"]
    assert row["device"] == "vmk0", "cached data is kept"
    assert row["reachable"] is False
    assert "notResponding" in row["note"]
    assert out["hosts_unreachable"] == 1


def test_unread_rows_are_paged_honestly(monkeypatch):
    """Control: the new row is a real row — it counts, and paging still works."""
    hosts = [
        _host("esxi01.lab", vnics=[_vnic("vmk0")]),
        _host("esxi04.lab", state="notResponding", config_present=False),
    ]
    _wire(monkeypatch, hosts)

    first = hnm.list_host_vmks(object(), limit=1)
    assert first["total"] == 2
    assert first["returned"] == 1
    assert first["truncated"] is True and first["hint"]

    second = hnm.list_host_vmks(object(), limit=1, offset=1)
    assert second["truncated"] is False
    assert second["items"][0] != first["items"][0]


# --- the service map is read off the same unreachable host ----------------------


def test_service_map_read_survives_a_host_with_no_config_manager(monkeypatch):
    """``host.configManager`` is None on a lost host too — unknown, not a crash.

    None here means "unverifiable", which remove_host_vmk and set_vmk_service
    already treat as refuse-to-act.
    """
    lost = types.SimpleNamespace(name="esxi04.lab", configManager=None)
    assert hnm._vmk_services(lost) is None


def test_unreachable_host_is_not_asked_for_its_service_map(monkeypatch):
    """No round trip to a host nobody can reach; the answer is unknown either way."""
    hosts = [_host("esxi04.lab", state="notResponding", vnics=[_vnic("vmk0")])]
    _wire(monkeypatch, hosts)
    asked = []
    monkeypatch.setattr(
        hnm, "_vmk_services", lambda h: asked.append(h.name) or {"vmk0": ["management"]}
    )

    out = hnm.list_host_vmks(object())

    assert asked == [], "unreachable host was queried anyway"
    assert out["items"][0]["services"] is None


# --- the write paths read the same adapter list, and answer differently --------


def _call(name, si, host_name):
    return {
        "add": lambda: hnm.add_host_vmk(
            si, host_name, "pg-test", "198.51.100.1", "255.255.255.0"
        ),
        "remove": lambda: hnm.remove_host_vmk(si, host_name, "vmk1"),
        "set": lambda: hnm.set_vmk_service(si, host_name, "vmk1", "vmotion", True),
        "ping": lambda: hnm.vmk_ping(si, host_name, "vmk1", "198.51.100.2"),
    }[name]()


@pytest.mark.parametrize("op", ["add", "remove", "set", "ping"])
def test_write_paths_refuse_a_host_vcenter_cannot_reach(monkeypatch, op):
    """Same `config is None`, deliberately the opposite answer to the list tool.

    These read the adapter list to decide whether to change it, and their empty
    case already means "no such vmk". A row would hand a reconfiguration
    request a confident false statement about a machine nobody is talking to.
    """
    dead = _host("esxi04.lab", state="notResponding", config_present=False)
    _wire(monkeypatch, [dead])
    # A real portgroup, so add_host_vmk gets past its own lookup and reaches the
    # host read this test is about.
    pg = types.SimpleNamespace(
        name="pg-test",
        key="dvportgroup-1",
        config=types.SimpleNamespace(
            distributedVirtualSwitch=types.SimpleNamespace(uuid="50 00 00 00")
        ),
    )
    monkeypatch.setattr(hnm, "_get_objects", lambda si, t: [pg])

    with pytest.raises(hnm.HostNetworkError) as e:
        _call(op, object(), "esxi04.lab")

    msg = str(e.value)
    assert "notResponding" in msg
    assert "not a report that it has none" in msg
    assert "Reconnect" in msg


def test_write_path_refusal_is_not_confused_with_a_missing_host(monkeypatch):
    """Control: 'host not found' and 'host not reachable' stay different errors."""
    _wire(monkeypatch, [_host("esxi01.lab", vnics=[_vnic("vmk0")])])

    with pytest.raises(hnm.HostNotFoundError):
        hnm.remove_host_vmk(object(), "esxi99.lab", "vmk0")


@pytest.mark.parametrize("state", ["notResponding", "disconnected", "unknown"])
def test_every_non_connected_state_counts_as_unread(monkeypatch, state):
    """`connected` is the only state that means vCenter actually talked to it."""
    _wire(monkeypatch, [_host("esxi04.lab", state=state, config_present=False)])

    out = hnm.list_host_vmks(object())

    assert out["hosts_unreachable"] == 1
    assert out["items"][0]["reachable"] is False
