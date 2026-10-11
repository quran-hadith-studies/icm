import sys, math, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from hadith_phylogeny.text_model import VariantModel, VariantSpec, pruning_log_likelihood, _binary_expm
from hadith_phylogeny.graph_model import Graph
from hadith_phylogeny.sanad_model import SanadModel, pair_hmm_log_likelihood
from hadith_phylogeny.inference import logsumexp

def test_expm_rows_sum_one():
    for t in (0.0, 0.5, 1.0, 7.0):
        T = _binary_expm(0.07, 0.03, t)
        assert np.allclose(T.sum(1), 1.0)
    assert np.allclose(_binary_expm(0.07, 0.03, 0.0), np.eye(2))

def test_multistate_rows():
    m = VariantModel([VariantSpec('v', n_states=4, cls='entity')])
    T = m.transition(0, 1.0)
    assert np.allclose(T.sum(1), 1.0) and T[0,0] > T[0,1]

def test_pruning_matches_bruteforce():
    # 3 leaves under root, enumerate ancestral states by hand
    specs=[VariantSpec('v', 2, 'lexical')]
    model=VariantModel(specs)
    g=Graph('r', {'a':'r','b':'r'}, anchors={'a':[0],'b':[1]})
    X=np.array([[1],[0]])
    ll=pruning_log_likelihood(X,g,model)
    T=model.transition(0,1.0); pi=model.root_distribution(0)
    # each leaf is observed through one further (copying) branch
    brute=sum(pi[s]*sum(T[s,a]*T[a,1] for a in range(2))*sum(T[s,b]*T[b,0] for b in range(2))
              for s in range(2))
    assert abs(ll-math.log(brute))<1e-10

def test_pruning_missing_data_uninformative():
    specs=[VariantSpec('v',2,'lexical')]; model=VariantModel(specs)
    g=Graph('r',{'a':'r'},anchors={'a':[0]})
    assert abs(pruning_log_likelihood(np.array([[-1]]),g,model)) < 1e-12

def test_pair_hmm_is_normalised_over_short_strings():
    # sum over all recorded chains of length <=2 from a 2-identity alphabet < 1
    phi=SanadModel(alphabet=2, p_ins=0.0)
    path=['x']
    tot=sum(math.exp(pair_hmm_log_likelihood(list(c),path,phi))
            for c in [[],['x'],['y']])
    assert tot <= 1.0 + 1e-9 and tot > 0.9

def test_pair_hmm_prefers_exact_match():
    phi=SanadModel()
    p=['a','b','c']
    assert pair_hmm_log_likelihood(p,p,phi) > pair_hmm_log_likelihood(['a','z','c'],p,phi)
    assert pair_hmm_log_likelihood(p,p,phi) > pair_hmm_log_likelihood(['a','c'],p,phi)

def test_logsumexp():
    assert abs(logsumexp([0.0,0.0]) - math.log(2)) < 1e-12

if __name__ == '__main__':
    fails=0
    for name,fn in sorted(list(globals().items())):
        if name.startswith('test_') and callable(fn):
            try:
                fn(); print('PASS', name)
            except Exception as e:
                fails+=1; print('FAIL', name, type(e).__name__, e)
    raise SystemExit(fails)
