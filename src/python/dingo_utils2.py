import torch

import os
import sys

import itertools

import threading

from concurrent.futures import ThreadPoolExecutor

from src.python.preprocess2 import *

from blast import *

from tempfile import gettempdir
tmp_dir = gettempdir()
out_dir = "./Data"

from scipy.stats import *

import pickle

NUM_CPU = 8

eps = 10e-6

E = ThreadPoolExecutor(NUM_CPU)

np.random.seed(101)

tmp_dir = gettempdir()

EVAL = 10e6

verbose = False


def save_object(obj, filename):
    with open(filename, 'wb') as output:
        try:
            pickle.dump(obj, output, pickle.HIGHEST_PROTOCOL)
        except RecursionError:
            sys.setrecursionlimit(2 * sys.getrecursionlimit())
            save_object(obj, filename)


def load_object(pth):
    with open(pth, 'rb') as f:
        loaded_dist_mat = pickle.load(f)
        assert len(loaded_dist_mat) > 0
    return loaded_dist_mat


def to_fasta(seq_map, out_file):
    sequences = []
    for unipid, seq in seq_map.items():
        sequences.append(SeqRecord(BioSeq(seq), unipid))
    SeqIO.write(sequences, open(out_file, 'w+'), "fasta")


def load_nature_repr_set(db):
    def to_fasta(seq_map, out_file):
        sequences = []
        for unipid, seq in seq_map.items():
            sequences.append(SeqRecord(BioSeq(seq), unipid))
        SeqIO.write(sequences, open(out_file, 'w+'), "fasta")
    repr_pth, all_pth = '%s/sp.nr.70' % out_dir, '%s/sp.fasta' % out_dir
    fasta_fname = '%s/sp.nr.70' % out_dir
    if not os.path.exists(repr_pth):
        query = {"db": "sp"}
        num_seq = db.uniprot.count(query)
        src_seq = db.uniprot.find(query)
        sp_seqs = UniprotCollectionLoader(src_seq, num_seq).load()
        to_fasta(sp_seqs, all_pth)
        os.system("cdhit/cd-hit -i %s -o %s -c 0.7 -n 5" % (all_pth, repr_pth))
    num_seq = count_lines(fasta_fname, sep=bytes('>', 'utf8'))
    fasta_src = parse_fasta(open(fasta_fname, 'r'), 'fasta')
    seq_map = FastaFileLoader(fasta_src, num_seq).load()
    all_seqs = [Seq(uid, str(seq)) for uid, seq in seq_map.items()]
    return all_seqs


def get_distribution(dataset):
    assert len(dataset) >= 3
    return Distribution(dataset)


class Distribution(object):

    def __init__(self, dataset):
        self.pdf = gaussian_kde([d * 10 for d in dataset])

    def __call__(self, *args, **kwargs):
        assert len(args) == 1
        # return self.pdf.integrate_box_1d(np.min(self.pdf.dataset), args[0])
        return self.pdf(args[0])[0]


class Histogram(object):

    def __init__(self, dataset):
        self.bins = {(a, a + 1): .01 for a in range(10)}
        for p in dataset:
            a = min(int(p * 10), 9)
            self.bins[(a, a + 1)] += 0.9 / len(dataset)

    def __call__(self, *args, **kwargs):
        v = int(args[0] * 10)
        return self.bins[(v, v + 1)]


class NaiveBayes(object):

    def __init__(self, dist_pos, dist_neg):
        self.dist_pos = dist_pos
        self.dist_neg = dist_neg

    def infer(self, val, prior):
        dist_pos = self.dist_pos
        dist_neg = self.dist_neg
        return np.log(prior) + np.log(dist_pos(val)) - np.log(dist_neg(val))


class ThreadSafeDict(dict) :
    def __init__(self, * p_arg, ** n_arg) :
        dict.__init__(self, * p_arg, ** n_arg)
        self._lock = threading.Lock()

    def __enter__(self) :
        self._lock.acquire()
        return self

    def __exit__(self, type, value, traceback) :
        self._lock.release()


