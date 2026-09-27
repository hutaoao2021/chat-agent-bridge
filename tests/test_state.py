from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from chat_agent_bridge.state import TaskStore

def test_binding_versions_and_duplicate(tmp_path):
    s = TaskStore(tmp_path/'state.db')
    code = s.register_chat_binding('c1', 'a', 30)
    t = s.begin(code, 'goal', 'key')
    assert s.begin(code, 'goal', 'key').id == t.id
    with pytest.raises(ValueError): s.begin(code, 'other', 'other')
    t = s.checkpoint(t.id, t.version, 'verified')
    with pytest.raises(ValueError): s.checkpoint(t.id, 0, 'stale')
    assert s.get(t.id).checkpoint == 'verified'
    expired = s.register_chat_binding('c2', 'a', -1)
    with pytest.raises(ValueError): s.begin(expired, 'goal', 'k')

def test_concurrent_lease_and_approval(tmp_path):
    s = TaskStore(tmp_path/'state.db')
    t = s.begin(s.register_chat_binding('c1','a',30),'goal','key')
    a = s.request_approval(t.id, 'digest', 'run command')
    assert s.claim_continuation(t.id,'c1','a',0,30) is None
    s.decide_approval(a.id, True)
    assert not s.consume_approval(a.id, 'wrong')
    assert s.consume_approval(a.id, 'digest')
    assert not s.consume_approval(a.id, 'digest')
    with ThreadPoolExecutor(2) as pool:
        leases = list(pool.map(lambda tab: s.claim_continuation(t.id,'c1',tab,0,30), ['a','b']))
    assert sum(x is not None for x in leases) == 1
    lease = next(x for x in leases if x)
    s.ack_continuation(lease.id,'fingerprint')
    s.ack_continuation(lease.id,'fingerprint')
    assert s.get(t.id).turn_count == 1
    with pytest.raises(ValueError): s.ack_continuation(lease.id,'different')

def test_expired_lease_never_reissued(tmp_path):
    s = TaskStore(tmp_path/'state.db')
    t = s.begin(s.register_chat_binding('c','a',30),'goal','k')
    t = s.checkpoint(t.id,t.version,'first checkpoint')
    assert s.claim_continuation(t.id,'c','a',0,-1)
    assert s.claim_continuation(t.id,'c','b',0,30) is None

def test_next_continuation_requires_fresh_checkpoint(tmp_path):
    s=TaskStore(tmp_path/'s.db')
    t=s.begin(s.register_chat_binding('c','a',30),'goal','k')
    t=s.checkpoint(t.id,t.version,'first checkpoint')
    lease=s.claim_continuation(t.id,'c','a',0,30)
    s.ack_continuation(lease.id,'sent')
    assert s.claim_continuation(t.id,'c','a',1,30) is None
    t=s.get(t.id);s.checkpoint(t.id,t.version,'new verified progress')
    assert s.claim_continuation(t.id,'c','a',1,30)

def test_human_reconcile_abandons_uncertain_turn(tmp_path):
    s=TaskStore(tmp_path/'s.db')
    t=s.begin(s.register_chat_binding('c','a',30),'goal','k')
    t=s.checkpoint(t.id,t.version,'first checkpoint')
    lease=s.claim_continuation(t.id,'c','a',0,-1)
    t=s.reconcile_continuation(t.id,'c')
    assert t.turn_count==1
    assert s.claim_continuation(t.id,'c','a',1,30)