class Seq(object):

    def __init__(self, uid, seq, aa20=True):

        if aa20:
            self.seq = seq.replace('U', 'C').replace('O', 'K')\
                .replace('X', np.random.choice(amino_acids))\
                .replace('B', np.random.choice(['N', 'D']))\
                .replace('Z', np.random.choice(['E', 'Q']))
        else:
            self.seq = seq

        self.uid = uid
        self.msa = None
        self.f = dict()

    def __hash__(self):
        return hash(self.uid)

    def __repr__(self):
        return "Seq(%s, %s)" % (self.uid, self.seq)

    def __eq__(self, other):
        if isinstance(other, Seq):
            return self.uid == other.uid
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __len__(self):
        return len(self.seq)


class Node(object):

    def __init__(self, go, sequences, fathers, children):
        self.go = go
        self.sequences = sequences
        self.fathers = fathers
        self.children = children
        self._f_dist_out = None
        self._f_dist_in = None
        self._plus = None
        self._ancestors = None
        self._descendants = None
        self.seq2vec = {}

    def __iter__(self):
        for seq in self.sequences:
            yield seq

    def __repr__(self):
        return "Node(%s, %d)" % (self.go, self.size)

    def __hash__(self):
        return hash(self.go)

    def __eq__(self, other):
        if isinstance(other, Node):
            return self.go == other.go
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def is_leaf(self):
        return len(self.children) == 0

    def is_root(self):
        return len(self.fathers) == 0

    @property
    def cousins(self):
        ret = set()
        for father in self.fathers:
            ret |= set(father.children)
        return ret - {self}

    @property
    def ancestors(self):
        if not self._ancestors:
            self._ancestors = get_ancestors(self)
        return self._ancestors

    @property
    def descendants(self):
        if not self._descendants:
            self._descendants = get_descendants(self)
        return self._descendants

    @property
    def plus(self):
        if not self._plus:
            union = sequences_of(self.children)
            assert len(union) <= self.size
            self._plus = list(self.sequences - union)
        return self._plus

    @property
    def size(self):
        return len(self.sequences)

    @property
    def f_dist_out(self):
        if self._f_dist_out:
            return self._f_dist_out
        else:
            raise(KeyError("f_dist_out not computed for %s" % self))

    @property
    def f_dist_in(self):
        if self._f_dist_in:
            return self._f_dist_in
        else:
            raise(KeyError("f_dist_in not computed for %s" % self))

    def sample(self, m):
        n = min(self.size, m)
        sequences = self.sequences
        s = set(np.random.choice(sequences, n, replace=False))
        assert len(s) == n > 0
        return s


def get_ancestors(node):
        Q = [node]
        visited = {node}
        while Q:
            curr = Q.pop()
            for father in curr.fathers:
                if father in visited or father.is_root():
                    continue
                visited.add(father)
                Q.append(father)
        return visited


def get_descendants(node):
    Q = [node]
    visited = {node}
    while Q:
        curr = Q.pop()
        for child in curr.children:
            if child in visited:
                continue
            visited.add(child)
            Q.append(child)
    return visited


def sequences_of(nodes):
    return reduce(lambda s1, s2: s1 | s2,
                  map(lambda node: node.sequences, nodes), set())


def compute_node_prior(node, graph, grace=0.0):
    node.prior = grace + (1 - grace) * node.size / len(graph.sequences)


class Graph(object):
    def __init__(self, onto, uid2seq, go2ids, grace=0.5):
        self._nodes = nodes = {}
        self.sequences = sequences = set()
        # self.onto = onto

        nodes[onto.root] = self.root = Node(onto.root, set(), [], [])

        for go, ids in go2ids.items():
            seqs = set([Seq(uid, uid2seq[uid]) for uid in ids])
            nodes[go] = Node(go, seqs, [], [])
            sequences |= seqs

        for go, obj in onto._graph._node.items():
            if 'is_a' not in obj:
                assert go == onto.root
                continue
            if go not in go2ids:
                assert go not in nodes
                continue
            if go not in nodes:
                assert go not in go2ids
                continue
            for father in obj['is_a']:
                nodes[go].fathers.append(nodes[father])
                nodes[father].children.append(nodes[go])

        for node in nodes.values():
            if node.is_leaf():
                assert node.size > 0
                continue
            children = node.children
            for child in children:
                assert child.size > 0
                node.sequences |= child.sequences

        for node in nodes.values():
            compute_node_prior(node, self, grace)

    def prune(self, gte):
        to_be_deleted = []
        for go, node in self._nodes.items():
            if node.size >= gte:
                continue
            for father in node.fathers:
                father.children.remove(node)
            for child in node.children:
                child.fathers.remove(node)
            to_be_deleted.append(node)
        for node in to_be_deleted:
            del self._nodes[node.go]
        return to_be_deleted

    def __len__(self):
        return len(self._nodes)

    def __iter__(self):
        for node in self._nodes.values():
            yield node

    def __getitem__(self, go):
        return self._nodes[go]

    def __contains__(self, go):
        return go in self._nodes

    @property
    def leaves(self):
        return [node for node in self if node.is_leaf()]

    @property
    def nodes(self):
        return list(self._nodes.values())

    def sample(self, max_add_to_sample=10):
        def sample_recursive(node, sampled):
            if not node.is_leaf():
                for child in node.children:
                    sampled |= sample_recursive(child, sampled)
            plus = node.plus
            s = min(max_add_to_sample, len(plus))
            if s > 0:
                sampled |= set(np.random.choice(plus, s, replace=False))
            return sampled
        return sample_recursive(self.root, set())


def run_metric_on_triplets(metric, triplets, verbose=True):
    data = []
    n = len(triplets)
    if verbose:
        pbar = tqdm(range(n), desc="triplets processed")
    for i, (seq1, seq2, node) in enumerate(triplets):
        data.append(metric(seq1, seq2, node))
        if verbose:
            pbar.update(1)
    if verbose:
        pbar.close()
    return data


def run_metric_on_pairs(metric, pairs, verbose=True):
    data = []
    n = len(pairs)
    if verbose:
        pbar = tqdm(range(n), desc="triplets processed")
    for i, (seq, node) in enumerate(pairs):
        data.append(metric(seq, node))
        if verbose:
            pbar.update(1)
    if verbose:
        pbar.close()
    return data


def l2_norm(seq, node):
    vec = node.seq2vec[seq]
    return np.linalg.norm(vec)


def cosine_similarity(seq1, seq2, node):
    vec1 = node.seq2vec[seq1]
    vec2 = node.seq2vec[seq2]
    ret = fast_cosine_similarity(vec1, [vec2])
    return ret[0]


def fast_cosine_similarity(vector, vectors, scale_zero_one=False):
    vectors = np.asarray(vectors)
    dotted = vectors.dot(vector)
    matrix_norms = np.linalg.norm(vectors, axis=1)
    vector_norm = np.linalg.norm(vector)
    matrix_vector_norms = np.multiply(matrix_norms, vector_norm)
    neighbors = np.divide(dotted, matrix_vector_norms).ravel()
    if scale_zero_one:
        return (neighbors + 1) / 2
    else:
        return neighbors


if __name__ == "__main__":

    cleanup()

    from pymongo import MongoClient
    client = MongoClient('mongodb://localhost:27017/')
    db = client['prot2vec']

    asp = 'F'   # molecular function
    onto = get_ontology(asp)
    t0 = datetime.datetime(2014, 1, 1, 0, 0)
    t1 = datetime.datetime(2014, 9, 1, 0, 0)
    # t0 = datetime.datetime(2017, 1, 1, 0, 0)
    # t1 = datetime.datetime.utcnow()

    print("Indexing Data...")
    trn_stream, tst_stream = get_training_and_validation_streams(db, t0, t1, asp)
    print("Loading Training Data...")
    uid2seq_trn, _, go2ids_trn = trn_stream.to_dictionaries(propagate=True)
    print("Loading Validation Data...")
    uid2seq_tst, _, go2ids_tst = tst_stream.to_dictionaries(propagate=True)

    print("Building Graph...")
    graph = Graph(onto, uid2seq_trn, go2ids_trn)
    print("Graph contains %d nodes" % len(graph))

    print("Pruning Graph...")
    deleted_nodes = graph.prune(3)
    print("Pruned %d, Graph contains %d" % (len(deleted_nodes), len(graph)))
    save_object(graph, "Data/dingo_%s_graph" % asp)